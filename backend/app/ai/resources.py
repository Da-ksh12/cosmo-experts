"""Resource management for AI work on a small host: bounded concurrency and queue, per-run timeouts with cooperative cancellation, a memory guard, a lazy
single-flight model cache, and a device adapter that never claims an unverified GPU.

Design points (tests/test_ai_engine.py):
* AT MOST `max_concurrency` runs execute at once and AT MOST `max_queue` more wait; anything beyond that is refused immediately (ResourceExhausted), never queued without bound.
* A timeout answers the caller at once and sets the run's CancelToken. Python cannot kill a running thread, so a handler that never checks the token keeps its slot
  until it returns: the slot is released only when the work really ends, so the bound on real concurrency is never exceeded.
* Models load lazily, once (single flight), and at most `capacity` stay resident (default 1: RAM is the binding constraint here). A load is refused when free memory is below
  the estimate plus a floor, with an explicit resource error instead of an out-of-memory crash.
* Nothing patient-specific is cached here: the cache holds models, not inputs or outputs.
* GPU: `select_device` returns CPU unless a GPU was explicitly requested or has a verification record produced by scripts/verify_gpu.py on that hardware."""
from __future__ import annotations

import concurrent.futures as cf
import contextvars
import gc
import json
import os
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import DATA_DIR
from .errors import Cancelled, ResourceExhausted, TimeoutExceeded, Unavailable


def _env_int(name: str, default: int, lo: int = 1, hi: int = 1024) -> int:
    try:
        v = int(os.environ.get(name, "") or default)
    except ValueError:
        raise SystemExit(f"{name} must be a whole number") from None
    return max(lo, min(hi, v))


class CancelToken:
    """Cooperative cancellation. Long handlers call `raise_if_cancelled()` between units of work (pages, batches, segments)."""

    def __init__(self, deadline: float | None = None):
        self._event = threading.Event()
        self.deadline = deadline                                  # time.monotonic() value, or None

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled()
        if self.expired:
            raise TimeoutExceeded()


class ResourceManager:
    def __init__(self, max_concurrency: int | None = None, max_queue: int | None = None):
        self.max_concurrency = max_concurrency or _env_int("COSMO_AI_MAX_CONCURRENCY", min(2, os.cpu_count() or 1), 1, 16)
        self.max_queue = max_queue if max_queue is not None else _env_int("COSMO_AI_MAX_QUEUE", 8, 0, 256)
        self._pool = cf.ThreadPoolExecutor(max_workers=self.max_concurrency, thread_name_prefix="ai")
        self._lock = threading.Lock()
        self._pending = 0                                         # submitted and not finished (running + waiting)
        self._sems: dict[str, threading.BoundedSemaphore] = {}
        self._runs: dict[tuple[str, str], int] = defaultdict(int)
        self._seconds: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])

    # ---- execution
    def _sem(self, capability: str, limit: int) -> threading.BoundedSemaphore:
        with self._lock:
            s = self._sems.get(capability)
            if s is None:
                s = self._sems[capability] = threading.BoundedSemaphore(max(1, limit))
            return s

    def submit(self, fn: Callable[[], Any], *, capability: str, concurrency: int = 1) -> cf.Future:
        with self._lock:
            if self._pending >= self.max_concurrency + self.max_queue:
                raise ResourceExhausted()
            self._pending += 1
        sem = self._sem(capability, concurrency)
        ctx = contextvars.copy_context()                          # the database identity of the caller travels with the work

        def wrapped():
            with sem:
                return ctx.run(fn)
        try:
            fut = self._pool.submit(wrapped)
        except Exception:
            with self._lock:
                self._pending -= 1
            raise
        fut.add_done_callback(self._finished)
        return fut

    def _finished(self, _fut) -> None:
        with self._lock:
            self._pending -= 1

    def run(self, fn: Callable[[], Any], *, capability: str, timeout: float, cancel: CancelToken | None = None, concurrency: int = 1) -> Any:
        fut = self.submit(fn, capability=capability, concurrency=concurrency)
        try:
            return fut.result(timeout=timeout)
        except cf.TimeoutError:
            if cancel is not None:
                cancel.cancel()
            fut.cancel()                                          # a run that never started is removed from the queue
            raise TimeoutExceeded() from None

    # ---- accounting (counts and durations only: no inputs, outputs or identities)
    def record(self, capability: str, status: str, seconds: float) -> None:
        with self._lock:
            self._runs[(capability, status)] += 1
            s = self._seconds[capability]
            s[0] += seconds
            s[1] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {"max_concurrency": self.max_concurrency, "max_queue": self.max_queue, "pending": self._pending,
                    "runs": {f"{c}|{s}": n for (c, s), n in sorted(self._runs.items())},
                    "seconds": {c: {"sum": round(v[0], 3), "count": v[1]} for c, v in sorted(self._seconds.items())}}

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------- memory
def available_mb() -> float:
    import psutil
    return psutil.virtual_memory().available / 1e6


def require_memory(needed_mb: float, what: str = "this model") -> None:
    """Refuse (explicitly) instead of risking an out-of-memory crash. The floor keeps the host usable for everything else."""
    floor = _env_int("COSMO_AI_MIN_FREE_MB", 200, 0, 100000)
    have = available_mb()
    if have < needed_mb + floor:
        raise ResourceExhausted(f"There is not enough free memory to load {what} right now. Please try again later.")


class ModelCache:
    """Lazy, single-flight, LRU cache of loaded models. Keys are model identifiers, values are whatever the loader returns."""

    def __init__(self, capacity: int | None = None):
        self.capacity = capacity or _env_int("COSMO_AI_MODEL_CACHE", 1, 1, 8)
        self._items: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self.loads = 0                                            # observable: how many real loads happened

    def get(self, key: str, loader: Callable[[], Any], est_mb: float = 0) -> Any:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
            kl = self._key_locks.setdefault(key, threading.Lock())
        with kl:                                                  # concurrent callers for the same model wait for ONE load
            with self._lock:
                if key in self._items:
                    self._items.move_to_end(key)
                    return self._items[key]
            if est_mb:
                require_memory(est_mb, "the model")
            obj = loader()
            with self._lock:
                self.loads += 1
                self._items[key] = obj
                while len(self._items) > self.capacity:
                    self._items.popitem(last=False)
                keep = set(self._items)
            self._key_locks = {k: v for k, v in self._key_locks.items() if k in keep or v.locked()}
        gc.collect()
        return obj

    def unload(self, key: str) -> bool:
        with self._lock:
            gone = self._items.pop(key, None) is not None
        gc.collect()
        return gone

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
        gc.collect()

    def stats(self) -> dict:
        with self._lock:
            return {"capacity": self.capacity, "resident": list(self._items), "loads": self.loads}


# ---------------------------------------------------------------- device adapter
GPU_RECORD = DATA_DIR / "gpu_verification.json"


@dataclass(frozen=True)
class DeviceInfo:
    kind: str            # cpu | cuda | mps
    name: str
    verified: bool       # a verification record from a real run on this hardware exists
    note: str


def gpu_record(path: Path | None = None) -> dict | None:
    p = path or GPU_RECORD
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("passed") is True and rec.get("device_kind") in ("cuda", "mps") else None


def _torch_accelerator() -> tuple[str, str] | None:
    try:
        import torch
    except ImportError:
        return None
    if torch.cuda.is_available():
        return "cuda", torch.cuda.get_device_name(0)
    mps = getattr(torch.backends, "mps", None)
    if mps and mps.is_available():
        return "mps", "Apple MPS"
    return None


def select_device(prefer: str | None = None, record_path: Path | None = None) -> DeviceInfo:
    """`auto` (default) uses a GPU only if this exact device kind has a passing verification record; `cpu` always works; `cuda`/`mps` are explicit requests (allowed for
    training and research, reported as unverified when no record exists). A requested accelerator that is not present raises Unavailable: no silent switch to CPU."""
    want = (prefer or os.environ.get("COSMO_AI_DEVICE") or "auto").lower()
    if want not in ("auto", "cpu", "cuda", "mps"):
        raise Unavailable("Unknown compute device setting.")
    if want == "cpu":
        return DeviceInfo("cpu", "CPU", True, "CPU is the reference device for every evaluation in this repository.")
    acc = _torch_accelerator()
    rec = gpu_record(record_path)
    if want == "auto":
        if acc and rec and rec.get("device_kind") == acc[0]:
            return DeviceInfo(acc[0], acc[1], True, f"Verified on {rec.get('device_name')} ({rec.get('verified_at')}).")
        return DeviceInfo("cpu", "CPU", True, "No verified GPU: using the CPU." + (f" A {acc[0]} device exists but has no verification record (scripts/verify_gpu.py)." if acc else ""))
    if not acc or acc[0] != want:
        raise Unavailable(f"The requested {want} device is not available on this machine.")
    verified = bool(rec and rec.get("device_kind") == want)
    return DeviceInfo(acc[0], acc[1], verified, "Verified." if verified else "Unverified: no passing verification record exists for this hardware; results are not claimed to match the CPU reference.")


_default_rm: ResourceManager | None = None
_default_cache: ModelCache | None = None
_singleton_lock = threading.Lock()


def resource_manager() -> ResourceManager:
    global _default_rm
    with _singleton_lock:
        if _default_rm is None:
            _default_rm = ResourceManager()
        return _default_rm


def model_cache() -> ModelCache:
    global _default_cache
    with _singleton_lock:
        if _default_cache is None:
            _default_cache = ModelCache()
        return _default_cache

"""Resumable image cache and feature store for the Phase 5 probes, plus the resource auto-tuner.

Why this exists: a frozen backbone is the one expensive step (about 0.2 s per image on this CPU), and the datasets are large. So
* images are decoded ONCE into a uint8 224 x 224 memory-mapped cache (the same `trainer._square_resize` preprocessing serving uses), filled in chunks so an interrupted run resumes;
* backbone features are extracted ONCE per dataset version into a memory-mapped float32 store, in chunks recorded in a small progress file, so any number of heads, selections and
  re-evaluations afterwards cost seconds;
* the thread count and batch size are chosen by a short measurement on the real machine, bounded by the memory that is actually free; a run that cannot continue (memory exhausted, time
  limit reached, stop requested) saves its position and raises `Paused` instead of failing.

Nothing here is specific to the CPU: the device comes from `ai.resources.select_device` (a GPU only when requested or verified), tensors and models follow it, and mixed precision (fp16
autocast) is used only on a CUDA device. On this machine everything runs in fp32 on the CPU."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ..ai.resources import available_mb
from ..config import DATA_DIR, MODEL_DIR
from . import trainer as T

FEATURE_DIR = DATA_DIR / "features"
SIZE = 224
MIN_FREE_MB = 120                 # below this the run pauses rather than push the machine into swapping hard enough to stall
PAUSE_AFTER_S = 90                # ... if it stays below for this long
POLL_S = 5
CACHE_CHUNK = 192               # rows decoded between flushes (and between checks for a stop request)


class Paused(Exception):
    """The run stopped at a safe point; calling it again with the same inputs continues from there."""

    def __init__(self, reason: str, done: int, total: int):
        super().__init__(f"{reason} ({done}/{total})")
        self.reason, self.done, self.total = reason, done, total


# ---------------------------------------------------------------- backbones
BACKBONES = {
    "dinov2_s14": {"label": "DINOv2 ViT-S/14 (self-supervised, Apache-2.0 weights)", "weights": MODEL_DIR / "pretrained" / "dinov2_small" / "model.safetensors",
                   "provenance": MODEL_DIR / "pretrained" / "dinov2_small" / "provenance.json", "dim": 768, "parts": {"cls": (0, 384), "cls+mean": (0, 768)}, "commercial_ok": True},
    "resnet18_in1k": {"label": "ResNet18 (ImageNet-1k, torchvision weights)", "weights": MODEL_DIR / "pretrained" / "resnet18_tv_in1k.pth",
                      "provenance": MODEL_DIR / "pretrained" / "resnet18_tv_in1k.provenance.json", "dim": 512, "parts": {"gap": (0, 512)}, "commercial_ok": False},
}


_ID_CACHE: dict[tuple, str] = {}


def backbone_id(name: str) -> str:
    """name + the hash of the weight file: features from a different file are a different store. (Hashed once per file version, not on every call.)"""
    w = Path(BACKBONES[name]["weights"])
    key = (name, str(w), w.stat().st_mtime_ns, w.stat().st_size)
    if key not in _ID_CACHE:
        _ID_CACHE[key] = f"{name}-{T.sha256_file(w)[:12]}"
    return _ID_CACHE[key]


def load_backbone(name: str, device: str = "cpu"):
    import torch
    spec = BACKBONES[name]
    if name == "dinov2_s14":
        from . import probe
        m = probe.load_backbone(str(spec["weights"]), size=SIZE)
    else:
        import torch.nn as nn
        import torchvision.models as tvm
        m = tvm.resnet18(weights=None)
        m.load_state_dict(torch.load(spec["weights"], map_location="cpu", weights_only=True))
        m.fc = nn.Identity()
    return m.eval().to(device)


def forward(name: str, model, x):
    """x: float tensor [B,3,224,224] already normalised. Returns [B, dim] float32 numpy. DINOv2: [CLS token, mean of the patch tokens] (768); ResNet18: global-average-pooled (512)."""
    import torch
    with torch.inference_mode():
        if name == "dinov2_s14":
            tok = model.forward_features(x)
            out = torch.cat([tok[:, 0], tok[:, 1:].mean(1)], dim=1)
        else:
            out = model(x)
    return out.float().cpu().numpy()


def extract_batch(name: str, model, batch_u8: np.ndarray, device: str = "cpu") -> np.ndarray:
    """Features for a uint8 [B,224,224,3] batch. Mixed precision only on CUDA."""
    import torch
    x = T._to_tensor(np.asarray(batch_u8)).to(device)
    if device == "cuda":
        with torch.autocast("cuda", dtype=torch.float16):
            return forward(name, model, x)
    return forward(name, model, x)


# ---------------------------------------------------------------- image cache (resumable)
def cache_key(entries: list[dict], size: int = SIZE) -> str:
    return hashlib.sha256((str(size) + "".join(e["sha256"] for e in entries)).encode()).hexdigest()[:20]


def image_cache(root, entries: list[dict], size: int = SIZE, workers: int = 3, progress=None, should_stop=None) -> np.ndarray:
    """uint8 [N,size,size,3], decoded once. Same path scheme as `trainer.build_cache` (a finished cache from either is reused); additionally resumable: rows are written in
    chunks and the number completed is kept beside the file."""
    from PIL import Image
    T.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = cache_key(entries, size)
    path = T.CACHE_DIR / f"{key}.npy"
    if path.exists():
        return np.load(path, mmap_mode="r")
    tmp, prog = Path(str(path) + ".tmp.npy"), Path(str(path) + ".progress")
    done = 0
    if tmp.exists() and prog.exists():
        try:
            done = int(json.loads(prog.read_text())["rows"])
        except (ValueError, KeyError):
            done = 0
    arr = np.lib.format.open_memmap(str(tmp), mode="r+" if done else "w+", dtype=np.uint8, shape=(len(entries), size, size, 3))

    def load(i):
        with Image.open(Path(root) / entries[i]["file"]) as im:
            return i, np.asarray(T._square_resize(im, size))
    t0, chunk = time.time(), CACHE_CHUNK
    try:
        with ThreadPoolExecutor(workers) as ex:
            for s in range(done, len(entries), chunk):
                for i, a in ex.map(load, range(s, min(s + chunk, len(entries)))):
                    arr[i] = a
                arr.flush()
                prog.write_text(json.dumps({"rows": min(s + chunk, len(entries))}))
                if progress:
                    progress(f"image cache {min(s + chunk, len(entries))}/{len(entries)} ({time.time() - t0:.0f}s)")
                if should_stop and min(s + chunk, len(entries)) < len(entries) and should_stop():          # nothing to pause once the last row is written
                    raise Paused("stop requested while decoding images", min(s + chunk, len(entries)), len(entries))
        arr.flush()
    finally:
        mm = getattr(arr, "_mmap", None)                 # release the mapping however we leave: on Windows an open map blocks the final rename (and a resumed call's own map)
        del arr
        if mm is not None:
            mm.close()
    tmp.replace(path)
    prog.unlink(missing_ok=True)
    return np.load(path, mmap_mode="r")


# ---------------------------------------------------------------- feature store (resumable)
def store_dir(name: str, entries: list[dict], size: int = SIZE) -> Path:
    key = hashlib.sha256((backbone_id(name) + str(size) + "".join(e["sha256"] for e in entries)).encode()).hexdigest()[:20]
    return FEATURE_DIR / f"{name}_{size}" / key


def extract_features(name: str, X: np.ndarray, entries: list[dict], device: str = "cpu", batch: int = 16, threads: int | None = None, progress=None, should_stop=None,
                     max_seconds: float | None = None, size: int = SIZE) -> np.ndarray:
    """Features for every image of the cache X (in order), resumable. Returns a float32 memmap [N, dim]."""
    import torch
    d = store_dir(name, entries, size)
    d.mkdir(parents=True, exist_ok=True)
    fpath, prog = d / "features.npy", d / "progress.json"
    dim = BACKBONES[name]["dim"]
    done = 0
    if fpath.exists() and prog.exists():
        p = json.loads(prog.read_text())
        done = int(p["rows"]) if p.get("backbone") == backbone_id(name) and p.get("n") == len(entries) else 0
    if done >= len(entries):
        return np.load(fpath, mmap_mode="r")
    F = np.lib.format.open_memmap(str(fpath), mode="r+" if done else "w+", dtype=np.float32, shape=(len(entries), dim))
    if threads:
        torch.set_num_threads(threads)
    model = load_backbone(name, device)
    t0, last_flush = time.time(), 0
    for s in range(done, len(entries), batch):
        low_since = None
        while available_mb() < MIN_FREE_MB:                         # memory exhausted by other programs: wait a little, then pause safely
            low_since = low_since or time.time()
            if time.time() - low_since > PAUSE_AFTER_S:
                F.flush()
                prog.write_text(json.dumps({"rows": s, "n": len(entries), "backbone": backbone_id(name), "saved_at": time.time()}))
                raise Paused(f"free memory stayed below {MIN_FREE_MB} MB", s, len(entries))
            time.sleep(POLL_S)
        e = min(s + batch, len(entries))
        F[s:e] = extract_batch(name, model, X[s:e], device)
        if e - last_flush >= 256 or e == len(entries):
            F.flush()
            prog.write_text(json.dumps({"rows": e, "n": len(entries), "backbone": backbone_id(name), "saved_at": time.time()}))
            last_flush = e
            if progress:
                el = time.time() - t0
                progress(f"features {name} {e}/{len(entries)} ({(e - done) / max(el, 1e-6):.2f} img/s, {el:.0f}s)")
        if should_stop and should_stop():
            F.flush()
            prog.write_text(json.dumps({"rows": e, "n": len(entries), "backbone": backbone_id(name), "saved_at": time.time()}))
            raise Paused("stop requested", e, len(entries))
        if max_seconds and time.time() - t0 > max_seconds and e < len(entries):
            F.flush()
            prog.write_text(json.dumps({"rows": e, "n": len(entries), "backbone": backbone_id(name), "saved_at": time.time()}))
            raise Paused(f"time limit of {max_seconds:.0f} s reached", e, len(entries))
    del F
    return np.load(fpath, mmap_mode="r")


# ---------------------------------------------------------------- resource auto-tuner
def autotune(name: str, X: np.ndarray, device: str = "cpu", sample: int = 24) -> dict:
    """Pick threads and batch size by measuring a few batches on this machine, bounded by the memory that is free right now. One measurement per run: the result is recorded in the plan."""
    import torch
    free = available_mb()
    cpu = os.cpu_count() or 2
    model = load_backbone(name, device)
    idx = np.linspace(0, len(X) - 1, min(sample, len(X))).astype(int)
    xb = np.asarray(X[idx])
    cands = [(t, b) for t in sorted({min(2, cpu), min(3, cpu), min(4, cpu)}) for b in (8, 16)] if device == "cpu" else [(0, 32)]
    if free < 600:                                               # a tight machine: smaller batches only
        cands = [c for c in cands if c[1] == 8]
    best, table = None, []
    base_threads = torch.get_num_threads()
    for t, b in cands:
        if t:
            torch.set_num_threads(t)
        extract_batch(name, model, xb[:b], device)                # warm-up
        t0 = time.time()
        n = 0
        for s in range(0, len(xb) - b + 1, b):
            extract_batch(name, model, xb[s:s + b], device)
            n += b
        rate = n / max(time.time() - t0, 1e-6)
        table.append({"threads": t, "batch": b, "images_per_s": round(rate, 2)})
        if best is None or rate > best["images_per_s"]:
            best = {"threads": t, "batch": b, "images_per_s": round(rate, 2)}
    torch.set_num_threads(base_threads)
    del model
    return {"device": device, "free_ram_mb_at_start": round(free), "cpu_logical": cpu, "chosen": best, "measured": table, "image_size": SIZE,
            "precision": "fp16 autocast (CUDA)" if device == "cuda" else "fp32 (mixed precision is only used on CUDA)"}

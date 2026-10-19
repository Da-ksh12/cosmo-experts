"""Runs ONE tool call under fixed rules, in this order, and stops at the first that fails:
  1. the tool exists in the closed list (a name is only ever looked up in that dictionary);
  2. the signed-in person's role may call it;
  3. patients have accepted the current privacy notice (the same check the direct APIs make);
  4. the arguments validate against the tool's strict schema (unknown fields are refused; the error never repeats what was sent);
  5. a tool that changes state is refused unless the caller is the confirmation path and holds a confirmed action;
  6. the call runs in a bounded worker with a timeout (an overrun is stopped and reported, never retried);
  7. the result is a dictionary of bounded size.
Every outcome is a ToolResult. A failure carries a code and a safe message; exception text and stack traces are logged without content and never returned."""
from __future__ import annotations

import concurrent.futures as cf
import contextvars
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass

from pydantic import ValidationError

from .. import consent
from ..ai.errors import ResourceExhausted, TimeoutExceeded
from ..redact import safe_exc
from .tools import MAX_RESULT_BYTES, TOOLS, ToolContext, ToolError

log = logging.getLogger("cosmo.agent")


class ToolPool:
    """The agent's own bounded worker pool. It is separate from the engine's pool on purpose: a chat turn may itself be running inside an engine worker, and tools waiting for a worker
    of that same small pool could never be served. Work is bounded (running + queued), every call has a timeout, and the caller's database identity travels with the work."""

    def __init__(self, workers: int | None = None, queue: int = 8):
        try:
            n = workers or int(os.environ.get("COSMO_AGENT_TOOL_WORKERS", "") or 2)
        except ValueError:
            n = 2
        self.workers, self.queue = max(1, min(n, 16)), queue
        self._pool = cf.ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="agent-tool")
        self._lock = threading.Lock()
        self._pending = 0

    def run(self, fn, timeout: float, cancel=None):
        with self._lock:
            if self._pending >= self.workers + self.queue:
                raise ResourceExhausted()
            self._pending += 1
        ctx = contextvars.copy_context()
        try:
            fut = self._pool.submit(ctx.run, fn)
        except Exception:
            with self._lock:
                self._pending -= 1
            raise
        fut.add_done_callback(self._done)
        try:
            return fut.result(timeout=timeout)
        except cf.TimeoutError:
            if cancel is not None:
                cancel.cancel()
            fut.cancel()
            raise TimeoutExceeded() from None

    def _done(self, _f) -> None:
        with self._lock:
            self._pending -= 1


_pool: ToolPool | None = None


def tool_pool() -> ToolPool:
    global _pool
    if _pool is None:
        _pool = ToolPool()
    return _pool


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: dict | None = None
    code: str = ""
    message: str = ""
    ms: int = 0
    args_sha256: str = ""
    effect: str = "read"

    def step(self) -> dict:
        """What a trace records about this call: the tool, whether it changed state, the outcome, the time, a hash of the arguments and how much came back. Never the arguments or the data."""
        size = {k: len(v) for k, v in (self.data or {}).items() if isinstance(v, (list, dict))}
        return {"tool": self.tool, "effect": self.effect, "status": "ok" if self.ok else self.code, "ms": self.ms, "args_sha256": self.args_sha256[:16], "returned": size}


def args_hash(args) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def call(name: str, raw_args, ctx: ToolContext, *, allow_write: bool = False) -> ToolResult:
    t0 = time.monotonic()
    tool = TOOLS.get(name) if isinstance(name, str) else None
    h = args_hash(raw_args if isinstance(raw_args, (dict, list)) else str(type(raw_args)))

    def fail(code: str, message: str = "") -> ToolResult:
        return ToolResult(name if isinstance(name, str) else "?", False, None, code, message or ToolError(code).message, round((time.monotonic() - t0) * 1000), h, tool.effect if tool else "read")

    if tool is None:
        return fail("unknown_tool")
    if (ctx.user or {}).get("role") not in tool.roles or not (ctx.user or {}).get("id"):
        return fail("forbidden")
    if ctx.user["role"] == "patient" and consent.enforced() and not consent.accepted(ctx.user["id"]):
        return fail("consent_required")
    try:
        args = tool.args.model_validate(raw_args if raw_args is not None else {})
    except ValidationError:
        return fail("invalid_arguments")
    if tool.effect == "write" and not (allow_write and ctx.confirmed_action):
        return fail("confirmation_required")
    try:
        data = tool_pool().run(lambda: tool.handler(ctx, args), tool.timeout)
    except ToolError as e:
        return fail(e.code, e.message)
    except TimeoutExceeded:
        return fail("timeout")
    except ResourceExhausted:
        return fail("unavailable", "The assistant is busy. Please try again in a moment.")
    except Exception as e:                                             # never return exception text; never retry
        log.error("tool %s failed (request %s): %s", name, ctx.request_id, safe_exc(e))
        return fail("error")
    if not isinstance(data, dict):
        log.error("tool %s returned a non-dictionary (request %s)", name, ctx.request_id)
        return fail("error")
    if len(json.dumps(data, default=str)) > MAX_RESULT_BYTES:
        return fail("too_large")
    return ToolResult(name, True, data, "", "", round((time.monotonic() - t0) * 1000), h, tool.effect)

"""Request-size limits, bounded concurrency and request timeouts, as a pure ASGI middleware (outermost layer).

* Body size: JSON/form requests are limited to COSMO_MAX_JSON_KB (default 1024 KiB); the upload routes to COSMO_MAX_UPLOAD_MB plus 1 MiB of multipart overhead. A declared
  Content-Length over the limit is refused before the body is read; a body that is chunked or lies about its length is cut off as soon as it passes the limit (413).
* Concurrency: at most COSMO_MAX_CONCURRENT_REQUESTS requests (default 64) are processed at once; further ones are refused at once with 503 + Retry-After instead of
  queueing without bound. Health, readiness and the job event stream do not count against the cap.
* Timeouts: a request that runs longer than COSMO_REQUEST_TIMEOUT_S (default 120) is answered with 504. Work in a worker thread cannot be interrupted, so the thread may finish
  in the background; long work belongs on the asynchronous job API (/api/v1/...), which these limits do not constrain. The job event stream has its own 10-minute bound.
Refusals are JSON with the request id and never contain request content."""
from __future__ import annotations

import asyncio
import json
import os
import re

from .config import MAX_UPLOAD_BYTES

UPLOAD_ROUTES = re.compile(r"^POST /api/(v1/)?(images|documents|speech/transcribe|images/check|ai/images/analyze|ai/documents/extract|ai/speech/transcribe)(/|$)")
EXEMPT = re.compile(r"^(GET /api/(v1/health/(live|ready)|health)|GET /api/v1/jobs/[^/]+/events|GET /metrics)$")
NO_TIMEOUT = re.compile(r"^GET /api/v1/jobs/[^/]+/events$")


class TooLarge(Exception):
    pass


def _int_env(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "") or default)
    except ValueError:
        raise SystemExit(f"{name} must be a whole number") from None
    return max(v, 1)


async def _respond(send, status: int, detail: str, request_id: str, extra: list | None = None) -> None:
    body = json.dumps({"detail": detail, "request_id": request_id}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"x-request-id", request_id.encode()), (b"cache-control", b"no-store")]
    await send({"type": "http.response.start", "status": status, "headers": headers + (extra or [])})
    await send({"type": "http.response.body", "body": body})


class RequestLimits:
    def __init__(self, app):
        self.app = app
        self.inflight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        max_json = _int_env("COSMO_MAX_JSON_KB", 1024) * 1024
        max_conc = _int_env("COSMO_MAX_CONCURRENT_REQUESTS", 64)
        timeout = _int_env("COSMO_REQUEST_TIMEOUT_S", 120)
        sig = f"{scope['method']} {scope['path']}"
        headers = {k.lower(): v for k, v in scope["headers"]}
        rid = re.sub(r"[^A-Za-z0-9._\-]", "", headers.get(b"x-request-id", b"").decode("latin-1"))[:64] or "-"
        limit = MAX_UPLOAD_BYTES + 1024 * 1024 if UPLOAD_ROUTES.match(sig) else max_json
        try:
            declared = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            return await _respond(send, 400, "Invalid request.", rid)
        if declared > limit:
            return await _respond(send, 413, "This request is too large.", rid)
        exempt = bool(EXEMPT.match(sig))
        if not exempt and self.inflight >= max_conc:
            return await _respond(send, 503, "The service is busy. Please try again in a moment.", rid, [(b"retry-after", b"2")])
        seen, exceeded, replaced = 0, False, False

        async def counted():
            nonlocal seen, exceeded
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    exceeded = True
                    raise TooLarge()
            return msg

        started = False

        async def tracking_send(msg):
            """FastAPI turns an exception raised while reading the body into its own 400/500: once the limit was exceeded, whatever the application answers is replaced by the 413."""
            nonlocal started, replaced
            if replaced:
                return
            if exceeded and not started and msg["type"] == "http.response.start":
                started = replaced = True
                await _respond(send, 413, "This request is too large.", rid)
                return
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        if not exempt:
            self.inflight += 1
        try:
            if NO_TIMEOUT.match(sig):
                await self.app(scope, counted, tracking_send)
            else:
                await asyncio.wait_for(self.app(scope, counted, tracking_send), timeout)
        except TooLarge:
            if not started:
                await _respond(send, 413, "This request is too large.", rid)
        except asyncio.TimeoutError:
            if not started:
                await _respond(send, 504, "The request took too long. Please try again.", rid)
        finally:
            if not exempt:
                self.inflight -= 1

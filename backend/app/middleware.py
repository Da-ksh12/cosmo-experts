"""Request IDs, security headers, request metrics and rate limiting.

Rate limits use `ratelimit.get()`: in-process by default, shared via Redis when COSMO_REDIS_URL is set (several instances behind one load balancer).
They are disabled with COSMO_RATE_LIMIT=off (tests; refused in production by prod_checks).
Client address: the socket peer unless uvicorn is started with --proxy-headers --forwarded-allow-ips=<proxy address>; behind a reverse proxy WITHOUT that,
every client shares the proxy's address and the per-IP limits become global (see docs/DEPLOYMENT.md)."""
from __future__ import annotations

import os
import re
import time
import uuid

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import logging_setup, metrics, ratelimit
from .dbcore import context as db_context

LIMITS = [  # (name, regex on "METHOD path", max requests, window seconds, keyed by)
    ("login", re.compile(r"^POST /api/auth/login$"), 8, 60, "ip"),
    ("submit", re.compile(r"^POST /api/(v1/(images|documents|speech|assistant|photo-analyses)|images|documents|speech|conversations)"), 40, 60, "token"),
    ("auth_flows", re.compile(r"^POST /api/auth/(register|password-reset/(request|confirm)|refresh)$"), 20, 60, "ip"),
    ("general", re.compile(r".*"), 600, 60, "ip"),
]


def reset():
    ratelimit.get().reset()


def _route(request) -> str:
    r = request.scope.get("route")
    return getattr(r, "path", None) or "unmatched"       # route template: bounded label cardinality


class Guard(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        started = time.perf_counter()
        rid = re.sub(r"[^A-Za-z0-9._\-]", "", request.headers.get("x-request-id", ""))[:64] or uuid.uuid4().hex     # client-supplied: only safe characters survive
        request.state.request_id = rid
        logging_setup.request_id_var.set(rid)                 # every log line of this request carries the id
        db_context.set_user("", "none")                        # no identity until the authentication dependency establishes one
        if os.environ.get("COSMO_RATE_LIMIT") != "off" and request.url.path.startswith("/api/"):
            ip = request.client.host if request.client else "?"
            sig = f"{request.method} {request.url.path}"
            token = request.headers.get("authorization", "")[-24:] or ip
            for name, rx, limit, window, by in LIMITS:
                if rx.match(sig):
                    ok, retry = await run_in_threadpool(ratelimit.get().hit, f"{name}:{ip if by == 'ip' else token}", limit, window)
                    if not ok:
                        metrics.observe(request.method, "rate_limited", 429, time.perf_counter() - started)
                        return JSONResponse({"detail": "Too many requests. Please wait a moment and try again."}, status_code=429,
                                            headers={"Retry-After": str(retry), "X-Request-ID": rid})
                    if name != "general":
                        break
        resp = await call_next(request)
        metrics.observe(request.method, _route(request), resp.status_code, time.perf_counter() - started)
        resp.headers["X-Request-ID"] = rid
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")            # an API response is never a page
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        if os.environ.get("COSMO_MODE") == "production" or request.url.scheme == "https":
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/") and "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp

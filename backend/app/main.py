import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from .dbcore import context
from . import clinical_content, db, limits, logging_setup, malware, metrics, prod_checks, security
from .config import CORS_ORIGINS
from .datasets import registry
from .gateway import api as gateway_api, jobs as gateway_jobs
from .middleware import Guard
from .routers import ai as ai_router, auth as auth_router, care, files as files_router, patient, staff

WARM = {"embeddings": "pending"}


def _warm_up():
    """Load the embedding model and run one encode so the first patient question is not the one that pays the load cost."""
    import time
    from .assistant.retrieval import get_retriever
    t = time.time()
    try:
        r = get_retriever()
        r.search("warm up", [{"text": "Ferritin 8 ng/mL 15 - 150 L"}])
        WARM["embeddings"] = f"warm ({r.mode}) in {time.time() - t:.1f}s"
    except Exception as e:  # never block the app because an optional model failed to load
        WARM["embeddings"] = f"failed: {type(e).__name__}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if "PYTEST_CURRENT_TEST" not in os.environ and os.environ.get("COSMO_STRUCTURED_LOGS", "1") != "0":
        logging_setup.configure()                               # redacting JSON logs with correlation ids (left alone under pytest)
    prod_checks.enforce()
    with db.context.as_system():                                 # start-up work (schema, seeding) is not performed for any user
        _startup()
    threading.Thread(target=context.run_as_system(_warm_up), daemon=True, name="warm-up").start()   # do not block startup; /ready reports when it is done
    yield


def _startup():
    db.init()
    gateway_jobs.ensure_schema()
    from .training import orchestrator
    orchestrator.mark_interrupted()                  # a training run still marked running belongs to a process that no longer exists
    from . import supabase_auth
    if not supabase_auth.configured():     # demo accounts exist only in local mode
        security.seed_users()
    registry.seed()
    clinical_content.seed_kb()          # shipped drafts are inserted as DRAFT only; the assistant ignores them until a doctor approves


app = FastAPI(title="Cosmo Experts AI", version="1.0.0", lifespan=lifespan)
# CORS is explicit: listed origins only (never "*", which prod_checks refuses), the methods and headers the web client uses, no credentials. The web client calls the API
# same-origin through its proxy, so the refresh cookie is not a cross-origin credential.
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Requested-With", "X-Request-ID"], expose_headers=["X-Request-ID", "Retry-After", "Location"],
                   allow_credentials=False, max_age=600)
@app.exception_handler(malware.UploadRefused)
async def _upload_refused(request: Request, exc: malware.UploadRefused):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


app.include_router(auth_router.router)
app.include_router(patient.router)
app.include_router(staff.router)
app.include_router(care.router)
app.include_router(files_router.router)
app.include_router(ai_router.router)
app.include_router(gateway_api.router)
app.add_middleware(Guard)
app.add_middleware(limits.RequestLimits)                     # outermost: size, concurrency and time limits apply before anything else runs


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    """Field names and messages only: FastAPI's default echoes the submitted INPUT values, which would put passwords and patient text into responses and logs."""
    errs = [{"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", ""))[:200]} for e in exc.errors()][:20]
    return JSONResponse({"detail": errs, "request_id": getattr(request.state, "request_id", "")}, status_code=422)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """No stack trace, path, SQL or exception text reaches the client; the request id lets support find the redacted log entry."""
    import logging
    from .redact import safe_exc
    rid = getattr(request.state, "request_id", "")
    logging.getLogger("cosmo.error").error("unhandled %s on %s (request %s)", safe_exc(exc), _route_of(request), rid)
    return JSONResponse({"detail": "Something went wrong on our side. Please try again.", "request_id": rid}, status_code=500, headers={"X-Request-ID": rid})


def _route_of(request: Request) -> str:
    r = request.scope.get("route")
    return f"{request.method} {getattr(r, 'path', None) or 'unmatched'}"          # the route template, never the raw path (which may carry a signed-link token)



@app.get("/metrics", include_in_schema=False)
def prometheus(request: Request):
    """Prometheus scrape target. Absent (404) unless COSMO_METRICS_TOKEN is configured; 401 without the bearer token. Not under /api: the web
    front end does not proxy it, so scrape the backend directly on the private network."""
    import os
    if not os.environ.get("COSMO_METRICS_TOKEN"):
        return PlainTextResponse("Not found", status_code=404)
    if not metrics.token_ok(request.headers.get("authorization", "")):
        return PlainTextResponse("Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"})
    with db.context.as_system():                                 # operational gauges read counts across tables: a server action
        body = metrics.render()
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return {"ok": True, "warm_up": WARM}

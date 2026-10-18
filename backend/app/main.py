import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from . import clinical_content, db, metrics, prod_checks, security
from .config import CORS_ORIGINS
from .datasets import registry
from .gateway import api as gateway_api, jobs as gateway_jobs
from .middleware import Guard
from .routers import care, patient, staff

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
    prod_checks.enforce()
    db.init()
    gateway_jobs.ensure_schema()
    from . import supabase_auth
    if not supabase_auth.configured():     # demo accounts exist only in local mode
        security.seed_users()
    registry.seed()
    clinical_content.seed_kb()          # shipped drafts are inserted as DRAFT only; the assistant ignores them until a doctor approves
    threading.Thread(target=_warm_up, daemon=True, name="warm-up").start()   # do not block startup; /ready reports when it is done
    yield


app = FastAPI(title="Cosmo Experts AI", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)
app.include_router(patient.router)
app.include_router(staff.router)
app.include_router(care.router)
app.include_router(gateway_api.router)
app.add_middleware(Guard)


@app.get("/metrics", include_in_schema=False)
def prometheus(request: Request):
    """Prometheus scrape target. Absent (404) unless COSMO_METRICS_TOKEN is configured; 401 without the bearer token. Not under /api: the web
    front end does not proxy it, so scrape the backend directly on the private network."""
    import os
    if not os.environ.get("COSMO_METRICS_TOKEN"):
        return PlainTextResponse("Not found", status_code=404)
    if not metrics.token_ok(request.headers.get("authorization", "")):
        return PlainTextResponse("Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return {"ok": True, "warm_up": WARM}

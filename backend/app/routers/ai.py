"""Transport for the unified AI engine: capability discovery and health. Capability-specific routes (vision, documents, agent) live beside this one and call the engine;
this module holds no AI logic."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..ai import get_engine
from ..security import current_user, require

router = APIRouter(prefix="/api/v1/ai")


@router.get("/capabilities")
def capabilities(user=Depends(current_user)):
    """Every capability the caller may know about, with its honest state. Patients see whether each one can be used and its limits; staff also see why a capability is
    unavailable, which model backs it and its approval state."""
    return JSONResponse([c.model_dump(mode="json") for c in get_engine().capabilities(user)])


@router.get("/health")
def health(user=Depends(require("admin"))):
    """Readiness without secrets: availability counts, queue and cache state, device (with whether it is verified)."""
    return get_engine().health()


# ---------------------------------------------------------------- images
from fastapi import File, Form, HTTPException, UploadFile  # noqa: E402

from .. import consent, db  # noqa: E402
from ..ai import vision  # noqa: E402
from ..ai.errors import Unsupported  # noqa: E402
from ..config import MAX_UPLOAD_BYTES  # noqa: E402


def _read(f: UploadFile) -> bytes:
    data = f.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"This file is too large. The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return data


@router.get("/images/categories")
def image_categories(user=Depends(current_user)):
    """Photo categories and, for each, the honest availability of every analysis that applies to it."""
    return vision.categories(user)


@router.post("/images/analyze", response_model=vision.ImageAnalysis)
def analyze_image(request: Request, category: str = Form(...), task: str | None = Form(None), model_id: str | None = Form(None), research: bool = Form(False),
                  file: UploadFile = File(...), user=Depends(consent.required)):
    """Quality check, routing and (only where permitted) analysis of one photo; nothing is stored. Patients and clinicians can only reach approved models (none exist today);
    research models run for an administrator who sets `research=true` and names both the task and the model."""
    if research and user["role"] != "admin":
        raise HTTPException(403, "Research use is for administrators.")
    data = _read(file)
    try:
        res = vision.analyze(data, category, user=user, task=task, model_id=model_id, allow_research=research, request_id=getattr(request.state, "request_id", ""))
    except Unsupported as e:
        raise HTTPException(422, e.public_message) from None
    db.audit(user["id"], "ai.image_analyze", category, {"route": res.route, "tasks": [o.task for o in res.outcomes], "research": bool(research), "request_id": res.request_id})
    return res

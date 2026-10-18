"""Transport for the unified AI engine: capability discovery and health. Capability-specific routes (vision, documents, agent) live beside this one and call the engine;
this module holds no AI logic."""
from fastapi import APIRouter, Depends
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

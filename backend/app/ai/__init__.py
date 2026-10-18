"""Unified AI engine (Phase 3). See docs/PHASE_3_AI_ENGINE.md.

    from app.ai import get_engine
    env = get_engine().run("image.quality", {"data": jpeg_bytes, "category": "face"}, user=user)
"""
from .engine import AIEngine, HandlerContext, HandlerResult, get_engine, reset_engine  # noqa: F401
from .schemas import CapabilityState, RunEnvelope  # noqa: F401

"""Model lifecycle access for the engine. (Batch 9 extends this module with the full state machine, activation events and rollback; the read functions below keep their
signatures.)

Rules the engine relies on:
* A model is patient-facing ONLY if its `model_versions.status` is `approved_for_defined_use` (complete approval record AND passed evaluation gates, enforced when the
  approval was recorded) AND it is the task's active model. Nothing else is ever substituted: if the active model is missing, the capability is unavailable.
* Registered research models are visible to administrators for research use. They are never promoted by this module."""
from __future__ import annotations

import json

from .. import db
from .schemas import ModelRef


def _ref(row: dict, card: dict, lifecycle: str) -> ModelRef:
    return ModelRef(model_id=row["id"], task=row["task"], lifecycle=lifecycle, architecture=str(card.get("architecture", ""))[:80],
                    artifact_sha256_prefix=str(card.get("artifact_sha256", ""))[:16])


def _setting(key: str) -> str | None:
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else None


def derive_state(status: str, is_active: bool, gates_failed: bool) -> str:
    """Lifecycle state of a model row that predates the explicit lifecycle column."""
    if status == "approved_for_defined_use":
        return "active" if is_active else "approved"
    return "evaluation_failed" if gates_failed else "experimental"


def active_approved(task: str) -> ModelRef | None:
    with db.context.as_system():
        mid = _setting(f"active_model:{task}")
        if not mid:
            return None
        row = db.q1("SELECT id,task,data,status FROM model_versions WHERE id=?", (mid,))
        if not row or row["status"] != "approved_for_defined_use":
            return None
        return _ref(row, json.loads(row["data"]), "active")


def research_models(task: str) -> list[ModelRef]:
    from ..training import trainer
    out = []
    with db.context.as_system():
        active = _setting(f"active_model:{task}")
        for row in db.q("SELECT id,task,data,status FROM model_versions WHERE task=? ORDER BY created_at DESC", (task,)):
            if row["status"] != "research_only":
                continue
            card = json.loads(row["data"])
            failed = any(not g["passed"] for g in trainer.evaluate_gates(card))
            out.append(_ref(row, card, derive_state(row["status"], row["id"] == active, failed)))
    return out

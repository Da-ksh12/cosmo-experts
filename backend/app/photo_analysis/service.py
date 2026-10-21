"""Photo-analysis sessions: starting them, cancelling them, and the job that produces them.

One request carries 1 to 4 photos of one area. Each photo becomes its own session (job), so each has its own real status, result and cancel; together they share one conversation and one
assistant message, which is what the app renders as one assessment card with a photo switcher. The conversation and its two messages are created here, in the same request, so the
assessment is part of the chat from the first moment and is still there, with its real status, if the person leaves and comes back.

The job runs the SAME photo check and storage as the older /api/v1/images/check route (services.core.check_and_store_image: one implementation, one set of rules), then resolves what the
capability registry allows for this photo (ai.vision.analyze: only approved models can ever run for a patient) and assembles the typed result (builder.build)."""
from __future__ import annotations

import json
import logging

from .. import db, storage
from ..ai import vision
from ..gateway import jobs
from ..imaging import suitability
from ..services import core
from . import answers, builder, store

log = logging.getLogger("cosmo.photo_analysis")
TASK = store.TASK
MAX_PHOTOS = 4


class SubmitError(Exception):
    def __init__(self, status: int, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.headers = status, message, headers or {}


def _discard(owner: str, image_id: str) -> None:
    """The person cancelled after the photo had already been stored: take it back out (the file, then the record) so nothing is kept that they asked not to send."""
    row = db.q1("SELECT stored_path FROM images WHERE id=? AND owner_id=?", (image_id, owner))
    if row and row["stored_path"]:
        storage.delete(row["stored_path"])
    db.x("UPDATE images SET deleted=1, stored_path='' WHERE id=? AND owner_id=?", (image_id, owner))
    db.audit(owner, "photo_analysis.cancelled_discarded", image_id)


def run(owner: str, params: dict, data: bytes | None, ctx: dict):
    """The job. Returns (status, PhotoAnalysis dict)."""
    if not data:
        raise ValueError("no photo")
    ctx["stage"]("quality")
    out = core.check_and_store_image(owner, params["region"], data, ctx["request_id"])
    if ctx["cancelled"]():
        _discard(owner, out["id"])
        return "cancelled", {}
    ctx["stage"]("assessment")
    user = {"id": owner, "role": "patient", "name": ""}
    try:
        ia = vision.analyze(data, params["region"], user=user, request_id=ctx["request_id"])
    except Exception as e:                                           # the routing detail is an enrichment: the measured result stands without it
        log.warning("photo analysis: routing unavailable (%s)", type(e).__name__)
        ia = None
    previous = db.q1("SELECT COUNT(*) c FROM images WHERE owner_id=? AND region=? AND deleted=0 AND stored_path!='' AND id!=?", (owner, params["region"], out["id"]))["c"]
    job = db.q1("SELECT created_at FROM jobs WHERE id=?", (ctx["job_id"],))
    analysis = builder.build(analysis_id=ctx["job_id"], owner=owner, out=out, data=data, params=params, created_at=job["created_at"], finished_at=db.now(), ia=ia,
                             previous_photos=int(previous or 0), kb_care=answers.kb_supports_care())
    status = analysis["status"]
    db.audit(owner, "photo_analysis.completed", ctx["job_id"], {"verdict": out["report"].get("verdict"), "region": params["region"], "request_id": ctx["request_id"],
                                                                 "observations": len(analysis["observations"])})
    return status, analysis


jobs.register(TASK, run)


def submit(user: dict, region: str, files: list[bytes], conversation_id: str | None, idem_key: str | None, request_id: str) -> dict:
    """Start one session per photo and record the assessment in the conversation. Raises SubmitError (status + patient-safe message)."""
    if region not in suitability.REGIONS:
        raise SubmitError(400, "Please choose face / skin, hair / scalp, or body.")
    if not 1 <= len(files) <= MAX_PHOTOS:
        raise SubmitError(422, f"Send between 1 and {MAX_PHOTOS} photos at a time.")
    if any(not f for f in files):
        raise SubmitError(422, "One of the photos is empty. Please choose it again.")
    owner = user["id"]
    if conversation_id:
        c = db.q1("SELECT owner_id FROM conversations WHERE id=?", (conversation_id,))
        if not c or c["owner_id"] != owner:
            raise SubmitError(404, "Conversation not found.")
    n = len(files)
    if idem_key:                                                       # the same upload sent again (for example after the connection dropped) returns the sessions it already made
        first = db.q1("SELECT id, params FROM jobs WHERE owner_id=? AND idempotency_key=?", (owner, f"{idem_key}:0"))
        if first:
            p = json.loads(first["params"])
            ids = [r["id"] for r in (db.q1("SELECT id FROM jobs WHERE owner_id=? AND idempotency_key=?", (owner, f"{idem_key}:{i}")) for i in range(int(p.get("count", 1)))) if r]
            return {"conversation_id": p["conversation_id"], "message_id": p["message_id"], "analyses": [store.get(i, owner) for i in ids], "idempotent_replay": True}
    cid, mid = conversation_id or db.uid(), db.uid()
    created: list[dict] = []
    try:
        for i, data in enumerate(files):
            job, made = jobs.submit(TASK, owner, {"region": region, "conversation_id": cid, "message_id": mid, "index": i, "count": n}, data, request_id, f"{idem_key}:{i}" if idem_key else None)
            created.append(job)
        if not conversation_id:
            db.x("INSERT INTO conversations VALUES(?,?,?)", (cid, owner, db.now()))
        label = builder.REGION_LABEL.get(region, region).lower()
        sources = json.dumps([{"kind": "photo_analysis", "analysis_id": j["id"], "index": i} for i, j in enumerate(created)])
        text = f"I uploaded {'a photo' if n == 1 else f'{n} photos'} of my {label} for review."
        db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "patient", text, sources, db.now()))
        db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (mid, cid, "assistant", "Photo assessment", sources, db.now() + 0.001))
        db.audit(owner, "photo_analysis.submit", cid, {"photos": n, "region": region, "request_id": request_id})
    except jobs.QueueFull as e:
        for j in created:
            jobs.cancel(j["id"], owner)
        raise SubmitError(429, str(e), {"Retry-After": str(e.retry_after)}) from None
    except Exception:
        for j in created:
            jobs.cancel(j["id"], owner)
        raise
    return {"conversation_id": cid, "message_id": mid, "analyses": [store.get(j["id"], owner) for j in created], "idempotent_replay": False}


def cancel(analysis_id: str, owner: str) -> dict | None:
    j = jobs.cancel(analysis_id, owner)
    if not j or j["task"] != TASK:
        return None
    return store.get(analysis_id, owner)

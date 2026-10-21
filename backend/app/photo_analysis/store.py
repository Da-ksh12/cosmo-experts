"""Reading a photo-analysis session back: the same typed payload for a photo that is still being processed, one that finished, and one that failed or was cancelled.

A session IS a gateway job (task "photo_analysis"): its id is the analysis id, its status is the job's real status, its stage is what the worker reports it is doing, and its result is the
finished PhotoAnalysis. The job table already enforces ownership, so a patient can only ever read their own sessions: another person's id answers "not found", exactly like an unknown id.
Read fresh every time: whether the photo is still kept (the patient may have deleted it) and whether a clinician has reviewed it are looked up now, not frozen at analysis time."""
from __future__ import annotations

import json

from .. import db
from ..gateway import jobs
from . import builder
from . import schema as S

TASK = "photo_analysis"
TERMINAL_WITH_RESULT = ("completed", "uncertain", "rejected")


def params_of(analysis_id: str) -> dict:
    row = db.q1("SELECT params FROM jobs WHERE id=? AND task=?", (analysis_id, TASK))
    try:
        return json.loads(row["params"]) if row and row["params"] else {}
    except (TypeError, ValueError):
        return {}


def get(analysis_id: str, owner: str) -> dict | None:
    """The current PhotoAnalysis of this person's session as a plain dict, or None when it does not exist or is not theirs."""
    job = jobs.get(analysis_id, owner)
    if not job or job["task"] != TASK:
        return None
    params = params_of(analysis_id)
    if job["status"] in TERMINAL_WITH_RESULT and isinstance(job["result"], dict):
        a = _fresh(dict(job["result"]), owner)
    else:
        a = builder.minimal(job, params, stage=job.get("stage"), error=job["error"])
    return S.PhotoAnalysis.model_validate(a).model_dump(mode="json")        # one more validation on the way out: nothing unvalidated, least of all a coordinate, reaches a client


def _fresh(a: dict, owner: str) -> dict:
    """Update what can change after the analysis was made: the photo may have been deleted, a clinician may have reviewed it."""
    img = a.get("image") or {}
    iid = img.get("id")
    if iid:
        row = db.q1("SELECT stored_path, deleted, clinician_review FROM images WHERE id=? AND owner_id=?", (iid, owner))
        kept = bool(row and row["stored_path"] and not row["deleted"])
        if not kept:
            a["image"] = {**img, "kept": False, "url": None, "thumb_url": None}
            a["clinician_review"] = {"status": "not_applicable", "message": "This photo is no longer kept, so there is nothing for a clinician to review."}
        elif row["clinician_review"] == "reviewed":
            a["clinician_review"] = {"status": "reviewed", "message": "A clinician has reviewed this photo."}
    return a

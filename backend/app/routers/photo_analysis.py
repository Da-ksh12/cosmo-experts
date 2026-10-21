"""Photo-analysis sessions: /api/v1/photo-analyses. The contract is typed and versioned (app/photo_analysis/schema.py, docs/PHOTO_ANALYSIS.md).

POST   /api/v1/photo-analyses              1 to 4 photos of one area (multipart: region, files, optional conversation_id) -> one session per photo, recorded in a conversation. 202.
GET    /api/v1/photo-analyses/{id}         the session's real, current state (queued, processing + stage, completed, ...), with the located areas and evidence.
POST   /api/v1/photo-analyses/{id}/cancel  stop a session that has not finished; a photo already stored is taken back out.

Patients only, and only their own sessions: another person's id answers 404, exactly like an unknown id. Photos are served separately by /api/images/{id}/file under the same rule."""
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from .. import consent
from ..config import MAX_UPLOAD_BYTES
from ..photo_analysis import service, store
from ..security import require

router = APIRouter(prefix="/api/v1/photo-analyses")


def _read(f: UploadFile) -> bytes:
    data = f.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"This file is too large. The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return data


@router.post("")
def create(request: Request, region: str = Form(...), files: list[UploadFile] = File(...), conversation_id: str | None = Form(None),
           idempotency_key: str | None = Header(default=None), user=Depends(consent.patient_consented)):
    photos = [_read(f) for f in files]
    try:
        res = service.submit(user, region, photos, conversation_id, idempotency_key, getattr(request.state, "request_id", ""))
    except service.SubmitError as e:
        raise HTTPException(e.status, e.message, headers=e.headers or None) from None
    return JSONResponse(res, status_code=200 if res["idempotent_replay"] else 202)


@router.get("/{analysis_id}")
def read(analysis_id: str, user=Depends(require("patient"))):
    a = store.get(analysis_id, user["id"])
    if a is None:
        raise HTTPException(404, "Photo assessment not found.")
    return a


@router.post("/{analysis_id}/cancel")
def cancel(analysis_id: str, user=Depends(require("patient"))):
    a = service.cancel(analysis_id, user["id"])
    if a is None:
        raise HTTPException(404, "Photo assessment not found.")
    return a

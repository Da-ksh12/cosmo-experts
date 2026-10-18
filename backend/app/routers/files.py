"""Signed, short-lived download links for private files (see filelinks.py). Issuing needs the normal sign-in and authorization; redeeming re-checks both."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .. import db, filelinks, security, storage
from ..security import can_access_patient_data, current_user

router = APIRouter(prefix="/api")
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; sandbox"}
TABLES = {"document": ("documents", "Document"), "image": ("images", "Photo")}


def _row(kind: str, rid: str) -> dict | None:
    table = TABLES[kind][0]
    r = db.q1(f"SELECT * FROM {table} WHERE id=? AND deleted=0", (rid,))
    return r if r and r["stored_path"] else None


def _issue(kind: str, rid: str, user: dict):
    r = _row(kind, rid)
    if not r or not can_access_patient_data(user, r["owner_id"]):
        raise HTTPException(404, f"{TABLES[kind][1]} not found.")
    token = filelinks.issue(kind, rid, user["id"])
    db.audit(user["id"], "file.link_issued", rid, {"kind": kind, "owner": r["owner_id"]} if user["id"] != r["owner_id"] else {"kind": kind})
    return {"url": f"/api/files/{token}", "expires_in": filelinks.TTL_DEFAULT}


@router.post("/documents/{doc_id}/link")
def document_link(doc_id: str, user=Depends(current_user)):
    return _issue("document", doc_id, user)


@router.post("/images/{image_id}/link")
def image_link(image_id: str, user=Depends(current_user)):
    return _issue("image", image_id, user)


@router.get("/files/{token}")
def redeem(token: str):
    try:
        kind, rid, uid = filelinks.redeem(token)
    except filelinks.LinkError:
        raise HTTPException(404, "This link is not valid or has expired.") from None
    with db.context.as_system():
        u = db.q1("SELECT id,name,role,disabled FROM users WHERE id=?", (uid,))
    if not u or u["disabled"] or u["role"] not in security.ROLES:
        raise HTTPException(404, "This link is not valid or has expired.")
    user = {"id": u["id"], "name": u["name"], "role": u["role"]}
    with db.context.as_user(user["id"], user["role"]):
        r = _row(kind, rid)
        if not r or not can_access_patient_data(user, r["owner_id"]):
            raise HTTPException(404, "This link is not valid or has expired.")
        try:
            body = storage.read(r["stored_path"])
        except storage.StorageError:
            raise HTTPException(404, "This link is not valid or has expired.") from None
        db.audit(user["id"], "file.download_linked", rid, {"kind": kind})
        name = "download" if kind == "image" else "".join(c for c in (r["filename"] or "download") if c.isalnum() or c in " ._-()")[:100] or "download"
        mime = r["mime"] if kind == "document" else "image/jpeg"
    return Response(body, media_type=mime, headers={**NO_STORE, "Content-Disposition": f'attachment; filename="{name}"'})

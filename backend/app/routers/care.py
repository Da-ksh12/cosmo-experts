"""Care plans, history timeline, thumbnails, suggestions and other screen-supporting endpoints."""
import io
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import db, security
from .. import storage
from ..quotes import service as quotes
from ..services import notify
from ..security import current_user, can_access_patient_data, require

router = APIRouter(prefix="/api")
doctor = require("doctor", "admin")


# ---------- thumbnails & image files ----------

def _thumb_png(data: bytes, mime: str, size=360) -> bytes | None:
    from PIL import Image
    try:
        if mime == "application/pdf":
            import pymupdf
            with pymupdf.open(stream=data, filetype="pdf") as doc:
                pix = doc[0].get_pixmap(dpi=60)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
        elif mime.startswith("image/"):
            img = Image.open(io.BytesIO(data))
        else:
            return None
        img = img.convert("RGB")
        img.thumbnail((size, size))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80)
        return out.getvalue()
    except Exception:
        return None


@router.get("/documents/{doc_id}/thumbnail")
def doc_thumbnail(doc_id: str, user=Depends(current_user)):
    d = db.q1("SELECT * FROM documents WHERE id=? AND deleted=0", (doc_id,))
    if not d or not can_access_patient_data(user, d["owner_id"]):
        raise HTTPException(404, "Document not found.")
    try:
        t = _thumb_png(storage.read(d["stored_path"]), d["mime"])
    except storage.StorageError:
        t = None
    if not t:
        raise HTTPException(404, "No preview available.")
    return Response(t, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.get("/images/{iid}/file")
def image_file(iid: str, thumb: int = 0, user=Depends(current_user)):
    r = db.q1("SELECT * FROM images WHERE id=? AND deleted=0", (iid,))
    if not r or not r["stored_path"] or not can_access_patient_data(user, r["owner_id"]):
        raise HTTPException(404, "Photo not found.")
    try:
        data = storage.read(r["stored_path"])
    except storage.StorageError:
        raise HTTPException(404, "Photo not found.")
    if thumb:
        data = _thumb_png(data, "image/jpeg") or data
    if user["id"] != r["owner_id"]:
        db.audit(user["id"], "image.staff_access", iid, {"owner": r["owner_id"]})
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


# ---------- patient flags a doubtful extracted value ----------

class Flag(BaseModel):
    reason: str = Field(default="", max_length=300)


@router.post("/documents/{doc_id}/fields/{fid}/flag")
def flag_field(doc_id: str, fid: str, body: Flag, user=Depends(require("patient"))):
    d = db.q1("SELECT owner_id FROM documents WHERE id=? AND deleted=0", (doc_id,))
    f = db.q1("SELECT id FROM doc_fields WHERE id=? AND document_id=?", (fid, doc_id))
    if not d or d["owner_id"] != user["id"] or not f:
        raise HTTPException(404, "Not found.")
    db.x("UPDATE doc_fields SET patient_flag=? WHERE id=?", (body.reason or "flagged", fid))
    db.audit(user["id"], "field.patient_flag", fid)
    return {"flagged": True}


# ---------- conversations & suggestions ----------

@router.get("/conversations")
def list_conversations(user=Depends(require("patient"))):
    rows = db.q("SELECT id,created_at FROM conversations WHERE owner_id=? ORDER BY created_at DESC LIMIT 30", (user["id"],))
    for r in rows:
        first = db.q1("SELECT content FROM messages WHERE conversation_id=? AND role='patient' ORDER BY created_at LIMIT 1", (r["id"],))
        r["preview"] = (first or {"content": ""})["content"][:80]
    return [r for r in rows if r["preview"]]


@router.get("/assistant/suggestions")
def suggestions(user=Depends(require("patient"))):
    names = db.q("SELECT f.name FROM doc_fields f JOIN documents d ON d.id=f.document_id WHERE d.owner_id=? AND d.deleted=0 "
                 "AND f.review_status!='rejected' GROUP BY f.name ORDER BY MAX(d.created_at) DESC LIMIT 3", (user["id"],))
    out = [f"What does my {n['name']} result say?" for n in names]
    out.append("What questions should I ask my doctor?")
    if db.q1("SELECT 1 x FROM kb_articles WHERE review_status='approved' AND title LIKE '%reference range%'"):
        out.append("What is a reference range?")
    if any(quotes.effective_status(q) == "approved" for q in db.q("SELECT status,valid_until FROM quotes WHERE patient_id=? AND status='approved'", (user["id"],))):
        out.append("What does my quotation include?")
    return out


# ---------- care plans ----------

STAGE_STATUS = ("planned", "in_progress", "done")


class Stage(BaseModel):
    title: str = Field(max_length=80)
    detail: str = Field(default="", max_length=300)
    status: str = "planned"


class PlanBody(BaseModel):
    assessment: str = Field(default="", max_length=3000)
    findings: list[str] = Field(default_factory=list, max_length=12)
    stages: list[Stage] = Field(default_factory=list, max_length=8)
    notes: str = Field(default="", max_length=3000)
    recommended_item_ids: list[str] = Field(default_factory=list, max_length=20)


def _plan_out(r: dict) -> dict:
    stages = json.loads(r["stages"])
    done = sum(1 for s in stages if s["status"] == "done")
    return {"id": r["id"], "patient_id": r["patient_id"], "status": r["status"], "assessment": r["assessment"],
            "findings": json.loads(r["findings"]), "stages": stages, "notes": r["notes"],
            "recommended_item_ids": json.loads(r["recommended_item_ids"]), "reviewed_at": r["reviewed_at"], "updated_at": r["updated_at"],
            "progress": {"done": done, "total": len(stages), "percent": round(100 * done / len(stages)) if stages else None}}


def _doctor_card(doctor_id: str | None):
    if not doctor_id:
        return None
    u = db.q1("SELECT id,name,credentials,verified FROM users WHERE id=?", (doctor_id,))
    if not u:
        return None
    # "verified" is only true when an administrator recorded verification of the stated credentials
    return {"name": u["name"], "credentials": u["credentials"] or "", "verified": bool(u["verified"] and u["credentials"])}


@router.get("/care-plan")
def my_care_plan(user=Depends(require("patient"))):
    r = db.q1("SELECT * FROM care_plans WHERE patient_id=? AND status='approved' ORDER BY updated_at DESC LIMIT 1", (user["id"],))
    photos = db.q("SELECT id,region,verdict,created_at FROM images WHERE owner_id=? AND deleted=0 AND stored_path!='' ORDER BY created_at DESC LIMIT 8", (user["id"],))
    docs = db.q("SELECT id,filename,doc_type,created_at,review_status FROM documents WHERE owner_id=? AND deleted=0 ORDER BY created_at DESC LIMIT 8", (user["id"],))
    return {"plan": _plan_out(r) if r else None, "doctor": _doctor_card(r["doctor_id"]) if r else None,
            "photos": photos, "documents": docs,
            "awaiting_review": any(d["review_status"] != "reviewed_by_clinician" for d in docs)}


@router.get("/clinic/patients")
def clinic_patients(user=Depends(doctor)):
    return db.q("SELECT id,name FROM users WHERE role='patient' ORDER BY name")


@router.get("/clinic/care-plans/{patient_id}")
def clinic_get_plans(patient_id: str, user=Depends(doctor)):
    rows = db.q("SELECT * FROM care_plans WHERE patient_id=? ORDER BY updated_at DESC", (patient_id,))
    return [_plan_out(r) for r in rows]


@router.put("/clinic/care-plans/{patient_id}")
def clinic_save_draft(patient_id: str, body: PlanBody, user=Depends(require("doctor"))):
    if not db.q1("SELECT id FROM users WHERE id=? AND role='patient'", (patient_id,)):
        raise HTTPException(404, "Patient not found.")
    for s in body.stages:
        if s.status not in STAGE_STATUS:
            raise HTTPException(422, "Unknown stage status.")
    for iid in body.recommended_item_ids:
        if not db.q1("SELECT id FROM price_items WHERE id=? AND active=1", (iid,)):
            raise HTTPException(422, "A recommended item is not in the approved price list.")
    vals = (user["id"], body.assessment, json.dumps(body.findings), json.dumps([s.model_dump() for s in body.stages]), body.notes,
            json.dumps(body.recommended_item_ids), db.now())
    draft = db.q1("SELECT id FROM care_plans WHERE patient_id=? AND status='draft'", (patient_id,))
    if draft:
        db.x("UPDATE care_plans SET doctor_id=?, assessment=?, findings=?, stages=?, notes=?, recommended_item_ids=?, updated_at=? WHERE id=?",
             vals + (draft["id"],))
        pid = draft["id"]
    else:
        pid = db.uid()
        db.x("INSERT INTO care_plans(id,patient_id,doctor_id,status,assessment,findings,stages,notes,recommended_item_ids,reviewed_at,updated_at)"
             " VALUES(?,?,?,'draft',?,?,?,?,?,NULL,?)", (pid, patient_id, *vals))
    db.audit(user["id"], "careplan.save_draft", pid, {"patient": patient_id})
    return _plan_out(db.q1("SELECT * FROM care_plans WHERE id=?", (pid,)))


@router.post("/clinic/care-plans/{plan_id}/approve")
def clinic_approve(plan_id: str, user=Depends(require("doctor"))):
    r = db.q1("SELECT * FROM care_plans WHERE id=? AND status='draft'", (plan_id,))
    if not r:
        raise HTTPException(404, "Draft plan not found.")
    if not r["assessment"].strip():
        raise HTTPException(422, "Write the clinical assessment before approving.")
    db.x("UPDATE care_plans SET status='superseded' WHERE patient_id=? AND status='approved'", (r["patient_id"],))
    db.x("UPDATE care_plans SET status='approved', doctor_id=?, reviewed_at=?, updated_at=? WHERE id=?", (user["id"], db.now(), db.now(), plan_id))
    db.audit(user["id"], "careplan.approve", plan_id, {"patient": r["patient_id"]})
    notify.send(r["patient_id"], "plan_approved", "Your care plan is ready", "Your clinician approved a care plan for you.", "/care", f"plan-approved:{plan_id}")
    return _plan_out(db.q1("SELECT * FROM care_plans WHERE id=?", (plan_id,)))


class StageUpdate(BaseModel):
    status: str


@router.post("/clinic/care-plans/{plan_id}/stages/{idx}")
def clinic_stage(plan_id: str, idx: int, body: StageUpdate, user=Depends(require("doctor"))):
    r = db.q1("SELECT * FROM care_plans WHERE id=? AND status='approved'", (plan_id,))
    if not r or body.status not in STAGE_STATUS:
        raise HTTPException(404, "Approved plan or status not found.")
    stages = json.loads(r["stages"])
    if not 0 <= idx < len(stages):
        raise HTTPException(404, "Stage not found.")
    stages[idx]["status"] = body.status
    stages[idx]["done_at"] = db.now() if body.status == "done" else None
    db.x("UPDATE care_plans SET stages=?, updated_at=? WHERE id=?", (json.dumps(stages), db.now(), plan_id))
    db.audit(user["id"], "careplan.stage", plan_id, {"idx": idx, "status": body.status})
    return _plan_out(db.q1("SELECT * FROM care_plans WHERE id=?", (plan_id,)))


class Verify(BaseModel):
    credentials: str = Field(max_length=200)
    verified: bool


@router.post("/admin/users/{uid}/verify-clinician")
def verify_clinician(uid: str, body: Verify, user=Depends(require("admin"))):
    if not db.q1("SELECT id FROM users WHERE id=? AND role='doctor'", (uid,)):
        raise HTTPException(404, "Clinician not found.")
    db.x("UPDATE users SET credentials=?, verified=? WHERE id=?", (body.credentials, 1 if body.verified else 0, uid))
    db.audit(user["id"], "clinician.verify", uid, body.model_dump())
    return {"ok": True}


# ---------- user management (administrator; local accounts) ----------
# With COSMO_AUTH=supabase accounts and roles live in Supabase Auth / public.profiles and are managed there; these endpoints then refuse changes.

import re as _re

_ID = _re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")


def _local_only():
    from .. import supabase_auth
    if supabase_auth.configured():
        raise HTTPException(409, "Accounts are managed in Supabase Auth for this deployment.")


def _active_admins(excluding: str = "") -> int:
    return db.q1("SELECT COUNT(*) c FROM users WHERE role='admin' AND disabled=0 AND id!=?", (excluding,))["c"]


class NewUser(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    role: str
    password: str = Field(min_length=12, max_length=128)


class RoleChange(BaseModel):
    role: str


class DisableChange(BaseModel):
    disabled: bool


class PasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=128)


@router.get("/admin/users")
def list_users(user=Depends(require("admin"))):
    return db.q("SELECT id,name,role,verified,credentials,disabled FROM users ORDER BY role,id")      # never includes password hashes


@router.post("/admin/users")
def create_user(body: NewUser, user=Depends(require("admin"))):
    _local_only()
    if body.role not in security.ROLES:
        raise HTTPException(422, "Unknown role.")
    if not _ID.match(body.id):
        raise HTTPException(422, "User id must be 3-40 characters: lowercase letters, digits, dot, dash or underscore.")
    if db.q1("SELECT 1 x FROM users WHERE id=?", (body.id,)):
        raise HTTPException(409, "That user id is already taken.")
    db.x("INSERT INTO users(id,name,role,password_hash,credentials,verified,disabled) VALUES(?,?,?,?, '', 0, 0)", (body.id, body.name.strip(), body.role, security.new_hash(body.password)))
    db.audit(user["id"], "user.create", body.id, {"role": body.role})
    return {"id": body.id, "role": body.role, "verified": 0}


@router.post("/admin/users/{uid}/role")
def change_role(uid: str, body: RoleChange, user=Depends(require("admin"))):
    _local_only()
    target = db.q1("SELECT id,role FROM users WHERE id=?", (uid,))
    if not target:
        raise HTTPException(404, "User not found.")
    if body.role not in security.ROLES:
        raise HTTPException(422, "Unknown role.")
    if uid == user["id"]:
        raise HTTPException(409, "You cannot change your own role.")
    if target["role"] == "admin" and body.role != "admin" and _active_admins(uid) == 0:
        raise HTTPException(409, "At least one active administrator must remain.")
    # a changed role never inherits clinical verification: it must be recorded again by an administrator
    db.x("UPDATE users SET role=?, verified=0, credentials='' WHERE id=?", (body.role, uid))
    db.audit(user["id"], "user.role", uid, {"from": target["role"], "to": body.role})
    return {"id": uid, "role": body.role}


@router.post("/admin/users/{uid}/disabled")
def set_disabled(uid: str, body: DisableChange, user=Depends(require("admin"))):
    _local_only()
    target = db.q1("SELECT id,role,disabled FROM users WHERE id=?", (uid,))
    if not target:
        raise HTTPException(404, "User not found.")
    if uid == user["id"]:
        raise HTTPException(409, "You cannot disable your own account.")
    if body.disabled and target["role"] == "admin" and _active_admins(uid) == 0:
        raise HTTPException(409, "At least one active administrator must remain.")
    db.x("UPDATE users SET disabled=? WHERE id=?", (1 if body.disabled else 0, uid))
    db.audit(user["id"], "user.disable" if body.disabled else "user.enable", uid)
    return {"id": uid, "disabled": body.disabled}


@router.post("/admin/users/{uid}/reset-password")
def reset_password(uid: str, body: PasswordReset, user=Depends(require("admin"))):
    _local_only()
    if not db.q1("SELECT 1 x FROM users WHERE id=?", (uid,)):
        raise HTTPException(404, "User not found.")
    db.x("UPDATE users SET password_hash=? WHERE id=?", (security.new_hash(body.password), uid))
    db.audit(user["id"], "user.password_reset", uid)                                                    # the password itself is never logged
    return {"id": uid}


# ---------- unified history ----------

@router.get("/history")
def history(kind: str = "all", since_days: int = 0, q: str = "", user=Depends(require("patient"))):
    uid = user["id"]
    items = []
    for d in db.q("SELECT id,filename,doc_type,doc_date,page_count,review_status,created_at FROM documents WHERE owner_id=? AND deleted=0", (uid,)):
        k = "prescription" if d["doc_type"] == "prescription" else "report"
        items.append({"kind": k, "id": d["id"], "title": d["filename"], "subtitle": f'{d["doc_type"].replace("_", " ").title()} · {d["page_count"]} page(s)',
                      "ts": d["created_at"], "status": d["review_status"], "link": f'/reports/{d["id"]}', "doc_type": d["doc_type"]})
    for i in db.q("SELECT id,region,verdict,created_at,stored_path,clinician_review FROM images WHERE owner_id=? AND deleted=0", (uid,)):
        rev = i["clinician_review"] == "reviewed"
        items.append({"kind": "photo", "id": i["id"], "title": f'{i["region"].replace("_", " / ").title()} photo',
                      "subtitle": f'Photo check: {i["verdict"]}' + (" · reviewed by a clinician" if rev else " · awaiting clinician review" if i["stored_path"] else ""),
                      "ts": i["created_at"], "status": "reviewed" if rev else i["verdict"], "link": "/history?kind=photos", "has_file": bool(i["stored_path"]), "region": i["region"]})
    for qu in db.q("SELECT id,status,total,currency,created_at FROM quotes WHERE patient_id=?", (uid,)):
        items.append({"kind": "quotation", "id": qu["id"], "title": f'Quotation {qu["id"][:8]}',
                      "subtitle": (f'{qu["total"]:.2f} {qu["currency"]}' if qu["status"] == "approved" else "Awaiting clinic approval"),
                      "ts": qu["created_at"], "status": qu["status"], "link": "/quote"})
    for p in db.q("SELECT * FROM care_plans WHERE patient_id=? AND status IN ('approved','superseded')", (uid,)):
        doc = _doctor_card(p["doctor_id"])
        items.append({"kind": "consultation", "id": p["id"], "title": "Doctor review and care plan", "subtitle": f'Reviewed by {doc["name"]}' if doc else "Reviewed",
                      "ts": p["reviewed_at"] or p["updated_at"], "status": "reviewed", "link": "/care"})
        for s in json.loads(p["stages"]):
            if s["status"] == "done" and s.get("done_at") and p["status"] == "approved":
                items.append({"kind": "treatment", "id": f'{p["id"]}:{s["title"]}', "title": s["title"], "subtitle": "Care-plan stage completed",
                              "ts": s["done_at"], "status": "done", "link": "/care"})
    if kind != "all":
        wanted = {"consultations": {"consultation"}, "reports": {"report", "prescription"}, "treatments": {"treatment"},
                  "photos": {"photo"}, "quotations": {"quotation"}}.get(kind, {kind})
        items = [i for i in items if i["kind"] in wanted]
    if since_days > 0:
        cut = db.now() - since_days * 86400
        items = [i for i in items if i["ts"] >= cut]
    if q.strip():
        ql = q.lower()
        items = [i for i in items if ql in (i["title"] + " " + i["subtitle"]).lower()]
    return sorted(items, key=lambda i: -i["ts"])


# ---------- patient data deletion (right-to-erasure workflow; legal review of retention rules required) ----------

@router.post("/me/delete-my-data")
def delete_my_data(user=Depends(require("patient"))):
    """Deletes what the patient controls: uploaded documents (files + extracted text/fields), photos, conversations and messages.
    Clinician-authored care plans, quotations and the audit trail are RETAINED (clinical/financial record-keeping duties) and are reported back,
    so the patient knows exactly what remains. The retention schedule must be confirmed by legal counsel for the deployment jurisdiction."""
    uid = user["id"]
    docs = db.q("SELECT id,stored_path FROM documents WHERE owner_id=? AND deleted=0", (uid,))
    for d in docs:
        storage.delete(d["stored_path"])
        db.x("DELETE FROM doc_pages WHERE document_id=?", (d["id"],))
        db.x("DELETE FROM doc_fields WHERE document_id=?", (d["id"],))
    db.x("UPDATE documents SET deleted=1, stored_path='', filename='deleted' WHERE owner_id=?", (uid,))
    imgs = db.q("SELECT id,stored_path FROM images WHERE owner_id=? AND deleted=0", (uid,))
    for i in imgs:
        storage.delete(i["stored_path"])
    db.x("UPDATE images SET deleted=1, stored_path='' WHERE owner_id=?", (uid,))
    convs = db.q("SELECT id FROM conversations WHERE owner_id=?", (uid,))
    for c in convs:
        db.x("DELETE FROM messages WHERE conversation_id=?", (c["id"],))
    db.x("DELETE FROM conversations WHERE owner_id=?", (uid,))
    db.x("DELETE FROM jobs WHERE owner_id=?", (uid,))
    with db.context.as_system():                              # the patient may read and mark their notifications; removing them is a server action
        db.x("DELETE FROM notifications WHERE user_id=?", (uid,))
    retained = {"care_plans": db.q1("SELECT COUNT(*) c FROM care_plans WHERE patient_id=?", (uid,))["c"],
                "quotations": db.q1("SELECT COUNT(*) c FROM quotes WHERE patient_id=?", (uid,))["c"]}
    db.audit(uid, "patient.data_deleted", uid, {"documents": len(docs), "photos": len(imgs), "conversations": len(convs), "retained": retained})
    return {"deleted": {"documents": len(docs), "photos": len(imgs), "conversations": len(convs)}, "retained_by_policy": retained,
            "note": "Care plans, quotations and the audit log are kept for clinical and financial record-keeping. Ask the clinic about their retention period."}


# ---------------- notifications (own only) ----------------

@router.get("/notifications")
def my_notifications(unread: bool = False, user=Depends(current_user)):
    rows = db.q("SELECT id,kind,title,body,link,created_at,read_at FROM notifications WHERE user_id=?" + (" AND read_at IS NULL" if unread else "")
                + " ORDER BY created_at DESC LIMIT 50", (user["id"],))
    return {"unread": db.q1("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND read_at IS NULL", (user["id"],))["c"], "items": rows}


@router.post("/notifications/read-all")
def read_all(user=Depends(current_user)):
    db.x("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (db.now(), user["id"]))
    return {"unread": 0}


@router.post("/notifications/{nid}/read")
def read_one(nid: str, user=Depends(current_user)):
    if not db.q1("SELECT 1 x FROM notifications WHERE id=? AND user_id=?", (nid, user["id"])):          # someone else's id is indistinguishable from a missing one
        raise HTTPException(404, "Notification not found.")
    db.x("UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE id=? AND user_id=?", (db.now(), nid, user["id"]))
    return {"unread": db.q1("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND read_at IS NULL", (user["id"],))["c"]}

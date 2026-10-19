import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import consent, db, retention, security
from ..assistant import engine
from ..config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from ..documents import pipeline, store
from ..imaging import suitability
from ..quotes import service as quotes
from .. import storage
from ..services import core, explanations, notify
from ..security import current_user, require, can_access_patient_data
from ..speech import adapters as speech

router = APIRouter(prefix="/api")


class Login(BaseModel):
    user_id: str
    password: str


@router.post("/auth/login")
def login(body: Login, response: Response):
    from .. import auth_flows, supabase_auth
    if supabase_auth.configured():                            # Supabase Auth: `user_id` carries the e-mail address; see auth_flows.login
        return auth_flows.login(body.user_id, body.password, response)
    with db.context.as_system():                              # nobody is signed in yet: the sign-in itself is a server action
        u = db.q1("SELECT * FROM users WHERE id=?", (body.user_id,))
        if not u or not security.verify_pw(body.password, u["password_hash"]):
            db.audit(body.user_id, "auth.failed")
            raise HTTPException(401, "Incorrect user or password.")
        if u["disabled"]:
            db.audit(u["id"], "auth.refused_disabled")
            raise HTTPException(403, "This account has been disabled. Please contact the clinic.")
        db.audit(u["id"], "auth.login")
    return {"token": security.make_token(u["id"]), "user": {"id": u["id"], "name": u["name"], "role": u["role"]}}


@router.get("/consent")
def consent_status(user=Depends(current_user)):
    return consent.status_for(user)


class ConsentBody(BaseModel):
    notice_sha256: str


@router.post("/consent/accept")
def consent_accept(body: ConsentBody, user=Depends(require("patient"))):
    consent.accept(user, body.notice_sha256)
    return consent.status_for(user)


@router.post("/consent/withdraw")
def consent_withdraw(user=Depends(require("patient"))):
    consent.withdraw(user)
    return consent.status_for(user)


@router.get("/consent/history")
def consent_history(user=Depends(require("patient"))):
    return {"events": consent.history(user["id"])}


@router.get("/me")
def me(user=Depends(current_user)):
    return user


def _read_upload(f: UploadFile) -> bytes:
    data = f.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"This file is too large. The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return data


# ---------------- images ----------------

@router.post("/images")
def upload_image(region: str = Form(...), file: UploadFile = File(...), user=Depends(consent.patient_consented)):
    if region not in suitability.REGIONS:
        raise HTTPException(400, "Please choose face / skin, hair / scalp, or body.")
    return core.check_and_store_image(user["id"], region, _read_upload(file))


@router.get("/images")
def list_images(user=Depends(current_user)):
    rows = db.q("SELECT id,owner_id,region,verdict,report,created_at FROM images WHERE deleted=0" +
                ("" if user["role"] != "patient" else " AND owner_id=?") + " ORDER BY created_at DESC",
                () if user["role"] != "patient" else (user["id"],))
    return [dict(r, report=json.loads(r["report"])) for r in rows]


@router.delete("/images/{iid}")
def delete_image(iid: str, user=Depends(current_user)):
    r = db.q1("SELECT * FROM images WHERE id=? AND deleted=0", (iid,))
    if not r or r["owner_id"] != user["id"]:
        raise HTTPException(404, "Not found.")
    storage.delete(r["stored_path"])
    db.x("UPDATE images SET deleted=1, stored_path='' WHERE id=?", (iid,))
    db.audit(user["id"], "image.delete", iid)
    return {"deleted": True}


# ---------------- documents ----------------

def _doc_or_404(doc_id: str, user) -> dict:
    d = db.q1("SELECT * FROM documents WHERE id=? AND deleted=0", (doc_id,))
    if not d or not can_access_patient_data(user, d["owner_id"]):
        raise HTTPException(404, "Document not found.")
    if user["id"] != d["owner_id"]:
        db.audit(user["id"], "document.staff_access", doc_id, {"owner": d["owner_id"]})
    return d


@router.post("/documents")
def upload_document(file: UploadFile = File(...), category: str = Form(""), user=Depends(consent.patient_consented)):
    data = _read_upload(file)
    try:
        return core.ingest_document(user["id"], file.filename or "upload", data, category)
    except pipeline.DocumentError as e:
        raise HTTPException(400 if str(e) == "Unknown document category." else getattr(e, "status", 422), str(e))


@router.get("/documents")
def list_documents(patient_id: str | None = None, user=Depends(current_user)):
    owner = user["id"] if user["role"] == "patient" else patient_id
    rows = db.q("SELECT id,owner_id,filename,doc_type,user_category,doc_date,page_count,size_bytes,mime,review_status,error,created_at FROM documents WHERE deleted=0" +
                (" AND owner_id=?" if owner else "") + " ORDER BY created_at DESC", (owner,) if owner else ())
    return rows


@router.get("/documents/compare")
def compare_documents(user=Depends(require("patient"))):
    rows = db.q("SELECT f.*, d.doc_date, d.id AS document_id FROM doc_fields f JOIN documents d ON d.id=f.document_id "
                "WHERE d.owner_id=? AND d.deleted=0", (user["id"],))
    return pipeline.compare(rows)


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, user=Depends(current_user)):
    d = _doc_or_404(doc_id, user)
    d.pop("stored_path", None)
    return {"document": d,
            "pages": db.q("SELECT page_no,text,method,ocr_confidence,script FROM doc_pages WHERE document_id=? ORDER BY page_no", (doc_id,)),
            "fields": store.load_fields(doc_id), "facts": store.load_facts(doc_id), "medications": store.load_medications(doc_id)}


@router.get("/documents/{doc_id}/file")
def get_document_file(doc_id: str, user=Depends(current_user)):
    d = _doc_or_404(doc_id, user)
    try:
        body = storage.read(d["stored_path"])
    except storage.StorageError:
        raise HTTPException(404, "The original file is no longer stored.")
    db.audit(user["id"], "document.download", doc_id)
    return Response(body, media_type=d["mime"], headers={"Content-Disposition": f'inline; filename="{d["filename"]}"', "X-Content-Type-Options": "nosniff"})


@router.get("/documents/{doc_id}/explanation")
def get_explanation(doc_id: str, lang: str = "en", user=Depends(current_user)):
    """Same service as the engine capability `document.explain`: one access rule, one audit record, one safety screening."""
    try:
        return explanations.build(user, doc_id, lang)
    except explanations.NotFound:
        raise HTTPException(404, "Document not found.")


@router.post("/documents/{doc_id}/request-review")
def request_document_review(doc_id: str, user=Depends(consent.patient_consented)):
    """Ask the clinic team to look at this document. (The assistant's confirmed action of the same name calls the same function.)"""
    try:
        return core.request_review(user, doc_id)
    except LookupError:
        raise HTTPException(404, "Document not found.")


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user=Depends(require("patient"))):
    d = _doc_or_404(doc_id, user)
    storage.delete(d["stored_path"])
    with db.tx() as t:
        store.delete_children(t, doc_id)
        t.x("UPDATE documents SET deleted=1, stored_path='', filename='deleted' WHERE id=?", (doc_id,))
        db.audit(user["id"], "document.delete", doc_id, t=t)
    return {"deleted": True}


# ---------------- conversation ----------------

class Msg(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/conversations")
def new_conversation(user=Depends(consent.patient_consented)):
    cid = db.uid()
    db.x("INSERT INTO conversations VALUES(?,?,?)", (cid, user["id"], db.now()))
    return {"id": cid, "retention_days": retention.configured_days().get("conversations")}


def _conv(cid, user):
    c = db.q1("SELECT * FROM conversations WHERE id=?", (cid,))
    if not c or c["owner_id"] != user["id"]:
        raise HTTPException(404, "Conversation not found.")
    return c


@router.post("/conversations/{cid}/messages")
def ask(cid: str, body: Msg, user=Depends(consent.patient_consented)):
    _conv(cid, user)
    db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "patient", body.text, "[]", db.now()))
    out = engine.answer(user["id"], cid, body.text)
    db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "assistant", out["answer"], json.dumps(out["sources"]), db.now() + 0.001))
    db.audit(user["id"], "assistant.ask", cid, {"abstained": out["abstained"], "reason": out.get("reason")})
    return out


@router.get("/conversations/{cid}")
def get_conversation(cid: str, user=Depends(require("patient"))):
    _conv(cid, user)
    return [dict(m, sources=json.loads(m["sources"])) for m in
            db.q("SELECT role,content,sources,created_at FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,))]


@router.delete("/conversations/{cid}")
def delete_conversation(cid: str, user=Depends(require("patient"))):
    _conv(cid, user)
    db.x("DELETE FROM messages WHERE conversation_id=?", (cid,))
    db.x("DELETE FROM conversations WHERE id=?", (cid,))
    db.audit(user["id"], "conversation.delete", cid)
    return {"deleted": True}


# ---------------- speech ----------------

class TTSReq(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    language: str = "en-IN"
    rate: float = 1.0


@router.get("/speech/status")
def speech_status(user=Depends(current_user)):
    tts = speech.get_tts().status()
    stt = speech.stt_status()
    if user["role"] == "patient":   # configuration detail is for administrators only
        tts = {"available": tts["available"]}
        stt = {"available": stt["available"]}
    return {"tts": tts, "stt": stt, "browser_fallback": "On-device browser voices may be used when the server has no voice; text is always shown."}


@router.post("/speech/tts")
def tts(body: TTSReq, user=Depends(current_user)):
    a = speech.get_tts()
    if not a.status()["available"]:
        raise HTTPException(503, "Voice playback is not available right now. The text is shown instead.")
    try:
        wav = a.synthesize(body.text, body.language, body.rate)
    except Exception:
        raise HTTPException(503, "Voice playback failed. The text is shown instead.")
    return Response(wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/speech/transcribe")
def transcribe(language: str | None = Form(None), file: UploadFile = File(...), user=Depends(consent.patient_consented)):
    st = speech.stt_status()
    if not st["available"]:
        raise HTTPException(503, "Voice questions are not available. Please type your question.")
    audio = _read_upload(file)
    try:
        text = speech.transcribe(audio, language)   # audio discarded inside; only text returned for patient confirmation
    except speech.AudioError as e:
        raise HTTPException(422, str(e))
    return {"text": text, "needs_confirmation": True}


# ---------------- quotations ----------------

class QuoteReq(BaseModel):
    items: list[dict]
    notes: str = ""


@router.get("/price-items")
def price_items(user=Depends(current_user)):
    return db.q("SELECT id,name,description,unit_price,currency,category,sessions,duration_min,compare_at_price FROM price_items "
                "WHERE active=1 AND approved_by!='' ORDER BY category,name")


@router.get("/quote-config")
def quote_config(user=Depends(current_user)):
    return quotes.get_config()


@router.post("/quotes")
def request_quote(body: QuoteReq, user=Depends(consent.patient_consented)):
    try:
        return core.request_quote(user, body.items, body.notes)
    except quotes.QuoteError as e:
        raise HTTPException(422, str(e))


@router.get("/quotes")
def my_quotes(patient_id: str | None = None, user=Depends(current_user)):
    owner = user["id"] if user["role"] == "patient" else patient_id
    rows = db.q("SELECT * FROM quotes" + (" WHERE patient_id=?" if owner else "") + " ORDER BY created_at DESC", (owner,) if owner else ())
    return [dict(r, lines=json.loads(r["lines"]), effective_status=quotes.effective_status(r)) for r in rows]


@router.get("/quotes/{qid}/pdf")
def quote_pdf(qid: str, user=Depends(current_user)):
    qu = db.q1("SELECT * FROM quotes WHERE id=?", (qid,))
    if not qu or not can_access_patient_data(user, qu["patient_id"]):
        raise HTTPException(404, "Quotation not found.")
    name = (db.q1("SELECT name FROM users WHERE id=?", (qu["patient_id"],)) or {"name": "Patient"})["name"]
    db.audit(user["id"], "quote.pdf", qid)
    return Response(quotes.pdf(qu, name), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="quotation-{qid[:8]}.pdf"'})


# ---------------- home summary ----------------

@router.get("/home")
def home(user=Depends(require("patient"))):
    return {"recent": {
        "documents": db.q("SELECT id,filename,doc_type,review_status,created_at FROM documents WHERE owner_id=? AND deleted=0 ORDER BY created_at DESC LIMIT 3", (user["id"],)),
        "images": db.q("SELECT id,region,verdict,created_at FROM images WHERE owner_id=? AND deleted=0 ORDER BY created_at DESC LIMIT 3", (user["id"],)),
        "quotes": db.q("SELECT id,status,total,currency FROM quotes WHERE patient_id=? ORDER BY created_at DESC LIMIT 3", (user["id"],))}}

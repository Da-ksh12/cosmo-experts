import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import db, security
from ..assistant import engine
from ..config import CONVERSATION_RETENTION_DAYS, MAX_UPLOAD_BYTES, UPLOAD_DIR
from ..documents import explain, pipeline
from ..imaging import suitability
from ..quotes import service as quotes
from .. import storage
from ..services import core, notify
from ..security import current_user, require, can_access_patient_data
from ..speech import adapters as speech

router = APIRouter(prefix="/api")


class Login(BaseModel):
    user_id: str
    password: str


@router.post("/auth/login")
def login(body: Login):
    from .. import supabase_auth
    if supabase_auth.configured():
        raise HTTPException(410, "Sign in with your account provider; this endpoint is disabled.")
    u = db.q1("SELECT * FROM users WHERE id=?", (body.user_id,))
    if not u or u["password_hash"] != security.hash_pw(body.password):
        db.audit(body.user_id, "auth.failed")
        raise HTTPException(401, "Incorrect user or password.")
    db.audit(u["id"], "auth.login")
    return {"token": security.make_token(u["id"]), "user": {"id": u["id"], "name": u["name"], "role": u["role"]}}


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
def upload_image(region: str = Form(...), file: UploadFile = File(...), user=Depends(require("patient"))):
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
def upload_document(file: UploadFile = File(...), category: str = Form(""), user=Depends(require("patient"))):
    data = _read_upload(file)
    try:
        return core.ingest_document(user["id"], file.filename or "upload", data, category)
    except pipeline.DocumentError as e:
        raise HTTPException(400 if str(e) == "Unknown document category." else 422, str(e))


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
            "pages": db.q("SELECT page_no,text,method,ocr_confidence FROM doc_pages WHERE document_id=? ORDER BY page_no", (doc_id,)),
            "fields": db.q("SELECT * FROM doc_fields WHERE document_id=? ORDER BY source_page,rowid", (doc_id,))}


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
    d = _doc_or_404(doc_id, user)
    fields = db.q("SELECT * FROM doc_fields WHERE document_id=? AND review_status!='rejected' ORDER BY source_page,rowid", (doc_id,))
    pages = db.q("SELECT page_no,method,ocr_confidence FROM doc_pages WHERE document_id=?", (doc_id,))
    out = explain.explain_document(d, fields, pages, lang)
    out["document_review_status"] = d["review_status"]
    out["extraction_warnings"] = [w for w in (d["error"] or "").split("; ") if w]
    return out


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user=Depends(require("patient"))):
    d = _doc_or_404(doc_id, user)
    storage.delete(d["stored_path"])
    db.x("DELETE FROM doc_pages WHERE document_id=?", (doc_id,))
    db.x("DELETE FROM doc_fields WHERE document_id=?", (doc_id,))
    db.x("UPDATE documents SET deleted=1, stored_path='', filename='deleted' WHERE id=?", (doc_id,))
    db.audit(user["id"], "document.delete", doc_id)
    return {"deleted": True}


# ---------------- conversation ----------------

class Msg(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/conversations")
def new_conversation(user=Depends(require("patient"))):
    cid = db.uid()
    db.x("INSERT INTO conversations VALUES(?,?,?)", (cid, user["id"], db.now()))
    return {"id": cid, "retention_days": CONVERSATION_RETENTION_DAYS}


def _conv(cid, user):
    c = db.q1("SELECT * FROM conversations WHERE id=?", (cid,))
    if not c or c["owner_id"] != user["id"]:
        raise HTTPException(404, "Conversation not found.")
    return c


@router.post("/conversations/{cid}/messages")
def ask(cid: str, body: Msg, user=Depends(require("patient"))):
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
def transcribe(language: str | None = Form(None), file: UploadFile = File(...), user=Depends(require("patient"))):
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
def request_quote(body: QuoteReq, user=Depends(require("patient"))):
    try:
        t = quotes.build_lines(body.items)
    except quotes.QuoteError as e:
        raise HTTPException(422, str(e))
    qid = db.uid()
    db.x("INSERT INTO quotes(id,patient_id,status,lines,total,currency,notes,created_by,approved_by,created_at,updated_at,subtotal,tax_percent,tax_amount)"
         " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (qid, user["id"], "draft", json.dumps(t["lines"]), t["total"], t["currency"], body.notes[:500], user["id"], "", db.now(), db.now(),
          t["subtotal"], t["tax_percent"], t["tax_amount"]))
    db.audit(user["id"], "quote.request", qid)
    notify.to_role("doctor", "quote_requested", "New quotation request", "A patient asked for a quotation.", "/clinic", f"quote-request:{qid}")
    return {"id": qid, "status": "draft", **{k: t[k] for k in ("subtotal", "tax_amount", "total", "currency")},
            "message": "Your request was sent. The clinic team will review and approve a quotation."}


@router.get("/quotes")
def my_quotes(patient_id: str | None = None, user=Depends(current_user)):
    owner = user["id"] if user["role"] == "patient" else patient_id
    rows = db.q("SELECT * FROM quotes" + (" WHERE patient_id=?" if owner else "") + " ORDER BY created_at DESC", (owner,) if owner else ())
    return [dict(r, lines=json.loads(r["lines"])) for r in rows]


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

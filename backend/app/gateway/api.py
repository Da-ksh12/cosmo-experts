"""Versioned inference gateway: /api/v1. Task-specific handlers behind one job API."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from .. import db, consent
from ..config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from ..documents import pipeline
from ..imaging import suitability
from ..security import current_user, require
from ..services import core
from ..speech import adapters as speech
from . import jobs

router = APIRouter(prefix="/api/v1")


class Public(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.public_message = message


# ---------------- handlers (run in worker threads) ----------------
def h_image(owner, params, data, ctx):
    out = core.check_and_store_image(owner, params["region"], data, ctx["request_id"])
    v = out["report"]["verdict"]
    return {"accepted": "completed", "uncertain": "uncertain", "rejected": "rejected"}[v], out


def h_document(owner, params, data, ctx):
    try:
        return "completed", core.ingest_document(owner, params["filename"], data, params.get("category", ""), ctx["request_id"])
    except pipeline.DocumentError as e:
        return "rejected", {"error": str(e)}


def h_stt(owner, params, data, ctx):
    st = speech.stt_status()
    if not st["available"]:
        raise Public("Voice questions are not available. Please type your question.")
    t0 = time.time()
    try:
        text = speech.transcribe(data, params.get("language"))
    except speech.AudioError as e:
        raise Public(str(e))
    return "completed", {"text": text, "needs_confirmation": True, "processing_ms": round((time.time() - t0) * 1000)}


def h_answer(owner, params, data, ctx):
    from ..assistant import engine
    cid = params["conversation_id"]
    c = db.q1("SELECT owner_id FROM conversations WHERE id=?", (cid,))
    if not c or c["owner_id"] != owner:
        raise Public("Conversation not found.")
    db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "patient", params["text"], "[]", db.now()))
    out = engine.answer(owner, cid, params["text"])
    db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "assistant", out["answer"], json.dumps(out["sources"]), db.now() + 0.001))
    db.audit(owner, "assistant.ask", cid, {"abstained": out["abstained"], "reason": out.get("reason"), "request_id": ctx["request_id"], "mode": out.get("mode")})
    return ("uncertain" if out["abstained"] else "completed"), out


def h_research(owner, params, data, ctx):
    """Administrator-only research inference, run through the unified AI engine (limits, cancellation, safety review, provenance). The route is admin-only; the engine checks again."""
    from ..ai import get_engine
    user = {"id": owner, "name": "", "role": "admin"}
    env = get_engine().run("image.lesion_classification_research", {"data": data, "model_id": params["model_id"]}, user=user, request_id=ctx["request_id"], allow_research=True)
    if env.status in ("unavailable", "forbidden", "error", "timeout", "cancelled", "resource_exhausted", "unsupported"):
        raise Public(env.message or "The research model could not be run.")
    out = env.result or {"status": "rejected_input", "problems": [env.message]}
    db.audit(owner, "research.predict", params["model_id"], {"status": out.get("status"), "request_id": ctx["request_id"], "engine_status": env.status})
    return ("rejected" if out.get("status") == "rejected_input" else "uncertain" if out.get("status") == "abstained" else "completed"), out


jobs.register("research_predict", h_research)
jobs.register("image_check", h_image)
jobs.register("document_process", h_document)
jobs.register("speech_transcribe", h_stt)
jobs.register("assistant_answer", h_answer)


# ---------------- API ----------------
def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def _submit(request, task, user, params, data, idem):
    try:
        job, created = jobs.submit(task, user["id"], params, data, _rid(request), idem)
    except jobs.QueueFull as e:
        return JSONResponse({"detail": str(e)}, status_code=429, headers={"Retry-After": str(e.retry_after)})
    return JSONResponse({**job, "status_url": f"/api/v1/jobs/{job['id']}", "events_url": f"/api/v1/jobs/{job['id']}/events", "idempotent_replay": not created},
                        status_code=202 if created else 200, headers={"Location": f"/api/v1/jobs/{job['id']}"})


def _read(f: UploadFile) -> bytes:
    data = f.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"This file is too large. The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return data


@router.post("/images/check")
def images_check(request: Request, region: str = Form(...), file: UploadFile = File(...), idempotency_key: str | None = Header(default=None),
                 user=Depends(consent.patient_consented)):
    if region not in suitability.REGIONS:
        raise HTTPException(400, "Please choose face / skin, hair / scalp, or body.")
    return _submit(request, "image_check", user, {"region": region}, _read(file), idempotency_key)


@router.post("/research/predict")
def research_predict(request: Request, model_id: str = Form(...), file: UploadFile = File(...), idempotency_key: str | None = Header(default=None),
                     user=Depends(require("admin"))):
    """Admin / ML-engineer only. Research inference on a registered model; never exposed to patients or clinicians."""
    return _submit(request, "research_predict", user, {"model_id": model_id}, _read(file), idempotency_key)


@router.post("/documents")
def documents_submit(request: Request, file: UploadFile = File(...), category: str = Form(""), idempotency_key: str | None = Header(default=None),
                     user=Depends(consent.patient_consented)):
    data = _read(file)
    try:
        pipeline.validate(data, file.filename or "upload")      # cheap validation up front: fail fast with a clear message
    except pipeline.DocumentError as e:
        raise HTTPException(422, str(e))
    return _submit(request, "document_process", user, {"filename": file.filename or "upload", "category": category}, data, idempotency_key)


@router.post("/speech/transcribe")
def speech_submit(request: Request, language: str | None = Form(None), file: UploadFile = File(...), idempotency_key: str | None = Header(default=None),
                  user=Depends(consent.patient_consented)):
    if not speech.stt_status()["available"]:
        raise HTTPException(503, "Voice questions are not available. Please type your question.")
    return _submit(request, "speech_transcribe", user, {"language": language}, _read(file), idempotency_key)


@router.post("/assistant/answer")
def answer_submit(request: Request, body: dict, idempotency_key: str | None = Header(default=None), user=Depends(consent.patient_consented)):
    text = str(body.get("text", "")).strip()
    if not text or len(text) > 1000 or not body.get("conversation_id"):
        raise HTTPException(422, "A conversation_id and a question of 1-1000 characters are required.")
    return _submit(request, "assistant_answer", user, {"conversation_id": body["conversation_id"], "text": text}, None, idempotency_key)


@router.get("/jobs/{job_id}")
def job_get(job_id: str, user=Depends(current_user)):
    j = jobs.get(job_id, user["id"])
    if not j:
        raise HTTPException(404, "Job not found.")
    return j


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: str, user=Depends(current_user)):
    j = jobs.cancel(job_id, user["id"])
    if not j:
        raise HTTPException(404, "Job not found.")
    return j


@router.get("/jobs/{job_id}/events")
def job_events(job_id: str, user=Depends(current_user)):
    """Server-sent events: one message per real state change until the job is terminal."""
    if not jobs.get(job_id, user["id"]):
        raise HTTPException(404, "Job not found.")

    def gen():
        last = None
        deadline = time.time() + 600
        while time.time() < deadline:
            j = jobs.get(job_id, user["id"])
            if j and j["status"] != last:
                last = j["status"]
                yield f"event: status\ndata: {json.dumps(j)}\n\n"
            if j and j["status"] in jobs.TERMINAL:
                return
            time.sleep(0.3)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ---------------- health / readiness / metrics ----------------
def _component(name, fn):
    t = time.time()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, type(e).__name__
    return {"name": name, "ready": bool(ok), "detail": detail, "check_ms": round((time.time() - t) * 1000)}


@router.get("/health/live")
def live():
    return {"status": "alive"}


@router.get("/health/ready")
def ready():
    from ..assistant import llm, retrieval
    def dbc():
        return db.q1("SELECT 1 x") is not None, "sqlite reachable"
    def store():
        from .. import storage
        return storage.healthy()
    def ocr():
        return pipeline.ocr_available(), f"tesseract languages: {pipeline.ocr_languages()}" if pipeline.ocr_available() else "tesseract not installed"
    def stt():
        s = speech.stt_status()
        return s["available"], s.get("adapter") or s.get("admin_message", "")
    def tts():
        s = speech.get_tts().status()
        return s["available"], f"{len(s.get('voices', []))} offline voices"
    def emb():
        e = retrieval.SentenceTransformerEmbedder()
        return e.available, e.name if e.available else "embedding model not downloaded (lexical retrieval fallback)"
    def lm():
        b = llm.get_backend()
        return b is not None, b.name if b else "not enabled (assistant is extractive)"
    core_ok = [_component("database", dbc), _component("private_storage", store)]
    optional = [_component("ocr", ocr), _component("speech_to_text", stt), _component("text_to_speech", tts), _component("embeddings", emb), _component("llm", lm)]
    ok = all(c["ready"] for c in core_ok)
    with db.context.as_system():
        queue = {k: v for k, v in jobs.stats(1).items() if k in ("queued_now", "processing_now", "workers", "queue_limit")}
    return JSONResponse({"status": "ready" if ok else "not_ready", "degraded": [c["name"] for c in optional if not c["ready"]], "core": core_ok, "optional": optional,
                         "queue": queue}, status_code=200 if ok else 503)


@router.get("/metrics")
def metrics(user=Depends(require("admin"))):
    return jobs.stats(24)

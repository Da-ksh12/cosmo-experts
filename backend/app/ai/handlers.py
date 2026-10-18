"""Handlers that connect the engine to the capabilities that already exist in the application. Each wraps an existing module; none duplicates its logic.
(Handlers for knowledge search, structured documents, the agent and the other capabilities are registered by their own modules as they are built.)"""
from __future__ import annotations

from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field

from ..config import MAX_UPLOAD_BYTES
from .engine import AIEngine, HandlerContext, HandlerResult
from .errors import Forbidden, RejectedInput, Unavailable
from .schemas import ModelRef


class ImagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: bytes
    category: str = "other"


class ResearchImagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: bytes
    model_id: str = Field(min_length=8, max_length=64)


class DocumentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: bytes
    filename: str = Field(default="upload", max_length=200)


class AudioPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio: bytes
    language: str | None = Field(default=None, max_length=8)


class SpeechTextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=5000)
    language: str = Field(default="en", max_length=8)
    rate: float = Field(default=1.0, ge=0.5, le=2.0)


class KnowledgePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=3, ge=1, le=10)


class ExplainPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=64)
    lang: str = Field(default="en", pattern="^(en|hi|hinglish)$")


def _typed(model: type[BaseModel], payload) -> BaseModel:
    if isinstance(payload, model):
        return payload
    try:
        return model.model_validate(payload)
    except Exception:
        raise RejectedInput("The request was not in the expected form.") from None


def _bytes_ok(b: bytes, what: str) -> None:
    if not b:
        raise RejectedInput(f"The {what} is empty.")
    if len(b) > MAX_UPLOAD_BYTES:
        raise RejectedInput(f"The {what} is too large. The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")


# ---------------------------------------------------------------- image.quality
def validate_image(payload) -> None:
    from ..imaging import quality
    p = _typed(ImagePayload, payload)
    _bytes_ok(p.data, "image")
    if p.category not in quality.CATEGORIES:
        raise RejectedInput("Unknown photo area.")


def h_image_quality(payload, ctx: HandlerContext) -> HandlerResult:
    from ..imaging import quality
    p = _typed(ImagePayload, payload)
    ctx.check()
    qa = quality.assess(p.data, p.category)
    return HandlerResult("ok", qa.to_dict(), qa.message, qa.warnings, processing_version=qa.version + "+" + str(qa.legacy.get("version", "")))


# ---------------------------------------------------------------- document.extract
def validate_document(payload) -> None:
    from ..documents import pipeline
    p = _typed(DocumentPayload, payload)
    try:
        pipeline.validate(p.data, p.filename)
    except pipeline.DocumentError as e:
        raise RejectedInput(str(e)) from None


def h_document_extract(payload, ctx: HandlerContext) -> HandlerResult:
    from ..documents import pipeline
    p = _typed(DocumentPayload, payload)
    try:
        res = pipeline.process(p.data, p.filename)
    except pipeline.DocumentError as e:
        raise RejectedInput(str(e)) from None
    result = {"doc_type": res.doc_type, "doc_date": res.doc_date, "page_count": len(res.pages), "readable": res.readable, "language": res.language,
              "pages": [{"page_no": pg.page_no, "method": pg.method, "confidence": pg.confidence, "characters": len(pg.text), "script": pg.script} for pg in res.pages],
              "fields": [asdict(f) for f in res.fields], "facts": [asdict(f) for f in res.facts], "medications": [m.to_dict() for m in res.medications], "needs_review": res.needs_review}
    return HandlerResult("review_required", result, "", res.warnings, processing_version=res.version)


def h_document_medications(payload, ctx: HandlerContext) -> HandlerResult:
    from ..documents import medication, pipeline
    p = _typed(DocumentPayload, payload)
    try:
        res = pipeline.process(p.data, p.filename, cancel=ctx.cancel)
    except pipeline.DocumentError as e:
        raise RejectedInput(str(e)) from None
    meds = [m.to_dict() for m in res.medications]
    warns = list(res.warnings) + ([] if meds else ["No medicine entries were found in this document."])
    return HandlerResult("review_required", {"doc_type": res.doc_type, "medications": meds, "readable": res.readable, "needs_review": True}, "", warns, processing_version=medication.VERSION)


def h_hindi_ocr(payload, ctx: HandlerContext) -> HandlerResult:
    """Research use only: recognition with the Hindi pack added. Not evaluated on real documents."""
    from ..documents import pipeline
    p = _typed(DocumentPayload, payload)
    token = pipeline.OCR_OVERRIDE.set(["eng", "hin"])
    try:
        res = pipeline.process(p.data, p.filename, cancel=ctx.cancel)
    except pipeline.DocumentError as e:
        raise RejectedInput(str(e)) from None
    finally:
        pipeline.OCR_OVERRIDE.reset(token)
    return HandlerResult("review_required", {"language": res.language, "pages": [{"page_no": pg.page_no, "method": pg.method, "confidence": pg.confidence, "script": pg.script, "text": pg.text} for pg in res.pages],
                                              "evaluation": "Hindi recognition has not been evaluated on real documents; treat the text as unverified."}, "", res.warnings, labels=["research_only"],
                         processing_version=res.version)


# ---------------------------------------------------------------- knowledge.search / document.explain
ABSTAIN_TEXT = {"insufficient_evidence": None, "unsupported_language": "The approved information is available in English only, so this question cannot be matched.",
                "empty_query": "Please ask a longer question.", "no_approved_content": "There is no approved information to search yet."}


def validate_knowledge(payload) -> None:
    _typed(KnowledgePayload, payload)


def h_knowledge_search(payload, ctx: HandlerContext) -> HandlerResult:
    """Approved, unexpired articles only; every hit carries a passage-level citation. The caller's identity decides how much of the review record is shown."""
    from . import knowledge, safety
    import hashlib
    p = _typed(KnowledgePayload, payload)
    ctx.check()
    staff = (ctx.user or {}).get("role") in ("doctor", "admin")
    res = knowledge.search(p.query, k=p.k)
    ctx.check()
    out = res.to_dict(staff=staff)
    if not res.answered:
        return HandlerResult("abstained", out, ABSTAIN_TEXT.get(res.reason) or safety.ABSTAIN_MSG, processing_version=knowledge.VERSION)
    ref = "approved_kb:" + hashlib.sha256("|".join(sorted(h.passage.id for h in res.hits)).encode()).hexdigest()[:12]
    return HandlerResult("ok", out, "", evidence=[h.passage.text for h in res.hits], dataset_ref=ref, processing_version=knowledge.VERSION, labels=["approved_reference"])


def validate_explain(payload) -> None:
    _typed(ExplainPayload, payload)


def h_document_explain(payload, ctx: HandlerContext) -> HandlerResult:
    """The same service as GET /api/documents/{id}/explanation. The person comes from the authenticated context, never from the payload; a document the caller may not open is reported
    exactly like one that does not exist."""
    from ..services import explanations
    p = _typed(ExplainPayload, payload)
    if not ctx.user or not ctx.user.get("id"):
        raise Forbidden()
    ctx.check()
    try:
        out = explanations.build(ctx.user, p.document_id, p.lang)
    except explanations.NotFound:
        raise RejectedInput("Document not found.") from None
    ctx.check()
    evid = [i["citation"]["excerpt"] for s in out["sections"] for i in s["items"] if i.get("citation")]
    return HandlerResult("abstained" if out.get("abstained") else "review_required", out, "", processing_version=out.get("version"), evidence=evid)


# ---------------------------------------------------------------- image.lesion_classification_research
def validate_research(payload) -> None:
    p = _typed(ResearchImagePayload, payload)
    _bytes_ok(p.data, "image")


def h_research_image(payload, ctx: HandlerContext) -> HandlerResult:
    from ..training import serving
    p = _typed(ResearchImagePayload, payload)
    try:
        out = serving.predict(p.model_id, p.data, ctx.request_id)
    except serving.ServingError as e:
        raise Unavailable(e.public_message) from None
    card_info = out.get("model") or {}
    ref = ModelRef(model_id=p.model_id, task="lesion_classification_research", lifecycle="research", architecture=str(card_info.get("architecture", ""))[:80],
                   artifact_sha256_prefix=str(card_info.get("artifact_sha256", ""))[:16])
    status = {"predicted": "ok", "abstained": "abstained", "rejected_input": "rejected_input"}.get(out["status"], "error")
    return HandlerResult(status, out, "", out.get("warnings", []), model=ref, dataset_ref=card_info.get("dataset"), model_derived=True, labels=["research_only"])


# ---------------------------------------------------------------- speech
def validate_audio(payload) -> None:
    p = _typed(AudioPayload, payload)
    _bytes_ok(p.audio, "recording")


def h_transcribe(payload, ctx: HandlerContext) -> HandlerResult:
    from ..speech import adapters
    p = _typed(AudioPayload, payload)
    try:
        text = adapters.transcribe(p.audio, p.language)
    except adapters.AudioError as e:
        raise RejectedInput(str(e)) from None
    return HandlerResult("review_required", {"text": text, "needs_confirmation": True})


def validate_speech_text(payload) -> None:
    _typed(SpeechTextPayload, payload)


def h_synthesize(payload, ctx: HandlerContext) -> HandlerResult:
    from ..speech import adapters
    p = _typed(SpeechTextPayload, payload)
    try:
        audio = adapters.get_tts().synthesize(p.text, p.language, p.rate)
    except RuntimeError as e:                              # "No installed offline voice supports 'hi'." and similar: honest, specific, safe
        raise Unavailable(str(e)) from None
    return HandlerResult("ok", {"mime": "audio/wav", "bytes": len(audio), "language": p.language}, artifacts={"audio": audio})


def register_all(engine: AIEngine) -> None:
    engine.register("image.quality", h_image_quality, validate_image)
    engine.register("document.extract", h_document_extract, validate_document)
    engine.register("document.medications", h_document_medications, validate_document)
    engine.register("document.ocr.hindi", h_hindi_ocr, validate_document)
    engine.register("document.explain", h_document_explain, validate_explain)
    engine.register("knowledge.search", h_knowledge_search, validate_knowledge)
    engine.register("image.lesion_classification_research", h_research_image, validate_research)
    engine.register("speech.transcribe", h_transcribe, validate_audio)
    engine.register("speech.synthesize", h_synthesize, validate_speech_text)

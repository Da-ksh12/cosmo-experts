"""Capability registry: what the system can do, whether it can do it right now, and who may see the result.

Capabilities are DEFINED here once (image tasks are generated from the existing `training/tasks.py` so there is a single source of truth) and RESOLVED on demand against reality:
installed tools and language packs, model files, approved knowledge, and the model lifecycle. A capability that has no implementation or no validated model resolves to
`unavailable`; the engine then answers honestly and never substitutes a different model or a mock.

Maturity of a definition:
  deterministic - rules or classical computer vision; makes no learned claim (output may still need clinician review)
  learned_model - needs a registered model; available to patients only after an approved lifecycle state; research models are administrator-only
  planned       - no implementation yet; always unavailable"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .. import status as st
from ..training import tasks as image_tasks
from . import lifecycle
from .schemas import CapabilityState, Dependency, Limits


@dataclass(frozen=True)
class CapabilityDef:
    id: str
    kind: str                                  # image | document | knowledge | speech | agent | llm | translation
    name: str
    version: str
    description: str
    maturity: str                              # deterministic | learned_model | planned
    audiences: tuple[str, ...] = ("patient", "clinician", "admin")
    limits: Limits = field(default_factory=Limits)
    requires: tuple[str, ...] = ()             # dependencies without which it cannot run
    optional: tuple[str, ...] = ()             # dependencies whose absence only degrades it
    model_task: str | None = None
    review_required: bool = False              # output is only ever a draft for clinician review
    experimental: bool = False                 # deterministic but not validated: resolves to research_only (never patient-facing)
    patient_if_approved: bool = False          # a learned model may reach patients once approved (research-only tasks never)
    limitations: tuple[str, ...] = ()
    unavailable_reason: str = ""


# ---------------------------------------------------------------- dependency checks (cached briefly: some spawn processes or touch the OS speech engine)
_TTL = {"tts": 600.0, "ocr_langs": 300.0}
_cache: dict[str, tuple[float, tuple[bool, str]]] = {}
_overrides: dict[str, tuple[bool, str]] = {}
_lock = threading.Lock()


def _cached(key: str, ttl: float, fn: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        val = fn()
    except Exception as e:                                           # a failing probe means "not available", never a crash
        val = (False, f"check failed ({type(e).__name__})")
    with _lock:
        _cache[key] = (now + ttl, val)
    return val


def invalidate() -> None:
    with _lock:
        _cache.clear()


def set_override(dep_id: str, value: tuple[bool, str] | None) -> None:
    """Test hook: pin a dependency result."""
    with _lock:
        if value is None:
            _overrides.pop(dep_id, None)
        else:
            _overrides[dep_id] = value


def _ocr_langs() -> list[str]:
    from ..documents import pipeline
    return pipeline.ocr_languages()


def _voice_tags() -> set[str]:
    from ..speech import adapters
    tts = adapters.get_tts()
    voices = tts.voices() if hasattr(tts, "voices") else tts.status().get("voices", [])
    tags = set()
    for v in voices:
        for l in v.get("languages", []):
            tags.add(str(l).replace("_", "-").split("-")[0].lower())
        n = v.get("name", "").lower()
        for code, word in (("en", "english"), ("hi", "hindi")):
            if word in n:
                tags.add(code)
    return tags


def check_dependency(dep_id: str) -> Dependency:
    with _lock:
        ov = _overrides.get(dep_id)
    if ov is not None:
        return Dependency(id=dep_id, ok=ov[0], detail=ov[1])
    kind, _, arg = dep_id.partition(":")
    if kind == "tesseract":
        from ..documents import pipeline
        ok, detail = _cached("tesseract", 60.0, lambda: (pipeline.ocr_available(), "Tesseract OCR is installed" if pipeline.ocr_available() else "Tesseract OCR is not installed"))
    elif kind == "ocr_lang":
        ok, detail = _cached(f"ocr_lang:{arg}", _TTL["ocr_langs"], lambda: ((arg in _ocr_langs()), f"OCR language pack '{arg}' " + ("is installed" if arg in _ocr_langs() else "is not installed")))
    elif kind == "embed_model":
        from ..assistant.retrieval import SentenceTransformerEmbedder
        ok, detail = _cached("embed", 60.0, lambda: ((a := SentenceTransformerEmbedder().available), "dense embedding model present" if a else "dense embedding model absent (lexical retrieval only)"))
    elif kind == "whisper":
        from ..speech import adapters
        ok, detail = _cached("whisper", 60.0, lambda: ((s := adapters.stt_status())["available"], "local speech recognition model present" if s["available"] else "local speech recognition is not installed"))
    elif kind == "tts":
        ok, detail = _cached(f"tts:{arg}", _TTL["tts"], lambda: _tts_probe(arg))
    elif kind == "llm_backend":
        from ..assistant.llm import get_backend
        ok, detail = _cached("llm", 30.0, lambda: ((b := get_backend() is not None), "a local language-model backend is configured" if b else "no language-model backend is configured"))
    elif kind == "approved_kb":
        from .. import clinical_content, db

        def kb():
            with db.context.as_system():
                n = len(clinical_content.approved_articles())
            return n > 0, f"{n} approved knowledge article(s)" if n else "no approved knowledge articles yet"
        ok, detail = _cached("kb", 15.0, kb)
    else:
        ok, detail = False, "unknown dependency"
    return Dependency(id=dep_id, ok=bool(ok), detail=detail)


def _tts_probe(lang: str) -> tuple[bool, str]:
    tags = _voice_tags()
    if lang in ("", "any"):
        return bool(tags), "an offline voice is installed" if tags else "no offline text-to-speech voice is installed"
    return lang in tags, f"an installed voice supports '{lang}'" if lang in tags else f"no installed voice supports '{lang}'"


# ---------------------------------------------------------------- definitions
_DOC_LIMITS = Limits(max_input_bytes=None, max_pages=None, max_seconds=90.0, concurrency=1)


def _image_defs() -> list[CapabilityDef]:
    out = [CapabilityDef(
        "image.quality", "image", "Photo quality check", "1.1.0",
        "Tells the person whether a photo is usable for the selected area (focus, light, size, framing, compression, subject). It does not assess any condition.",
        "deterministic", limits=Limits(max_seconds=20.0, concurrency=2),
        limitations=("Quality rules for faces and hair/scalp were not validated on real photographs; they produce guidance, not clinical findings.",
                     "A successful check is not a diagnosis."))]
    for t in image_tasks.TASKS:
        if t["id"] == "image_suitability":
            continue
        research = t["declared_status"] == "research_only"
        out.append(CapabilityDef(
            f"image.{t['id']}", "image", t["name"], t["version"], t["intended_use"], "learned_model",
            audiences=("admin",) if research else ("clinician", "admin"), limits=Limits(max_seconds=120.0 if research else 45.0, concurrency=1),
            model_task=t["id"], patient_if_approved=not research and t["id"] != "other_uncertain",
            limitations=tuple(t["limitations"]), unavailable_reason=f"Required data: {t['required_dataset']}"))
    return out


DEFS: list[CapabilityDef] = _image_defs() + [
    CapabilityDef("document.extract", "document", "Document text and lab-result extraction", "docpipe-1.0.0",
                  "Reads PDFs, photos of documents, Word and text files; extracts printed lab results with source text, page and confidence. Everything extracted is a draft for clinician review.",
                  "deterministic", limits=_DOC_LIMITS, optional=("tesseract",), review_required=True,
                  limitations=("Scanned pages need OCR (Tesseract).", "Values are copied as printed; nothing is converted or interpreted.")),
    CapabilityDef("document.medications", "document", "Prescription field extraction", "medparse-1.0.0",
                  "Rule-based extraction of printed medicine entries (name, strength, form, dose, route, frequency, duration, instructions) with the original text and ambiguity flags.",
                  "deterministic", limits=_DOC_LIMITS, optional=("tesseract",), review_required=True,
                  limitations=("Never recommends starting, stopping or changing a medicine.", "No brand/generic substitution.", "Evaluated on synthetic fixtures only.")),
    CapabilityDef("document.ocr.hindi", "document", "Hindi text recognition", "0.0.0",
                  "Reads Hindi (Devanagari) printed text when the Tesseract Hindi language pack is installed.", "deterministic", limits=_DOC_LIMITS,
                  requires=("tesseract", "ocr_lang:hin"), review_required=True, experimental=True,
                  limitations=("Not evaluated on real documents: not claimed to be clinically reliable.",)),
    CapabilityDef("document.explain", "document", "Report explanation", "explain-2.0.0",
                  "Explains what a report prints, with citations to the page and text, and approved references where they exist. Abstains when evidence is insufficient.",
                  "deterministic", limits=Limits(max_seconds=30.0), optional=("approved_kb",), review_required=True,
                  limitations=("Does not interpret values beyond what the report or an approved reference states.", "Hindi and Hinglish templates are unreviewed.")),
    CapabilityDef("knowledge.search", "knowledge", "Approved knowledge search", "retrieval-2.0.0",
                  "Finds passages in clinician-approved, unexpired articles, with passage-level citations. Abstains below the calibrated threshold.",
                  "deterministic", limits=Limits(max_seconds=20.0, concurrency=2), requires=("approved_kb",), optional=("embed_model",),
                  limitations=("Only approved and unexpired content is searched.", "Without the dense embedding model retrieval falls back to lexical scoring with a different calibration.")),
    CapabilityDef("agent.chat", "agent", "Assistant with tools", "agent-1.0.0",
                  "Answers from the person's own reports and approved knowledge, using audited tools with server-side permissions. Cannot diagnose or advise on medicines.",
                  "deterministic", audiences=("patient",), limits=Limits(max_seconds=60.0, concurrency=2), review_required=True,
                  limitations=("Rule-based planner; a language-model planner is disabled until it passes its evaluation gates.",)),
    CapabilityDef("llm.generate", "llm", "Language-model drafting", "0.0.0",
                  "Drafts claims that are then verified against their cited sources.", "learned_model", audiences=("admin",), limits=Limits(max_seconds=120.0),
                  requires=("llm_backend",), model_task="llm_assistant",
                  limitations=("The evaluated local model (Qwen2.5-1.5B) failed the acceptance criteria and stays disabled for patients.",),
                  unavailable_reason="No language model has passed the evaluation gates."),
    CapabilityDef("speech.transcribe", "speech", "Speech to text", "stt-2.0.0",
                  "Local speech recognition (faster-whisper). The person confirms the transcript before it is used.", "deterministic",
                  limits=Limits(max_input_bytes=10 * 1024 * 1024, max_seconds=90.0, concurrency=1), requires=("whisper",), review_required=True,
                  limitations=("English verified on synthetic speech only; Hindi/Hinglish not verified.",)),
    CapabilityDef("speech.synthesize", "speech", "Text to speech", "tts-2.0.0",
                  "Reads visible text aloud with an installed offline voice that supports the language; never uses a mismatched voice.", "deterministic",
                  limits=Limits(max_seconds=60.0, concurrency=1), requires=("tts:any",), optional=("tts:hi",),
                  limitations=("A Hindi voice is only used if one is installed.",)),
    CapabilityDef("translation.text", "translation", "Translation", "0.0.0", "Translate text between languages.", "planned",
                  unavailable_reason="No reviewed translation engine exists; hand-written Hindi/Hinglish templates are unreviewed."),
]
BY_ID = {d.id: d for d in DEFS}


def get(capability_id: str) -> CapabilityDef | None:
    return BY_ID.get(capability_id)


# ---------------------------------------------------------------- resolution
def _status_vocabulary(availability: str, approval: str) -> str:
    if availability == "unavailable":
        return "unavailable"
    if approval == "research_only":
        return "research_only"
    if approval == "review_required":
        return "clinician_review_required"
    if approval == "approved_for_defined_use":
        return st.effective_status("approved_for_defined_use", st.Approval("lifecycle", "defined use", {r: True for r in st.RELEASE_REQUIREMENTS}))
    return "available"


def resolve(d: CapabilityDef) -> CapabilityState:
    deps = [check_dependency(x) for x in (*d.requires, *d.optional)]
    req_missing = [x for x in deps if x.id in d.requires and not x.ok]
    opt_missing = [x for x in deps if x.id in d.optional and not x.ok]
    reasons: list[str] = []
    model = None
    audiences = list(d.audiences)
    if d.maturity == "planned":
        availability, approval = "unavailable", "none"
        reasons.append(d.unavailable_reason or "No implementation exists yet.")
    elif d.maturity == "deterministic":
        approval = "research_only" if d.experimental else "review_required" if d.review_required else "not_required"
        if req_missing:
            availability = "unavailable"
            reasons += [x.detail for x in req_missing]
        elif opt_missing:
            availability = "degraded"
            reasons += [x.detail for x in opt_missing]
        else:
            availability = "available"
    else:                                                              # learned_model
        active = lifecycle.active_approved(d.model_task)
        research = [] if active else lifecycle.research_models(d.model_task)
        if active:
            model, approval = active, "approved_for_defined_use"
            availability = "unavailable" if req_missing else "available"
            if d.patient_if_approved and "patient" not in audiences:
                audiences.append("patient")
        elif research:
            model, approval = research[0], "research_only"
            availability = "unavailable" if req_missing else "available"
            reasons.append("Registered models are research-only: none has passed its release gates and approval.")
        else:
            availability, approval = "unavailable", "none"
            reasons.append("No validated model exists for this task.")
            if d.unavailable_reason:
                reasons.append(d.unavailable_reason)
        reasons += [x.detail for x in req_missing]
    patient_facing = availability == "available" and "patient" in audiences and approval in ("not_required", "review_required", "approved_for_defined_use")
    return CapabilityState(id=d.id, kind=d.kind, name=d.name, version=d.version, description=d.description, availability=availability, approval=approval,
                           status=_status_vocabulary(availability, approval), patient_facing=patient_facing, audiences=audiences, reasons=reasons, dependencies=deps,
                           model=model, limits=d.limits, limitations=list(d.limitations))


def resolve_all() -> list[CapabilityState]:
    return [resolve(d) for d in DEFS]

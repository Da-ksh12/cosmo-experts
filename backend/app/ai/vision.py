"""Image routing: which analysis, if any, a photo of a given category may receive.

Recognising a category does NOT mean a validated model exists for it. This module therefore (1) checks image quality first, (2) lists the tasks that APPLY to the category (an
acne task never receives a hair photo; a lesion model never receives a face), (3) resolves each task's real state from the capability registry, and (4) runs only tasks that are
available to this caller: patients and clinicians only ever reach APPROVED, ACTIVE models; a research model runs only for an administrator who asked for research explicitly and named
the model. When nothing may run the answer is an honest clinician-review route, never a guess.

Routes:  rejected (unusable photo) | document_pipeline (looks like a report) | image_models (at least one task ran) | clinician_review (no automated assessment exists/allowed)"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from . import safety
from .engine import get_engine
from .errors import Unsupported
from .schemas import Strict


@dataclass(frozen=True)
class CategorySpec:
    id: str
    label: str
    tasks: tuple[str, ...]               # image task ids (tasks.py) that apply to this category
    route: str = "vision"                # vision | document
    research_only: bool = False          # the only tasks are research models: never offered to patients


CATEGORY_SPECS: dict[str, CategorySpec] = {s.id: s for s in (
    CategorySpec("face", "Face", ("acne_features", "pigmentation", "redness_irritation", "dryness_scaling", "texture_scarring")),
    CategorySpec("hair_scalp", "Hair and scalp", ("hair_thinning", "scalp_findings")),
    CategorySpec("body", "Body skin area", ("dryness_scaling",)),
    CategorySpec("other", "Other skin area", ()),
    CategorySpec("skin_lesion", "Skin lesion close-up", ("lesion_classification_research",), research_only=True),
    CategorySpec("medical_report_image", "Photo of a medical report", (), route="document"),
)}


class TaskCandidate(Strict):
    task: str
    capability: str
    name: str
    available: bool
    status: str                           # status.py vocabulary
    runnable_by_caller: bool
    reason: str = ""


class TaskOutcome(Strict):
    task: str
    capability: str
    status: str
    message: str = ""
    result: dict[str, Any] | None = None
    labels: list[str] = Field(default_factory=list)


class ImageAnalysis(Strict):
    request_id: str
    category: str
    category_label: str
    route: Literal["rejected", "document_pipeline", "image_models", "clinician_review"]
    message: str
    quality: dict[str, Any] | None = None
    candidates: list[TaskCandidate] = Field(default_factory=list)
    outcomes: list[TaskOutcome] = Field(default_factory=list)
    clinician_review: bool = True
    disclaimers: list[str] = Field(default_factory=list)


def categories(user: dict | None = None) -> list[dict]:
    """Categories with the honest availability of every task that applies to them (what a screen may show)."""
    eng = get_engine()
    out = []
    for spec in CATEGORY_SPECS.values():
        if spec.research_only and (user or {}).get("role") not in ("admin",):
            continue
        tasks = []
        for t in spec.tasks:
            s = eng.describe(f"image.{t}")
            tasks.append({"task": t, "name": s.name, "available": s.patient_facing if (user or {}).get("role") == "patient" else s.availability == "available", "status": s.status})
        out.append({"id": spec.id, "label": spec.label, "route": spec.route, "tasks": tasks})
    return out


def _runnable(state, role: str, allow_research: bool) -> tuple[bool, str]:
    if state.availability != "available":
        return False, "No validated model exists for this task." if state.approval == "none" else "Not available right now."
    if state.approval == "approved_for_defined_use":
        aud = {"patient": "patient", "doctor": "clinician", "admin": "admin"}.get(role)
        return aud in state.audiences, "" if aud in state.audiences else "Not available for this account."
    if state.approval == "research_only":
        return (role == "admin" and allow_research), "Research-only model: administrators only, and only when research use is requested explicitly."
    return False, "Not approved for use."


def analyze(data: bytes, category: str, *, user: dict, task: str | None = None, model_id: str | None = None, allow_research: bool = False, request_id: str = "") -> ImageAnalysis:
    spec = CATEGORY_SPECS.get(category)
    if spec is None:
        raise Unsupported("This photo category is not supported.")
    if task is not None and task not in spec.tasks:
        raise Unsupported(f"The task '{task}' does not apply to the category '{spec.label}'.")      # an unrelated model is never invoked
    eng = get_engine()
    role = user.get("role", "patient")
    q = eng.run("image.quality", {"data": data, "category": category}, user=user, request_id=request_id)
    rid = q.request_id
    base = dict(request_id=rid, category=category, category_label=spec.label)
    if q.status != "ok" or q.result is None:
        route = "rejected" if q.status == "rejected_input" else "clinician_review"
        return ImageAnalysis(route=route, message=q.message or safety.UNAVAILABLE_MSG, **base)
    quality = q.result
    # A page of text is recognised BEFORE a skin-photo rejection: a report photographed under the wrong category is not a bad skin photo, it is a document.
    if quality["reason"] != "invalid_image" and (spec.route == "document" or quality.get("document_like")):
        return ImageAnalysis(route="document_pipeline", quality=quality, clinician_review=False,
                             message="This looks like a document. Please upload it under Reports so it can be read and checked; no image model is used for documents.",
                             disclaimers=[safety.NOT_DIAGNOSIS], **base)
    if quality["verdict"] == "not_usable":
        return ImageAnalysis(route="rejected", message=quality["message"], quality=quality, disclaimers=[safety.NOT_DIAGNOSIS], **base)
    candidates = []
    for t in spec.tasks:
        st = eng.describe(f"image.{t}")
        ok, why = _runnable(st, role, allow_research)
        candidates.append(TaskCandidate(task=t, capability=st.id, name=st.name, available=st.availability == "available", status=st.status, runnable_by_caller=ok, reason=why if not ok else ""))
    to_run = [c for c in candidates if c.runnable_by_caller and (task is None or c.task == task)]
    if task is not None and not to_run:
        c = next(c for c in candidates if c.task == task)
        return ImageAnalysis(route="clinician_review", quality=quality, candidates=candidates, message=c.reason or safety.UNAVAILABLE_MSG + " A clinician reviews the photo.",
                             disclaimers=[safety.NOT_DIAGNOSIS], **base)
    if task is None:                                                  # automatic routing: approved models only; research models never run without being named
        to_run = [c for c in to_run if c.status == "approved_for_defined_use"]
    outcomes: list[TaskOutcome] = []
    for c in to_run:
        payload: Any = {"data": data}
        if c.task == "lesion_classification_research":
            payload["model_id"] = model_id or ""
        env = eng.run(c.capability, payload, user=user, request_id=rid, allow_research=allow_research)
        outcomes.append(TaskOutcome(task=c.task, capability=c.capability, status=env.status, message=env.message, result=env.result, labels=env.labels))
    ran = [o for o in outcomes if o.status in ("ok", "abstained")]
    if not ran:
        return ImageAnalysis(route="clinician_review", quality=quality, candidates=candidates, outcomes=outcomes,
                             message="No automated assessment is available for this kind of photo. A clinician reviews your photo; nothing is diagnosed by this app.",
                             disclaimers=[safety.NOT_DIAGNOSIS, safety.REVIEW_NOTE], **base)
    return ImageAnalysis(route="image_models", quality=quality, candidates=candidates, outcomes=outcomes, message="Assessment complete. Results are for clinician review.",
                         disclaimers=[safety.NOT_DIAGNOSIS, safety.REVIEW_NOTE] + ([safety.EXPERIMENTAL_NOTE] if any("research_only" in o.labels for o in ran) else []), **base)

"""The unified AI engine: one place that runs every AI capability with the same rules.

For each run: look the capability up (unknown -> `unsupported`), resolve its REAL state (unavailable -> `unavailable`, never a substitute or a mock), check the caller's audience
(research models: administrators only, and only when the route asked for research), validate and bound the input, run the handler in a bounded worker with a timeout and a
cancel token, review the output with the shared safety policy, and wrap it in an envelope that records which capability, model, code revision and device produced it.

Separation of concerns: handlers do the work (and know nothing about HTTP or permissions), the engine owns availability, limits, safety and provenance, the routers own
transport. A handler that raises, returns nothing, or returns something that is not a HandlerResult yields an `error` envelope: the engine never invents an output."""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..redact import safe_exc
from . import provenance, registry, safety
from .errors import AIError, Forbidden, RejectedInput, Unavailable
from .resources import CancelToken, DeviceInfo, ResourceManager, resource_manager, select_device
from .schemas import CapabilityState, ModelRef, PatientCapabilityView, Provenance, RunEnvelope, SafetyInfo, StaffCapabilityView, Timings

log = logging.getLogger("cosmo.ai")

ROLE_AUDIENCE = {"patient": "patient", "doctor": "clinician", "admin": "admin"}
TEXT_KEYS = ("message", "text", "answer", "explanation", "summary")
SAFETY_KIND = {"image": "image_assessment", "document": "extraction", "speech": "extraction", "knowledge": "education", "agent": "education", "llm": "education", "translation": "education"}


@dataclass
class HandlerContext:
    request_id: str
    user: dict | None
    cancel: CancelToken
    capability: CapabilityState
    device: DeviceInfo
    started: float = field(default_factory=time.monotonic)

    def check(self) -> None:
        """Handlers call this between units of work so a timeout or cancellation actually stops them."""
        self.cancel.raise_if_cancelled()


@dataclass
class HandlerResult:
    status: str = "ok"                                    # ok | abstained | review_required | rejected_input
    result: dict | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    model: ModelRef | None = None
    dataset_ref: str | None = None
    processing_version: str | None = None
    evidence: list[str] = field(default_factory=list)     # source texts the output may quote verbatim
    model_derived: bool = False
    artifacts: dict[str, bytes] = field(default_factory=dict)   # binary outputs (for example audio); never serialised into the JSON envelope


Handler = Callable[[Any, HandlerContext], HandlerResult]
Validator = Callable[[Any], None]


class AIEngine:
    def __init__(self, resources: ResourceManager | None = None):
        self._resources = resources
        self._handlers: dict[str, tuple[Handler, Validator | None]] = {}

    @property
    def resources(self) -> ResourceManager:
        return self._resources or resource_manager()

    # ---- registration and description
    def register(self, capability_id: str, handler: Handler, validator: Validator | None = None) -> None:
        if registry.get(capability_id) is None:
            raise KeyError(f"unknown capability {capability_id}")
        self._handlers[capability_id] = (handler, validator)

    def has_handler(self, capability_id: str) -> bool:
        return capability_id in self._handlers

    def describe(self, capability_id: str) -> CapabilityState | None:
        d = registry.get(capability_id)
        return registry.resolve(d) if d else None

    def capabilities(self, user: dict | None = None) -> list[PatientCapabilityView] | list[StaffCapabilityView]:
        """What this caller may see. Patients get only patient-facing, honest summaries (no internal reasons); staff get the full state."""
        role = (user or {}).get("role")
        states = registry.resolve_all()
        if role == "patient":
            return [PatientCapabilityView(id=s.id, kind=s.kind, name=s.name, available=s.patient_facing, status=s.status, limitations=s.limitations)
                    for s in states if "patient" in s.audiences or registry.get(s.id).patient_if_approved]   # a task that could one day reach patients is listed, honestly, as unavailable
        return [StaffCapabilityView(**s.model_dump(), implemented=s.id in self._handlers) for s in states]

    def health(self) -> dict:
        """Readiness without secrets: capability availability counts, queue and cache state."""
        from .resources import model_cache
        states = registry.resolve_all()
        return {"engine": provenance.ENGINE_VERSION, "code_revision": provenance.code_revision(), "device": select_device().__dict__,
                "capabilities": {s.id: {"availability": s.availability, "approval": s.approval} for s in states},
                "counts": {a: sum(1 for s in states if s.availability == a) for a in ("available", "degraded", "unavailable")},
                "resources": self.resources.snapshot(), "model_cache": model_cache().stats()}

    # ---- running
    def run(self, capability_id: str, payload: Any = None, *, user: dict | None = None, request_id: str = "", allow_research: bool = False,
            timeout: float | None = None, cancel: CancelToken | None = None) -> RunEnvelope:
        t0 = time.monotonic()
        rid = request_id or uuid.uuid4().hex
        staff = (user or {}).get("role") != "patient"

        def env(status: str, message: str = "", **kw) -> RunEnvelope:
            e = RunEnvelope(request_id=rid, capability=capability_id, status=status, message=message, **kw)
            e.timings.total_ms = round((time.monotonic() - t0) * 1000)
            self.resources.record(capability_id, status, time.monotonic() - t0)
            return e

        d = registry.get(capability_id)
        if d is None:
            return env("unsupported", safety.UNSUPPORTED_MSG)
        state = registry.resolve(d)
        compact = {"availability": state.availability, "approval": state.approval, "status": state.status}
        if staff:
            compact["reasons"] = state.reasons
        if state.availability == "unavailable":
            return env("unavailable", safety.UNAVAILABLE_MSG, state=compact, labels=["unavailable"])
        role = (user or {}).get("role", "system")
        audience = ROLE_AUDIENCE.get(role, "system")
        if audience != "system" and audience not in state.audiences:
            return env("forbidden", Forbidden.default_message, state=compact)
        if state.approval == "research_only" and (not allow_research or audience not in ("admin", "system")):
            return env("forbidden", "This is a research-only capability and is not available for this use.", state=compact, labels=["research_only"])
        entry = self._handlers.get(capability_id)
        if entry is None:
            return env("unavailable", safety.UNAVAILABLE_MSG, state=compact, warnings=["No implementation is registered for this capability."], labels=["unavailable"])
        handler, validator = entry
        try:
            if validator is not None:
                validator(payload)
            device = select_device()
        except RejectedInput as e:
            return env("rejected_input", e.public_message, state=compact)
        except AIError as e:
            return env(e.status, e.public_message, state=compact)
        limit = timeout or state.limits.max_seconds
        token = cancel or CancelToken()
        token.deadline = time.monotonic() + limit
        ctx = HandlerContext(rid, user, token, state, device)
        queued_at = time.monotonic()
        marks: dict[str, float] = {}

        def job() -> HandlerResult:
            marks["start"] = time.monotonic()
            return handler(payload, ctx)

        try:
            hr = self.resources.run(job, capability=capability_id, timeout=limit, cancel=token, concurrency=state.limits.concurrency)
        except AIError as e:
            if e.status == "timeout":                              # a timeout is the model's failure; a busy service, a refused input or a cancellation is not
                self._note(d, state, False)
            return env(e.status, e.public_message, state=compact)
        except Exception as e:                                     # never surface exception text; never substitute an output
            log.error("capability %s failed (request %s): %s", capability_id, rid, safe_exc(e))
            self._note(d, state, False)
            return env("error", AIError.default_message, state=compact)
        if not isinstance(hr, HandlerResult):
            log.error("capability %s returned an invalid result (request %s)", capability_id, rid)
            self._note(d, state, False)
            return env("error", AIError.default_message, state=compact)
        self._note(d, state, True)
        run_ms = round((time.monotonic() - marks.get("start", queued_at)) * 1000)
        e = self._finish(env, d, state, hr, ctx, compact)
        e.timings.queued_ms = round((marks.get("start", queued_at) - queued_at) * 1000)
        e.timings.run_ms = run_ms
        return e

    @staticmethod
    def _note(d, state: CapabilityState, ok: bool) -> None:
        """Tell the model lifecycle how a run that used the task's ACTIVE approved model ended (failures only count when the model itself errored or timed out)."""
        if d.model_task and state.model is not None and state.model.lifecycle == "active":
            from . import lifecycle
            lifecycle.record_outcome(d.model_task, ok)
            if not ok:
                try:
                    lifecycle.maybe_auto_rollback(d.model_task)
                except Exception as e:                              # monitoring must never break the request
                    log.error("model monitor failed: %s", safe_exc(e))

    def _finish(self, env, d, state: CapabilityState, hr: HandlerResult, ctx: HandlerContext, compact: dict) -> RunEnvelope:
        """Safety review, labels and provenance. A blocked output is replaced by an abstention, never edited into something that looks approved."""
        kind = SAFETY_KIND.get(d.kind, "other")
        texts = [hr.message] + [str(hr.result[k]) for k in TEXT_KEYS if hr.result and isinstance(hr.result.get(k), str)]
        review = safety.review_text("\n".join(t for t in texts if t), kind=kind, evidence=hr.evidence, model_derived=hr.model_derived, experimental=state.approval == "research_only")
        bad_keys = safety.scan_structured(hr.result) if hr.result else []
        labels = list(dict.fromkeys([*hr.labels, *review.labels, *(["review_required"] if state.approval == "review_required" else []),
                                     *(["research_only"] if state.approval == "research_only" else [])]))
        info = SafetyInfo(kind=kind, labels=labels, disclaimers=review.disclaimers, violations=review.codes() + [f"structured:{k}" for k in bad_keys],
                          clinician_review=state.approval in ("review_required", "research_only") or hr.status == "review_required")
        prov = Provenance(capability=d.id, capability_version=d.version, engine_version=provenance.ENGINE_VERSION, code_revision=provenance.code_revision(),
                          model=hr.model or state.model, dataset_ref=hr.dataset_ref, processing_version=hr.processing_version, device=ctx.device.kind)
        if not review.allowed or bad_keys:
            return env("abstained", review.replacement or safety.UNSUPPORTED_MSG, safety=info, provenance=prov, state=compact, labels=labels,
                       warnings=["The output was withheld by the safety policy."])
        status = hr.status if hr.status in ("ok", "abstained", "review_required", "rejected_input") else "error"
        if status == "error":
            return env("error", AIError.default_message, provenance=prov, state=compact)
        result = hr.result
        if state.approval == "research_only" and result is not None:
            result = safety.label_experimental(result)
        e = env(status, hr.message, result=result, labels=labels, warnings=hr.warnings, provenance=prov, safety=info, state=compact)
        e.artifacts = hr.artifacts
        return e


_engine: AIEngine | None = None


def get_engine() -> AIEngine:
    """The process-wide engine with the built-in capability handlers registered (lazily: handlers import heavy modules only when run)."""
    global _engine
    if _engine is None:
        _engine = AIEngine()
        from . import handlers
        handlers.register_all(_engine)
    return _engine


def reset_engine() -> None:
    global _engine
    _engine = None

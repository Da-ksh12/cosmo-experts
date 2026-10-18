"""Typed vocabulary of the unified AI engine: capability states, run envelopes and provenance.

Two orthogonal questions are answered separately and never conflated:
* AVAILABILITY - can this capability run right now (dependencies present, a usable implementation or model exists)?
* APPROVAL     - may its output be shown to a patient (no learned claim without an approved lifecycle state; research models never)?
The legacy patient-facing vocabulary of `status.py` is derived from both, so existing screens keep working."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Availability = Literal["available", "degraded", "unavailable"]
# none: nothing exists to approve; not_required: deterministic helper that makes no clinical claim; research_only: a registered model that has not passed its release gates;
# review_required: output is only ever a draft for clinician review; approved_for_defined_use: complete approval record AND an active lifecycle state.
Approval = Literal["none", "not_required", "research_only", "review_required", "approved_for_defined_use"]
Audience = Literal["patient", "clinician", "admin"]
RunStatus = Literal["ok", "abstained", "unsupported", "unavailable", "rejected_input", "review_required", "forbidden", "error", "timeout", "cancelled", "resource_exhausted"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(Strict):
    max_input_bytes: int | None = None
    max_pages: int | None = None
    max_seconds: float = 60.0
    concurrency: int = 1                 # simultaneous runs of this capability (a further run waits in the bounded queue)


class Dependency(Strict):
    id: str
    ok: bool
    detail: str = ""                     # never contains a path, URL or secret


class ModelRef(Strict):
    model_id: str
    task: str
    lifecycle: str = ""
    architecture: str = ""
    artifact_sha256_prefix: str = ""     # first 16 hex characters: enough to tell versions apart, not a credential


class CapabilityState(Strict):
    id: str
    kind: str
    name: str
    version: str
    description: str
    availability: Availability
    approval: Approval
    status: str                          # status.py vocabulary: available | research_only | clinician_review_required | unavailable | approved_for_defined_use
    patient_facing: bool
    audiences: list[Audience]
    reasons: list[str] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    model: ModelRef | None = None
    limits: Limits = Field(default_factory=Limits)
    limitations: list[str] = Field(default_factory=list)


class Provenance(Strict):
    capability: str
    capability_version: str
    engine_version: str
    code_revision: str
    model: ModelRef | None = None
    dataset_ref: str | None = None
    processing_version: str | None = None
    device: str = "cpu"


class SafetyInfo(Strict):
    kind: str = "education"              # education | extraction | image_assessment | diagnosis (refused) | other
    labels: list[str] = Field(default_factory=list)
    disclaimers: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    clinician_review: bool = False


class Timings(Strict):
    queued_ms: int = 0
    run_ms: int = 0
    total_ms: int = 0


class RunEnvelope(Strict):
    request_id: str
    capability: str
    status: RunStatus
    message: str = ""
    result: dict[str, Any] | None = None
    labels: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None
    safety: SafetyInfo = Field(default_factory=SafetyInfo)
    timings: Timings = Field(default_factory=Timings)
    state: dict[str, Any] | None = None  # compact capability state (availability, approval, reasons) for the caller; never internal detail for patients
    artifacts: dict[str, bytes] = Field(default_factory=dict, exclude=True)   # binary outputs for in-process callers; excluded from every JSON serialisation

    @property
    def succeeded(self) -> bool:
        return self.status in ("ok", "review_required")


class PatientCapabilityView(Strict):
    """What a patient is told about a capability: whether it can be used, in the shared status vocabulary, and its limits. No internal reasons, models or dependencies."""
    id: str
    kind: str
    name: str
    available: bool
    status: str
    limitations: list[str] = Field(default_factory=list)


class StaffCapabilityView(CapabilityState):
    implemented: bool = False

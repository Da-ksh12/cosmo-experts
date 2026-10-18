"""Capability status system. A capability can only be reported as 'approved_for_defined_use'
if a real approval record AND all release requirements are satisfied; otherwise it is downgraded."""
from dataclasses import dataclass, field

STATUSES = ("available", "research_only", "clinician_review_required", "unavailable", "approved_for_defined_use")

LABELS = {
    "available": "Available",
    "research_only": "Research only",
    "clinician_review_required": "Clinician review required",
    "unavailable": "Unavailable",
    "approved_for_defined_use": "Approved for defined use",
}

RELEASE_REQUIREMENTS = (
    "held_out_evaluation_passed",
    "subgroup_evaluation_documented",
    "clinical_signoff_recorded",
    "intended_use_documented",
    "regulatory_review_recorded",
)


@dataclass
class Approval:
    approved_by: str
    intended_use: str
    requirements_met: dict = field(default_factory=dict)


def effective_status(declared: str, approval: Approval | None = None) -> str:
    """Never trust a declared 'approved' status without a complete approval record."""
    if declared != "approved_for_defined_use":
        return declared
    if approval is None or not approval.approved_by or not approval.intended_use:
        return "research_only"
    if not all(approval.requirements_met.get(r) is True for r in RELEASE_REQUIREMENTS):
        return "research_only"
    return declared


def describe(status: str) -> dict:
    return {"status": status, "label": LABELS[status]}

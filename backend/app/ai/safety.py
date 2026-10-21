"""Shared safety policy for every patient-reachable output: image assessments, document explanations, voice, and agent answers.

What it decides (all deterministic and testable; it contains no clinical knowledge of its own):
* CLASSIFY a request as education, extraction, image assessment, a request for a diagnosis, a request for medication advice, or urgent. Urgent wording comes ONLY from the
  clinician-approval controls in `clinical_content` (the built-in default stays marked as pending approval); this module never writes emergency guidance.
* REVIEW an output before it is shown: no unsupported diagnostic claims, no medication start/stop/change recommendations, no certainty language, no emergency instructions other
  than the approved policy message, experimental output always labelled. A sentence that is a verbatim quotation of the person's own document or of approved material is not
  the system speaking, so it is allowed and labelled as a quotation.
* ABSTAIN with standard, honest messages (unsupported, unavailable, insufficient evidence, clinical decision, urgent).
* FLAG contradictory documents instead of choosing between them.
High model confidence is never treated as proof: model-derived output always carries the statement that the score is not evidence of correctness."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import clinical_content

# ---------------------------------------------------------------- standard messages (unchanged wording where the assistant already used them)
REFUSE_MSG = ("I can't recommend or choose medicines, doses or a diagnosis. Those decisions need a qualified doctor who has examined you. "
              "I can explain what your uploaded reports say, share clinic-approved information, or help you prepare questions for your doctor.")
ABSTAIN_MSG = ("I couldn't find anything in your documents or the clinic's approved information that answers this. "
               "I would rather not guess. You can ask the clinic team to review your question.")
UNSUPPORTED_MSG = "This kind of request is not supported yet. The clinic team can review it with you."
UNAVAILABLE_MSG = "This feature is not available right now."
EXPERIMENTAL_NOTE = "Research output only. Not a diagnosis, not validated for clinical use, and not shown for patient decisions."
CONFIDENCE_NOTE = "The confidence score is the model's own estimate. A high score is not evidence that the result is correct or that the image is suitable."
REVIEW_NOTE = "A clinician reviews this before it is relied on."
NOT_DIAGNOSIS = "This is not a diagnosis."

# ---------------------------------------------------------------- request classification
DIAGNOSIS_REQUEST = re.compile(
    r"\b(diagnos(?:e|is)\s+me|do i have|what (?:disease|condition|illness) do i|do you think i have|what(?:'s| is) wrong with me|am i (?:sick|dying|ill)|"
    r"is (?:this|it|that|my|the) (?:[a-z]{1,15} ){0,3}(?:cancer|cancerous|melanoma|malignant|a tumou?r|serious|dangerous))\b", re.I)
MEDICATION_REQUEST = re.compile(
    r"\b(prescribe|prescription for|what (?:medicine|medication|drug|tablet)|which (?:medicine|medication|drug|tablet)|how (?:much|many) (?:mg|tablets?|pills?|doses?)|"
    r"dosage|dose of|should i (?:take|start|stop|use|increase|reduce|skip)|can i (?:take|stop|start|skip|double|increase|reduce)|"
    r"change (?:my|the) (?:dose|medicine|medication|tablet)|switch (?:to|from)|stop taking|(?:increase|decrease|double|halve) (?:my|the) dose)\b", re.I)
QUOTE_REQUEST = re.compile(r"\b(quote|quotation|price|cost|estimate|charges?|fee|how much)\b", re.I)


@dataclass(frozen=True)
class RequestClass:
    kind: str                 # urgent | diagnosis_request | medication_advice | education
    reasons: tuple[str, ...] = ()

    @property
    def refuse(self) -> bool:
        return self.kind in ("diagnosis_request", "medication_advice")


# Ordinary non-prescription skincare products and routines, and the medicines that are NOT that. "Should I use sunscreen?" is a skincare question; "should I use isotretinoin?" is not.
SKINCARE_PRODUCTS = re.compile(
    r"\b(?:sun ?screens?|spf|moistur\w+|cleansers?|face ?wash|toners?|serums?|emollients?|lotions?|creams?|gels?|ointments?|vitamin c|niacinamide|hyaluronic|ceramides?|exfoliat\w+|scrubs?|masks?|"
    r"make-?up|foundation|lip balm|soaps?|shampoos?|conditioners?|retinol|salicylic|benzoyl|azelaic|glycolic|kojic|aha|bha|products?|routines?|skincare|skin care)\b", re.I)
PRESCRIPTION_DRUGS = re.compile(
    r"\b(?:isotretinoin|accutane|roaccutane|tretinoin|retin-?a|tazarotene|clindamycin|erythromycin|doxycycline|minocycline|lymecycline|tetracycline|azithromycin|amoxicillin|spironolactone|"
    r"prednisolone|prednisone|clobetasol|betamethasone|mometasone|fluocinolone|acitretin|dapsone|metronidazole|methotrexate|finasteride|ivermectin|fluconazole|terbinafine|ketoconazole)\b", re.I)
_MEDICINE_WORDS = re.compile(r"\b(?:medicines?|medications?|drugs?|tablets?|pills?|doses?|dosage|prescri\w+|antibiotics?|capsules?|injections?)\b", re.I)


def _skincare_question(text: str) -> bool:
    """A question about ordinary skincare products (sunscreen, cleanser, moisturiser ...) that mentions no prescription medicine and no medicine words."""
    return bool(SKINCARE_PRODUCTS.search(text)) and not PRESCRIPTION_DRUGS.search(text) and not _MEDICINE_WORDS.search(text)


_DOC_PRINTS = re.compile(r"\b(?:listed|printed|written|mentioned|prescribed)\b.{0,40}\b(?:on|in)\b.{0,30}\b(?:prescription|report|document|file|paper)\b|"
                         r"\b(?:prescription|report|document|file)\b.{0,40}\b(?:lists?|says?|shows?|mentions?|prints?)\b", re.I)


def classify_request(text: str, *, domain: str = "general") -> RequestClass:
    """Urgent first (approval-controlled policy), then the two refusals; everything else is education/extraction.
    domain='skincare': 'should I use / can I start ...' about an ordinary skincare product is education, not a request for medication advice.
    domain='records' (and 'skincare'): 'which medicine is listed on my prescription' asks what a document prints, not what to take."""
    if clinical_content.urgent_policy().regex.search(text):
        return RequestClass("urgent", ("urgent_symptom_policy",))
    if MEDICATION_REQUEST.search(text) and not (domain == "skincare" and _skincare_question(text)) and not (domain in ("skincare", "records") and _DOC_PRINTS.search(text)):
        return RequestClass("medication_advice", ("medication_advice_request",))
    if DIAGNOSIS_REQUEST.search(text):
        return RequestClass("diagnosis_request", ("diagnosis_request",))
    return RequestClass("education")


def urgent_message() -> str:
    """The wording of the urgent response: whatever the current policy says. Approved wording stays behind clinical approval; the default carries its pending-approval note."""
    return clinical_content.urgent_policy().message


def abstention(reason: str) -> dict:
    """Standard abstention payload. `reason`: unsupported | unavailable | insufficient_evidence | clinical_decision | urgent | experimental_only."""
    msgs = {"unsupported": UNSUPPORTED_MSG, "unavailable": UNAVAILABLE_MSG, "insufficient_evidence": ABSTAIN_MSG, "clinical_decision": REFUSE_MSG,
            "urgent": urgent_message(), "experimental_only": EXPERIMENTAL_NOTE}
    return {"abstained": True, "reason": reason, "message": msgs.get(reason, ABSTAIN_MSG)}


# ---------------------------------------------------------------- output review
DIAG_CLAIM = re.compile(
    r"\b(?:you (?:have|are suffering from|probably have|likely have)\b(?! (?:a |an )?(?:question|appointment|report|document|quotation|photo))|"
    r"this (?:is|looks like|appears to be) (?:a |an )?(?:melanoma|cancer|carcinoma|malignan\w*|tumou?r|basal cell|squamous)|"
    r"diagnos(?:is|ed) (?:is|of|with)|"
    r"(?:consistent with|suggests?|indicates?|confirms?|shows?) (?:a |an )?(?:melanoma|cancer|carcinoma|malignan\w*|deficiency|an[a]?emia|diabetes|disease))\b", re.I)
MED_CHANGE = re.compile(
    r"\b(?:(?:you should|you must|you need to|you can|i recommend|i suggest|consider|please)\s+(?:start|stop|take|increase|decrease|double|skip|switch|discontinue|reduce|use)\b|"
    r"(?:start|stop|increase|decrease|double|skip|switch|discontinue|reduce)\s+(?:your|the|taking)\s+(?:\w+\s+){0,2}(?:medicines?|medications?|tablets?|pills?|doses?|dosage|drugs?|creams?|ointments?|supplements?))",
    re.I)
# Skincare guidance (kind="skincare_guidance"): general non-prescription self-care is allowed to say "use", "apply", "choose". What stays forbidden: changing a MEDICINE, naming a
# prescription-only drug, and giving a dose or a strength. Diagnostic claims, certainty claims and unapproved emergency wording stay forbidden too.
MED_CHANGE_SKINCARE = re.compile(
    r"\b(?:(?:you should|you must|you need to|i recommend|i suggest|please)\s+(?:start|stop|increase|decrease|double|skip|switch|discontinue|reduce)\s+(?:\w+\s+){0,3}"
    r"(?:medicines?|medications?|tablets?|pills?|doses?|dosage|drugs?|antibiotics?)|"
    r"(?:start|stop|increase|decrease|double|skip|switch|discontinue|reduce)\s+(?:your|the|taking)\s+(?:\w+\s+){0,2}(?:medicines?|medications?|tablets?|pills?|doses?|dosage|drugs?|antibiotics?))", re.I)
DOSE_OR_STRENGTH = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|µg|ml|iu|units?)\b|\b\d+(?:\.\d+)?\s?%", re.I)
OVERCLAIM = re.compile(r"\b(?:definitely|certainly|100\s?%|guarantee[sd]?|without (?:a )?doubt|proves?|proven|for sure|absolutely)\b", re.I)
EMERGENCY_GUIDANCE = re.compile(r"\b(?:call (?:an |the |your )?(?:ambulance|emergency)|dial (?:911|112|108|999)|go to the (?:nearest )?(?:emergency|er\b|a&e)|emergency (?:room|number|services?))\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_QUOTED = re.compile(r'"([^"]{6,400})"|“([^”]{6,400})”')


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.,/%-]", "", s.lower())).strip()


@dataclass
class Violation:
    code: str
    excerpt: str


@dataclass
class SafetyReview:
    kind: str
    allowed: bool = True
    violations: list[Violation] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    disclaimers: list[str] = field(default_factory=list)
    replacement: str | None = None
    text: str = ""

    def codes(self) -> list[str]:
        return [v.code for v in self.violations]


def review_text(text: str, *, kind: str = "education", evidence: list[str] | None = None, model_derived: bool = False, experimental: bool = False) -> SafetyReview:
    """Check generated text. `evidence`: the source texts the output may quote verbatim (the person's document lines, approved material). Quoted sentences are allowed and labelled
    `quoted_from_source`; the same words written by the system are not. `model_derived`: the output comes from a learned model (adds the confidence statement)."""
    ev = [_norm(e) for e in (evidence or []) if e]
    pol = _norm(urgent_message())
    rev = SafetyReview(kind=kind, text=text)
    for sent in (s.strip() for s in _SENTENCE.split(text or "")):
        if not sent:
            continue
        ns = _norm(sent)
        quoted = len(ns) >= 12 and any(ns in e for e in ev)
        if ns and ns in pol:
            continue                                                       # the (approved or pending-approval) urgent message is the one permitted emergency wording
        # words inside a quotation that is verbatim in a source are the source speaking, not the system: remove them before checking the rest of the sentence
        checked = _QUOTED.sub(lambda m: " " if any(_norm(g) in e for g in m.groups() if g for e in ev) else m.group(0), sent)
        quoted_span = checked != sent
        found = []
        sent_full, sent = sent, checked
        if DIAG_CLAIM.search(sent):
            found.append("diagnostic_claim")
        if (MED_CHANGE_SKINCARE if kind == "skincare_guidance" else MED_CHANGE).search(sent):
            found.append("medication_change")
        if kind == "skincare_guidance":
            if PRESCRIPTION_DRUGS.search(sent):
                found.append("prescription_drug")
            if DOSE_OR_STRENGTH.search(sent):
                found.append("dose_or_strength")
        if OVERCLAIM.search(sent):
            found.append("certainty_claim")
        if EMERGENCY_GUIDANCE.search(sent):
            found.append("unapproved_emergency_guidance")
        if quoted_span and "quoted_from_source" not in rev.labels:
            rev.labels.append("quoted_from_source")
        if found and quoted:
            if "quoted_from_source" not in rev.labels:
                rev.labels.append("quoted_from_source")
            continue
        rev.violations += [Violation(c, sent_full[:90]) for c in found]
    rev.allowed = not rev.violations
    if not rev.allowed:
        codes = set(rev.codes())
        rev.replacement = REFUSE_MSG if codes & {"diagnostic_claim", "medication_change", "prescription_drug", "dose_or_strength"} else UNSUPPORTED_MSG if "unapproved_emergency_guidance" not in codes else urgent_message()
    if experimental:
        rev.labels.append("research_only")
        rev.disclaimers += [EXPERIMENTAL_NOTE]
    if model_derived:
        rev.disclaimers.append(CONFIDENCE_NOTE)
    if kind in ("extraction", "image_assessment", "education", "skincare_guidance"):
        rev.disclaimers.append(NOT_DIAGNOSIS)
    return rev


# ---------------------------------------------------------------- structured outputs
FORBIDDEN_KEYS = {"diagnosis", "diagnoses", "diagnosed_as", "disease", "prescription", "treatment_recommendation", "medication_recommendation", "dose_recommendation"}


def scan_structured(obj, path: str = "") -> list[str]:
    """Keys of a structured result that would assert a diagnosis or a medication recommendation. Extracted document content stored under data keys is not affected; only
    the system's own output keys are checked."""
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                bad.append(f"{path}{k}")
            bad += scan_structured(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            bad += scan_structured(v, f"{path}{i}.")
    return bad


def label_experimental(result: dict) -> dict:
    """Experimental model output: always labelled, always carries the interpretation limits, never named a diagnosis."""
    out = dict(result)
    out["research_only"] = True
    out["interpretation"] = EXPERIMENTAL_NOTE
    out["confidence_note"] = CONFIDENCE_NOTE
    return out


# ---------------------------------------------------------------- contradictory inputs
def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def detect_contradictions(fields: list[dict]) -> list[dict]:
    """The same test, on the same report date, with different values or units: report both, choose neither. Rows need name, value, unit, doc_date (document_id, source_page optional)."""
    groups: dict[tuple, list[dict]] = {}
    for f in fields:
        if f.get("name") and f.get("doc_date"):
            groups.setdefault((_key(f["name"]), f["doc_date"]), []).append(f)
    out = []
    for (name, date), rows in groups.items():
        distinct = {(str(r.get("value", "")).strip(), str(r.get("unit", "")).strip()) for r in rows}
        if len(distinct) > 1:
            out.append({"test": rows[0]["name"], "date": date, "values": [{"value": r.get("value"), "unit": r.get("unit"), "document_id": r.get("document_id"), "page": r.get("source_page")} for r in rows],
                        "note": "These reports print different results for the same test and date. Neither is chosen here; your clinician can tell you which is right."})
    return out

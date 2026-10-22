"""What kind of request is this? One decision, made before any answer is built, so that a photo question is never sent to document search and a skin question never needs a document.

Categories (the order below is the order of precedence):
    urgent      G  - a warning sign or an emergency wording: safety comes first and ends the normal flow (the triage level says how urgent).
    photo       A  - a question about the photo(s) of this conversation: what is in it, what to do about it, a summary of it.
    booking     F  - appointments, clinic hours and contact, services and prices, quotations.
    report      C  - a medical report or lab result: explain it, find a value, upload one.
    care_plan   E  - the clinician's care plan.
    history     D  - the person's own records and progress (earlier reports, photos, visits), and short follow-ups to a previous turn.
    education   B  - general dermatology and skincare information; answered from the dermatology library, no uploaded document needed.
    general     H1 - greetings, thanks, "what can you do".
    unrelated   H2 - nothing to do with skin, health or the clinic.
A request for a diagnosis or for a medicine is recognised too (`request`: diagnosis | medication): the answer is helpful (what can be said, what a clinician decides), not a bare refusal.

Rules are plain word patterns in English, Hinglish and Hindi over the person's OWN message and the conversation's context (is there a photo in this conversation? what was the last topic?). They are deterministic
and unit-tested (tests/test_assistant_router.py); a wrong guess costs a clarifying answer, never a wrong action, because nothing that changes state runs without the person's confirmation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ai import safety
from ..derm import lexicon, reference, triage as triage_mod

CATEGORY = {"urgent": "G", "photo": "A", "booking": "F", "report": "C", "care_plan": "E", "history": "D", "education": "B", "general": "H", "unrelated": "H"}


def _r(p: str) -> re.Pattern:
    return re.compile(p, re.I)


GREETING = _r(r"^\s*(?:hi|hii+|hello|hey|namaste|namaskar|good (?:morning|afternoon|evening)|thanks?|thank you|thx|ok(?:ay)?|got it|great|nice|bye|goodbye|नमस्ते|धन्यवाद|शुक्रिया)\b[\s!.,?]*$")
HELP = _r(r"\b(?:what can you do|what do you do|who are you|help me|how can you help|how do you work|what are you|what (?:else )?can i ask|your (?:capabilities|features)|madad|kya kar sakte|aap kaun)\b|मदद|आप कौन")
BOOKING = _r(r"\b(?:book(?:ing)?|schedule|reserve|appointments?|consult(?:ation|ations)?|slots?|timings?|clinic (?:hours|timings?|address|phone|number|location|contact)|opening hours|working hours|"
             r"call (?:me|the clinic)|contact (?:the )?clinic|phone number|visit the clinic|where (?:is|are) (?:the |your )?(?:clinic|centre|center|office)|(?:clinic|centre|center|office) (?:is |are )?(?:located|open|closed)|"
             r"(?:is|are) (?:the clinic|the centre|the center|you) (?:open|closed)|(?:your|the clinic'?s?) (?:address|hours|phone|whatsapp|e-?mail|location)|directions to|how (?:do|can) i (?:reach|contact|get to)|whatsapp|"
             r"clinic ka|kahan hai|kab khula|meet (?:a |the )?(?:doctor|dermatologist)|see (?:a |the )?(?:doctor|dermatologist) (?:today|tomorrow|this week|soon|on)|"
             r"talk to (?:a |the )?(?:doctor|dermatologist|someone)|speak to (?:a |the )?(?:doctor|dermatologist)|video (?:call|consult\w*)|dikhana hai|milna hai|appointment chahiye)\b|अपॉइंटमेंट|बुकिंग|समय")
SERVICES = _r(r"\b(?:prices?|price ?list|costs?|how much|charges?|fees?|rates?|services?|treatments? (?:do you|you) (?:offer|provide|have)|what (?:do you|treatments) (?:offer|provide)|procedures?|packages?|quotations?|quotes?|estimates?|"
              r"laser|facial|peel|chemical peel|prp|mesotherapy|hydrafacial)\b|कीमत|फीस|खर्च")
REPORT = _r(r"\b(?:reports?|lab(?:oratory)? (?:results?|tests?|reports?)|blood (?:tests?|work|reports?)|test results?|results? (?:of|from) (?:my )?tests?|medical report|prescriptions?|reference range|normal range|"
            r"haemoglobin|hemoglobin|hb\b|ferritin|vitamin (?:d|b12|b-12)|b12|tsh|thyroid|cholesterol|hba1c|blood sugar|glucose|creatinine|platelets?|wbc|rbc|esr|crp|uric acid|"
            r"upload(?:ed)? (?:a |my |the )?(?:medical )?(?:report|document|file|test)|my documents?|my files?|scan report|x-?ray|mri|ultrasound)\b|रिपोर्ट|जांच|टेस्ट")
CARE_PLAN = _r(r"\b(?:care plan|treatment plan|my plan|plan from (?:the |my )?doctor|doctor'?s plan|next steps from (?:the |my )?doctor|stages? of (?:my )?(?:treatment|plan)|what did (?:the |my )?doctor (?:say|advise|plan))\b|केयर प्लान|इलाज की योजना")
HISTORY = _r(r"\b(?:my (?:history|records?|past|previous|earlier|last|old) (?:visits?|photos?|reports?|consults?|appointments?|records?)|(?:previous|earlier|last|old) (?:photos?|reports?|visits?|appointments?)|"
             r"progress (?:so far|over time)|how (?:am i|have i been) (?:doing|progressing)|since (?:my )?last (?:visit|photo|report)|compare (?:with|to) (?:my )?(?:previous|earlier|last|old)|what have i uploaded|what do i have|my account)\b")
PHOTO_REF = _r(r"\b(?:(?:my|this|the|that|uploaded|attached|latest|last|above|current) (?:photos?|pictures?|pics?|images?|selfies?|scan)|(?:in|from|on) (?:the |my |this )?(?:photos?|pictures?|pics?|images?|selfies?)|"
               r"(?:photos?|pictures?|images?) (?:shows?|showing|looks?)|what (?:is|are|was) (?:this|these|that|those)|what do you see|can you see|look at (?:this|my|these|it)|look(?:s|ing)? at (?:the )?photo|"
               r"is (?:this|it|that) (?:acne|a pimple|a rash|eczema|a mole|normal|serious|bad|okay|ok|infected|pigmentation|a scar)|"
               r"(?:these|those) (?:spots?|marks?|bumps?|pimples?|patches|dots|areas?)|(?:this|that) (?:spot|mark|bump|patch|rash|area|part|thing|pimple|scar|mole)|"
               r"(?:highlighted|outlined|marked|circled) (?:area|part|region|spot)|explain (?:this|it|the (?:area|photo|picture|result|assessment))|the (?:area|result|assessment) (?:in|of) (?:the |my )?(?:photo|picture))\b|"
               r"फोटो|तस्वीर|इमेज")
PHOTO_EXPLAIN = _r(r"\b(?:what (?:is|are|does|do|could|can|might)\b.*\b(?:this|these|that|those|it)\b|explain\b.*\b(?:this|these|that|it|photo|result|area)\b|tell me (?:about|what) (?:this|it|my photo)|"
                   r"what do you (?:see|think|make of)|can you tell|is (?:this|it) (?:serious|bad|normal|okay|ok|infected|dangerous|cancer|acne)|what should i (?:do|use) (?:for|about) (?:this|it|these)|"
                   r"what could (?:be )?(?:causing|contributing to) (?:this|it|these))")
DETECT_REQ = _r(r"\b(?:can|could|will|would|do|are) (?:you|u)\b.{0,24}\b(?:find|detect|see|spot|identify|tell|check|analy[sz]e|scan|look (?:at|for)|diagnos\w+)\b|"
                r"\b(?:find|detect|spot|identify|analy[sz]e|scan|check)\b.{0,25}\b(?:acne|pimples?|redness|spots?|marks?|wrinkles?|pigment\w*|dark circles?|skin|face|problem|issue|condition)\b")
PHOTO_ACTION = _r(r"\b(?:better|clearer|another|new|sharper) (?:photo|picture|pic|selfie)\b|\btake (?:a |another )?(?:better|clearer|sharper)\b|\b(?:retake|re-take)\b|\bwhat happens (?:next|to (?:my|the|this) (?:photo|picture))\b|"
                  r"\bwill (?:a |the )?(?:doctor|clinician|dermatologist) (?:see|review|look at)\b|\bwho (?:sees|reviews|looks at)\b|\bwhat (?:was|did you) (?:measure|check)\w*\b")
SUMMARY = _r(r"\b(?:prepare|write|make|create|generate|draft|give me|i need|can you (?:make|write|prepare))\b.{0,24}\bsummary\b|\bsummary (?:for|of|to (?:give|show|send)) (?:my |the |a )?(?:dermatologist|doctor|clinician|appointment|visit|clinic)\b|"
             r"\b(?:something|notes?) to (?:show|give|send) (?:my |the )?(?:dermatologist|doctor|clinician)\b")
SHORT_FOLLOWUP = _r(r"^\s*(?:and|also|what about|how about|why|how long|how often|when|where|which|is it|does it|will it|can i|should i|any|ok(?:ay)?(?: and)?|so|then|what if|kya|aur|kaise|kab|kitna)\b")
ASKS_DIAGNOSIS = _r(r"\b(?:do i have|what (?:disease|condition|illness|infection) (?:is|do i)|what(?:'s| is) wrong with me|diagnos\w+|is (?:this|it|that) (?:acne|a pimple|eczema|rosacea|psoriasis|melasma|a mole|a rash|ringworm|a fungal infection|an infection|vitiligo|cancer|melanoma)|"
                    r"which (?:disease|condition)|name (?:of )?(?:this|the) (?:disease|condition)|kaun si bimari|kya bimari)\b")
ASKS_MEDICINE = _r(r"\b(?:prescribe|prescription for|what (?:medicine|medication|drug|tablet|antibiotic|steroid)s?|which (?:medicine|medication|drug|tablet|antibiotic|steroid)s?|dosage|dose of|how (?:much|many) (?:mg|tablet|pill)s?|"
                   r"antibiotics? for|steroid (?:cream|tablet)s? for|should i (?:take|start|stop)|can i (?:take|stop|start)|kaun si (?:dawai|tablet|medicine))\b")


@dataclass
class Route:
    kind: str                                   # urgent | photo | booking | report | care_plan | history | education | general | unrelated
    category: str                               # A-H
    topic: str | None = None                    # the article topic when the question is about one
    topics: list[str] = field(default_factory=list)
    request: str | None = None                  # diagnosis | medication: what the person is asking for, when it is something that cannot be given
    triage: triage_mod.Triage = field(default_factory=triage_mod.Triage)
    sub: str = ""                               # booking: appointment | services | quote ; photo: describe | routine ...
    reasons: list[str] = field(default_factory=list)
    inherited: bool = False                     # decided from the previous turn (a short follow-up)

    def public(self) -> dict:
        return {"kind": self.kind, "category": self.category, "topic": self.topic, "request": self.request, "sub": self.sub, "triage": self.triage.level, "reasons": self.reasons}


def _has_photo_words(text: str) -> bool:
    return bool(PHOTO_REF.search(text))


def classify(text: str, *, has_photo: bool = False, prev_kind: str | None = None, prev_topic: str | None = None, prior_text: str = "", slots: lexicon.Slots | None = None,
             explicit_photo: bool = False, doc_count: int = 0) -> Route:
    """The route for one message. `has_photo`: this conversation holds a photo assessment. `explicit_photo`: the client attached one (the person tapped a question or an area on the photo).
    `prev_kind`/`prev_topic`: the route and topic of the previous answer, used only for short follow-ups. `doc_count`: how many documents the person has (a report question with none still routes to
    'report', which then says so and offers the upload)."""
    t = " ".join((text or "").split())
    reasons: list[str] = []
    slots = slots or lexicon.extract_slots([t])
    tri = triage_mod.assess(t, prior=prior_text, duration_days=slots.duration_days)
    topics = [x for x, _ in reference.topics_in(t)]
    topic = next((x for x in topics if x in lexicon.CONCERN_TOPICS), topics[0] if topics else None)
    cls = safety.classify_request(t, domain="skincare")
    request = None
    if cls.kind == "medication_advice":
        request = "medication"
    elif cls.kind == "diagnosis_request":
        request = "diagnosis"
    elif ASKS_MEDICINE.search(t) and not safety._skincare_question(t) and not REPORT.search(t):
        request = "medication"
    elif ASKS_DIAGNOSIS.search(t):
        request = "diagnosis"
    if request == "medication" and REPORT.search(t) and re.search(r"\b(?:listed|printed|written|mentioned|prescribed|in my|on my|on the|in the)\b", t, re.I):
        request = None                                              # "which medicine is listed on my prescription" asks what a document prints, not what to take

    def route(kind: str, **kw) -> Route:
        return Route(kind, CATEGORY[kind], topic=kw.pop("topic", topic), topics=topics, request=request, triage=tri, reasons=reasons + kw.pop("reasons", []), **kw)

    if tri.level in ("emergency", "urgent"):
        return route("urgent", reasons=["warning_signs:" + tri.level])
    skin = lexicon.is_skin_related(t)
    photo_words = _has_photo_words(t)
    asks_booking = bool(BOOKING.search(t))
    asks_services = bool(SERVICES.search(t))
    asks_report = bool(REPORT.search(t))
    if SUMMARY.search(t):                                         # "prepare a summary for my dermatologist": about the photo if there is one, otherwise about the person's records and what they told me
        return route("photo" if (has_photo or explicit_photo) else "history", sub="summary", reasons=["summary_request"])
    # a question about when to see a clinician is education, even though it mentions a doctor; only an explicit request to book/contact is booking
    if asks_booking and not re.search(r"\b(?:should i|do i need|is it (?:necessary|worth)|when (?:should|to)|how (?:soon|long)|need to see)\b", t, re.I):
        return route("booking", sub="appointment", reasons=["booking_words"])
    if asks_services and not skin_only_question(t):
        return route("booking", sub="quote" if re.search(r"\b(?:quot\w+|estimate)\b", t, re.I) else "services", reasons=["service_or_price_words"])
    detect = bool(DETECT_REQ.search(t)) and (has_photo or photo_words)                  # "can you find the acne on my face?" asks the photo analysis to find something
    action = has_photo and bool(PHOTO_ACTION.search(t))                                  # "how can I take a better photo", "what happens next": about the photo in this conversation
    about_photos_in_general = topic == "photo-limits" and not (explicit_photo or detect or action)              # "what can a photo show?" is education, with or without a photo in the conversation
    if not about_photos_in_general and (explicit_photo or detect or action or (has_photo and (photo_words or PHOTO_EXPLAIN.search(t) and not topic)) or (photo_words and not has_photo and not topic)):
        if not (asks_report and not photo_words):
            return route("photo", reasons=["detect_request" if detect else "photo_words" if photo_words else "photo_context"], sub="detect" if detect else "", topic=topic)
    if asks_report and not (skin and topic and not re.search(r"\b(?:report|lab|test|result|upload|document|prescription)\b", t, re.I)):
        return route("report", reasons=["report_words"])
    if CARE_PLAN.search(t):
        return route("care_plan", reasons=["care_plan_words"])
    if HISTORY.search(t):
        return route("history", reasons=["history_words"])
    if topic or skin:
        return route("education", reasons=["skin_topic"] if topic else ["skin_words"])
    if GREETING.search(t) or HELP.search(t):
        return route("general", reasons=["greeting_or_help"])
    # a short follow-up with no cue of its own continues the previous turn ("and how long does it take?")
    if prev_kind in CATEGORY and prev_kind not in ("general", "unrelated", "urgent") and SHORT_FOLLOWUP.search(t) and len(t.split()) <= 12:
        return route(prev_kind, topic=prev_topic, inherited=True, reasons=["follows_previous_turn"])
    return route("unrelated", reasons=["no_skin_health_or_clinic_words"])


def skin_only_question(t: str) -> bool:
    """'How much sunscreen should I use' contains 'how much' but is a skincare question, not a price question."""
    return bool(re.search(r"\bhow much\b", t, re.I)) and bool(lexicon.is_skin_related(t)) and not re.search(r"\b(?:cost|price|fee|charge|rate|pay|paid|rupees?|inr|rs\.?)\b", t, re.I)

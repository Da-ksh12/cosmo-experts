"""Warning signs: how soon should a described skin problem be looked at, and why.

Four levels, each with a plain reason the person is shown (an escalation is always explained, never just announced):
    emergency  - signs that can mean a life-threatening reaction or illness. The message is the clinic's urgent-symptom policy message (clinical_content.urgent_policy); this module writes
                 no emergency instructions of its own, only the reason the words raised it.
    urgent     - signs that should be assessed the SAME DAY (spreading infection, eye involvement, a blistering rash on one side of the face, infected eczema, a rash after a new medicine ...).
    prompt     - worth booking a clinician appointment soon (a changing mole, a sore that does not heal, acne that has not responded or is scarring, a rash lasting weeks, low mood about skin).
    routine    - nothing in the message calls for escalation; gentle self-care is reasonable.

The rules are conservative by design: when the wording could mean either, the higher level is chosen, because a needless escalation costs a phone call and a missed one can cost far more. They are
clinical content that a clinician must approve; until then every rule carries status "pending_clinician_review" (see review_sheet()) and the built-in urgent-symptom policy keeps its
"pending approval" note. A mention that is negated ("no fever", "without swelling") does not count, and a rule never fires on the assistant's own words, only on the person's.

Nothing here diagnoses. A reason says which words raised the level and what that kind of sign can mean; it never says the person has a condition."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import clinical_content
from ..ai import safety

VERSION = "derm-triage-1.0.0"
LEVELS = {"routine": 1, "prompt": 2, "urgent": 3, "emergency": 4}
_NEG = re.compile(r"(?:\b(?:no|not|without|never|none|nothing|nahi|nahin|nhi|bina)\b|नहीं|बिना)\W+(?:\w+\W+){0,2}$", re.I)


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.I)


@dataclass(frozen=True)
class Rule:
    id: str
    level: str
    trigger: str                      # plain-language description for the clinicians who review the rules
    reason: str                       # what the person is told: why this was raised, without claiming a diagnosis
    groups: tuple[re.Pattern, ...]    # every group must match (a match that is negated does not count)
    unless: re.Pattern | None = None
    article: str | None = None        # the article that explains the sign
    sources: tuple[str, ...] = ()


_SWELL = (r"(?:swell\w*|puffy|puffed)\W+(?:\w+\W+){0,3}(?:face|lips?|tongue|throat|mouth)|(?:face|lips?|tongue|throat|mouth)\W+(?:\w+\W+){0,3}(?:swell\w*|swollen|puffy|puffed)|throat\W+(?:\w+\W+){0,2}(?:closing|tight)"
          r"|(?:chehra|chehre|honth|jeebh|zubaan|gala|munh)\W+(?:\w+\W+){0,3}(?:sooj\w*|suj\w*)")
_UNWELL = r"fever\w*|temperature|chills|unwell|very ill|really sick|headache|stiff neck|drows\w+|confus\w+|sleepy|vomit\w*|bukhar|बुखार"
_SKIN_PROBLEM = r"rash|pimple|boil|abscess|infect\w*|red|redness|swollen|swelling|blister\w*|sore|wound|cut|bite|spots?|hives|skin|sooj\w*|laal|daane|chhale|phunsi|khujli|chakatte|छाले|खुजली|सूजन"

RULES: tuple[Rule, ...] = (
    # ------------------------------------------------------------------ emergency
    Rule("E-airway-swelling", "emergency", "Swelling of the lips, tongue, face, mouth or throat, or a tight throat.",
         "You mentioned swelling of the lips, tongue, face, mouth or throat. Swelling like this can be a sign of a serious allergic reaction.",
         (_rx(_SWELL + r"|होंठ\W+(?:\w+\W+){0,2}सूज|जीभ\W+(?:\w+\W+){0,2}सूज|गला\W+(?:\w+\W+){0,2}(?:बंद|जकड़)"),), article="derm.warning-signs", sources=("nhs-anaphylaxis",)),
    Rule("E-breathing", "emergency", "Trouble breathing or swallowing, or wheezing, together with a skin or allergy problem.",
         "You mentioned trouble breathing or swallowing with a skin or allergy problem. This can be a sign of a serious allergic reaction.",
         (_rx(r"(?:can'?t|cannot|can not|unable to|trouble|difficulty|hard to|struggling to)\W+(?:\w+\W+){0,2}(?:breath\w*|swallow\w*)|wheez\w*|shortness of breath|सांस\W+(?:\w+\W+){0,3}(?:तकलीफ|दिक्कत|नहीं)"),
          _rx(r"rash|hives|itch\w*|skin|allerg\w*|swell\w*|sting|bite|cream|medicine|tablet|injection|food|reaction|lips?|tongue|face")), article="derm.warning-signs", sources=("nhs-anaphylaxis",)),
    Rule("E-nonblanching-rash-unwell", "emergency", "A rash that does not fade when pressed with a glass (or purple spots) together with fever, headache, stiff neck, drowsiness or feeling very unwell.",
         "You described a rash that does not fade under pressure, or purple spots, together with feeling unwell. That combination can be a sign of a serious blood or brain infection.",
         (_rx(r"glass test|non[- ]?blanching|(?:rash|spots?|marks?|patches|bruises?)\W+(?:\w+\W+){0,6}(?:does ?n[o']?t|doesn't|won'?t|not|never)\W+(?:\w+\W+){0,2}(?:fade|blanch|disappear|go away)(?:\W+\w+){0,5}\W+(?:press\w*|glass)|"
              r"(?:purple|dark red|bruise[- ]like)\W+(?:\w+\W+){0,2}(?:spots?|rash|patches|marks)"), _rx(_UNWELL)), article="derm.warning-signs", sources=("nhs-meningitis-symptoms",)),
    Rule("E-blistering-mouth-eyes", "emergency", "A spreading, painful, blistering or peeling rash with sores in the mouth, eyes, lips, throat or genitals.",
         "You described blistering or peeling skin with sores in the mouth, eyes, lips, throat or genitals. That pattern can be a sign of a serious skin reaction.",
         (_rx(r"blister\w*|peel\w*|skin (?:is )?(?:coming|falling|sloughing) off|raw skin|chhale|छाले"), _rx(r"mouth|lips?|eyes?|eyelids?|throat|genitals?|private parts|inside (?:my )?(?:nose|mouth)"),
          _rx(r"spread\w*|all over|widespread|painful|fever|whole body|rapidly|rash")), _rx(r"cold sores?|fever blisters?|canker\w*|mouth ulcers?|stye"), "derm.warning-signs", ("nhs-stevens-johnson",)),
    Rule("E-infection-sepsis-signs", "emergency", "A skin infection or a hot, swollen, painful red area together with confusion, fainting, a very fast heartbeat, shivering, clammy or pale skin or purple patches.",
         "You described a skin infection or a hot, swollen, painful area together with signs that the whole body may be affected (such as confusion, fainting, a racing heart or clammy skin).",
         (_rx(r"infect\w*|cellulitis|red(?:ness)?\W+(?:\w+\W+){0,3}(?:spread\w*|hot|painful)|hot\W+(?:\w+\W+){0,2}swollen|swollen\W+(?:\w+\W+){0,2}hot|pus|abscess"),
          _rx(r"confus\w*|disorient\w*|faint\w*|passing out|(?:fast|racing|pounding) heart\w*|heart (?:is )?racing|very high (?:fever|temperature)|shiver\w*|clammy|cold and pale|purple patches|can'?t stay awake|unresponsive|blue lips")),
         None, "derm.warning-signs", ("nhs-cellulitis",)),
    # ------------------------------------------------------------------ urgent (same day)
    Rule("U-spreading-infection-signs", "urgent", "Redness, swelling or a rash that is spreading quickly and is hot, painful, tender or has pus, or red streaks.",
         "You described skin that is spreading quickly and is hot, painful, swollen or has pus. These can be signs of a skin infection, which is treated best when it is seen early.",
         (_rx(r"redness|red (?:area|patch|skin)|rash|swelling|swollen|skin|streaks?"), _rx(r"spread\w*\W+(?:\w+\W+){0,2}(?:fast|quickly|rapidly)|getting (?:much )?bigger|growing (?:fast|quickly)|red streaks?|streaks? (?:going|up|along)|rapidly|within (?:hours|a day)|overnight|spreading"),
          _rx(r"hot|warm|painful|pain|tender|pus|fever|swollen|swelling")), None, "derm.warning-signs", ("nhs-cellulitis",)),
    Rule("U-fever-with-skin-problem", "urgent", "A fever together with a rash, spots, sore, swelling or other skin problem.",
         "You mentioned a fever together with a skin problem. A fever with a rash or an infected area is worth having looked at the same day.",
         (_rx(r"fever\w*|temperature|chills|bukhar|बुखार"), _rx(_SKIN_PROBLEM)), None, "derm.warning-signs", ("nhs-cellulitis", "nhs-atopic-eczema")),
    Rule("U-eye-involvement", "urgent", "Eye pain, blurred vision, light sensitivity, a red or gritty eye or swelling around the eye together with a skin condition or rash.",
         "You mentioned eye symptoms (such as pain, blurred vision, light sensitivity or a red eye) together with a skin problem. Eye symptoms with a skin condition should be checked promptly.",
         (_rx(r"eyes?|eyelids?|vision|eyesight|sight"), _rx(r"pain\w*|blurr\w*|light sensitiv\w*|sensitive to light|can'?t see|double vision|red eye|gritty|swollen|swelling|discharge"),
          _rx(r"rash|skin|rosacea|blister\w*|shingles|face|forehead|nose|dermatitis|eczema|sore|pimple|acne|stye|cream|product|reaction")), None, "derm.rosacea", ("nhs-rosacea", "nhs-shingles")),
    Rule("U-shingles-like-rash", "urgent", "A painful, burning or tingling blistering rash on one side of the body or face.",
         "You described a painful or burning blistering rash on one side. That pattern can be shingles, which is treated best when it is seen within the first few days, and a rash near the eye needs particular care.",
         (_rx(r"blister\w*|rash|sores?"), _rx(r"one side|single side|band|stripe|shingles|half (?:of )?(?:my )?(?:face|body|forehead)"), _rx(r"pain\w*|burn\w*|tingl\w*|sting\w*|hurts?|dard|दर्द")), None,
         "derm.warning-signs", ("nhs-shingles",)),
    Rule("U-eczema-infected", "urgent", "Eczema with blisters, crusting, weeping, pus, heat, swelling, pain or feeling unwell.",
         "You described eczema with blistering, crusting, weeping, pus, heat or swelling. Eczema can become infected, and that needs treatment.",
         (_rx(r"eczema|dermatitis|itchy patches?"), _rx(r"blister\w*|crust\w*|weeping|oozing|pus|yellow\W+(?:\w+\W+){0,2}(?:fluid|crust|discharge)|hot|swollen|swelling|painful|fever|unwell|spreading rapidly|worse quickly")),
         None, "derm.eczema", ("nhs-atopic-eczema",)),
    Rule("U-rash-after-medicine", "urgent", "A new rash, hives, peeling or blisters that started after beginning a medicine, tablet, antibiotic or injection.",
         "You described a new rash that began after starting a medicine. A rash after a new medicine should be checked the same day by the prescriber or a clinician, and the medicine should not be stopped or changed without their advice.",
         (_rx(r"rash|hives|itchy skin|peeling|blister\w*|spots all over"),
          _rx(r"(?:after|since|when i)\W+(?:\w+\W+){0,3}(?:start\w*|tak\w+|us\w+|having|given|began)\W+(?:\w+\W+){0,4}(?:medicine|medication|tablets?|pills?|antibiotics?|injection|drug|painkiller|capsules?)")),
         None, "derm.warning-signs", ("nhs-stevens-johnson",)),
    Rule("U-baby-rash-unwell", "urgent", "A baby or infant with a rash and fever, poor feeding, drowsiness or floppiness.",
         "You mentioned a baby with a rash and signs that the baby may be unwell. A baby with a rash who is feverish, not feeding well or unusually sleepy should be seen the same day.",
         (_rx(r"baby|infant|newborn|my \d+[- ]?(?:month|week)s?[- ]?old"), _rx(r"rash|spots"), _rx(r"fever|temperature|not feeding|won'?t feed|drows\w+|floppy|hard to wake|unwell|lethargic|bukhar")),
         None, "derm.warning-signs", ("nhs-meningitis-symptoms",)),
    Rule("U-severe-pain", "urgent", "Severe or unbearable pain together with a skin problem, swelling, lump, blister or wound.",
         "You described severe pain with a skin problem. Severe pain out of proportion to how the skin looks can be a sign that something deeper is wrong.",
         (_rx(r"severe|unbearable|excruciating|intense|worst|agony|terrible"), _rx(r"pain\w*|hurts?"), _rx(r"skin|rash|face|pimple|boil|swelling|lump|blister\w*|burn|sore|wound|acne")), None,
         "derm.warning-signs", ("nhs-cellulitis",)),
    Rule("U-chemical-or-blistering-burn", "urgent", "A chemical burn, or a sunburn that has blistered.",
         "You described a chemical burn or a blistering sunburn. Burns like this should be assessed the same day.",
         (_rx(r"chemical burn|acid (?:burn|splash|on my)|burnt my (?:skin|face)|burned my (?:skin|face)|bleach on (?:my )?(?:skin|face)|sunburn\W+(?:\w+\W+){0,4}blister\w*|blister\w*\W+(?:\w+\W+){0,4}sunburn|blistered (?:from|after) (?:the )?sun"),),
         None, "derm.warning-signs", ()),
    # ------------------------------------------------------------------ prompt (book soon)
    Rule("P-changing-mole-or-spot", "prompt", "A mole, spot or growth that is changing, getting bigger or darker, bleeding, itching, crusting, uneven, or looks different from the others.",
         "You described a mole or spot that is changing or has other features that deserve a check. A change like that is a reason to have the spot examined, and most checks do not find cancer.",
         (_rx(r"moles?|birthmarks?|freckles?|lesion|spot|growth|lump|\btil\b|तिल|मस्सा"),
          _rx(r"chang\w+|bigger|larger|growing|darker|darkening|bleed\w*|itch\w*|crust\w*|irregular|uneven|asymmetr\w*|ragged|new mole|looks different|ugly duckling|multi[- ]?colou?r\w*|more than one colou?r")),
         _rx(r"acne|pimples?|zits?|breakouts?"), "derm.moles-and-spots", ("nhs-melanoma-symptoms", "aad-abcdes")),
    Rule("P-sore-not-healing", "prompt", "A sore, wound or scab that does not heal, or heals and comes back.",
         "You described a sore that does not heal or keeps coming back. A sore that has not healed after a few weeks should be examined.",
         (_rx(r"sores?|ulcer|wound|scab"), _rx(r"(?:doesn'?t|does not|won'?t|will not|not|isn'?t|never) (?:heal\w*|go away)|non[- ]?healing|heals and (?:comes|returns) back|keeps coming back|(?:for|over|more than)\W+(?:\w+\W+){0,2}(?:four|five|six|several|many|4|5|6)\W+weeks")),
         None, "derm.moles-and-spots", ("nhs-melanoma-symptoms", "aad-abcdes")),
    Rule("P-dark-line-under-nail", "prompt", "A dark line or streak under a fingernail or toenail.",
         "You described a dark line under a nail. A dark area under a nail that was not caused by an injury should be looked at.",
         (_rx(r"dark (?:line|streak|band)\W+(?:\w+\W+){0,3}(?:nail|toenail|fingernail)|(?:nail|toenail|fingernail)\W+(?:\w+\W+){0,4}dark (?:line|streak|band)|black (?:line|streak)\W+(?:\w+\W+){0,3}nail"),),
         None, "derm.moles-and-spots", ("nhs-melanoma-symptoms",)),
    Rule("P-skin-cancer-worry", "prompt", "A worry that a spot might be skin cancer or melanoma.",
         "You mentioned a worry about skin cancer. A photo cannot tell, and an examination in person is the way to find out.",
         (_rx(r"skin cancer|melanoma|cancerous|carcinoma|malignant"), _rx(r"worried|scared|afraid|is it|could it be|might be|do i have|think it is|looks like|my (?:mole|spot)")), None, "derm.moles-and-spots", ("nhs-melanoma-symptoms",)),
    Rule("P-acne-needs-clinician", "prompt", "Acne that is deep, painful, cystic or scarring, has not improved, has lasted months or years, or is affecting mood.",
         "You described acne with features (such as deep or painful spots, scarring, no improvement or a long duration) for which a clinician can offer more than over-the-counter care.",
         (_rx(r"acne|pimples?|breakouts?|cystic|nodul\w+|cysts?|zits?"),
          _rx(r"deep|painful|cystic|nodul\w+|cyst\w*|scarr\w*|scars?|keloid|leaving (?:marks|dark marks|scars)|not (?:getting )?(?:better|clearing|improving|working)|no improvement|nothing (?:is )?working|worse(?:ning)?|getting worse|"
              r"tried everything|for (?:\w+\W+){0,2}(?:months?|years?)|since (?:\w+\W+){0,3}(?:months?|years?)|since (?:my )?teen\w*")), None, "derm.acne", ("nhs-acne", "aad-acne-tips")),
    Rule("P-low-mood-about-skin", "prompt", "Low mood, anxiety or embarrassment because of how the skin looks.",
         "You mentioned that your skin is affecting how you feel. That matters, and it is a good reason to speak to a clinician; skin conditions can affect mental health and help is available.",
         (_rx(r"depress\w*|anxi\w+|low confidence|ashamed|embarrass\w*|hate my (?:skin|face)|don'?t want to (?:go out|see (?:anyone|people))|avoid\w* (?:people|going out)|mental health|self[- ]?esteem"),
          _rx(r"skin|face|acne|pimples?|marks?|scars?|spots?|rash|looks?")), None, "derm.acne", ("nhs-acne",)),
    Rule("P-raised-or-spreading-scars", "prompt", "Raised, thick or keloid scars, or scars that are growing.",
         "You described raised or growing scars. These are worth discussing with a dermatologist early, because some treatments work best before a scar matures.",
         (_rx(r"keloid|raised scars?|thick scars?|scars? (?:that )?(?:are |is )?(?:getting|growing|spreading)"),), None, "derm.acne-marks-scars", ("aad-acne-darker-skin",)),
    Rule("P-hair-loss", "prompt", "Sudden, patchy or heavy hair loss, or bald patches.",
         "You mentioned sudden or patchy hair loss. Hair and scalp concerns are best looked at by a clinician, and the library here does not cover hair and scalp yet.",
         (_rx(r"(?:patchy|sudden|rapid|a lot of|excessive|clumps of) hair (?:loss|fall)|bald (?:patch|spot)s?|hair (?:is )?falling out in (?:patches|clumps)"),), None, None, ()),
)
BY_ID = {r.id: r for r in RULES}

_PERSIST_WORDS = re.compile(r"rash|itch\w*|patch(?:es)?|flak\w+|redness|bumps?|dry|scaly|blotch\w*", re.I)
_ACNE_WORDS = re.compile(r"acne|pimples?|breakouts?|zits?", re.I)


@dataclass
class Triage:
    level: str = "routine"
    rules: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    article: str | None = None
    sources: list[str] = field(default_factory=list)

    @property
    def rank(self) -> int:
        return LEVELS[self.level]

    def escalated(self) -> bool:
        return self.rank >= LEVELS["prompt"]


def _group_match(rx: re.Pattern, text: str) -> bool:
    for m in rx.finditer(text):
        if not _NEG.search(text[max(0, m.start() - 36):m.start()]):
            return True
    return False


def _fires(rule: Rule, text: str) -> bool:
    if rule.unless is not None and _group_match(rule.unless, text):
        return False
    return all(_group_match(g, text) for g in rule.groups)


def assess(text: str, *, prior: str = "", duration_days: int | None = None) -> Triage:
    """Level and reasons for what the person wrote (and, for a follow-up, their previous message). The assistant's own words are never passed in."""
    joined = f"{prior} . {text}" if prior else (text or "")
    t = Triage()
    fired: list[Rule] = [r for r in RULES if _fires(r, joined)]
    if prior:                                                      # the previous message may complete a sign described across two messages, but it must not raise the same alarm again on its own
        now = {r.id for r in RULES if _fires(r, text or "")}
        before = {r.id for r in RULES if _fires(r, prior)}
        fired = [r for r in fired if r.id in now or r.id not in before]
    # a rash or irritation that has lasted weeks is worth a clinician's look even when no single warning word appears (acne has its own rule with its own time frame)
    if duration_days is not None and duration_days >= 21 and _PERSIST_WORDS.search(joined) and not _ACNE_WORDS.search(joined) and not any(r.level in ("emergency", "urgent") for r in fired):
        fired.append(Rule("P-persistent-rash", "prompt", "A rash, itch, patch or dryness that has lasted three weeks or more.",
                          "You said this has lasted for weeks. A rash or patch that has not settled after a few weeks is worth having looked at.", (), None, "derm.when-to-see", ("nhs-contact-dermatitis", "nhs-atopic-eczema")))
    if duration_days is not None and duration_days >= 56 and _ACNE_WORDS.search(joined) and not any(r.id == "P-acne-needs-clinician" for r in fired):
        fired.append(BY_ID["P-acne-needs-clinician"])
    if not fired:
        return t
    top = max(LEVELS[r.level] for r in fired)
    t.level = next(k for k, v in LEVELS.items() if v == top)
    chosen = [r for r in fired if LEVELS[r.level] == top]
    t.rules = [r.id for r in fired]
    t.reasons = [r.reason for r in chosen][:2]
    t.article = next((r.article for r in chosen if r.article), None)
    t.sources = sorted({s for r in chosen for s in r.sources})
    return t


# ---------------------------------------------------------------- what the person is told
URGENT_BODY = ("This is the kind of problem that should be looked at today rather than waiting for an ordinary appointment. Please contact the clinic or an urgent care service today. "
               "If you become very unwell, get worse quickly or feel faint, use the emergency steps below.")
PROMPT_BODY = "It would be sensible to book an appointment with a dermatologist soon. This is not an emergency, and booking is the quickest way to get it looked at properly."


def message(t: Triage) -> str:
    """The wording shown with an escalation. For an emergency it is exactly the clinic's urgent-symptom policy message and nothing this code writes (the reason is a separate field of the card,
    shown beneath it by the app). For a same-day or soon level it is the reason followed by what to do."""
    if t.level == "emergency":
        return safety.urgent_message()
    if t.level == "urgent":
        return f"{' '.join(t.reasons)}\n\n{URGENT_BODY}"
    if t.level == "prompt":
        return f"{' '.join(t.reasons)}\n\n{PROMPT_BODY}"
    return ""


def review_sheet() -> list[dict]:
    """Every rule in words, for the clinicians who must approve them (docs/clinical_review/DERM_REVIEW_SHEET.md is generated from this)."""
    return [{"id": r.id, "level": r.level, "trigger": r.trigger, "reason_shown": r.reason, "article": r.article, "sources": list(r.sources), "status": "pending_clinician_review"} for r in RULES]


def policy_status() -> dict:
    p = clinical_content.urgent_policy()
    return {"version": VERSION, "rules": len(RULES), "status": "pending_clinician_review", "emergency_message_source": p.source, "emergency_message_approved": p.approved}

"""What is a message about? Topic detection and a few facts a person states (how long, which symptoms), in English, Hinglish and Hindi.

Deterministic and testable: a keyword index built from the pack's own keyword lists, plus small word lists for the facts. It never guesses a medical condition for a person: it only recognises
which article of general information a message is asking about, and which of the person's OWN statements (duration, symptoms, products, pregnancy) can make the answer more careful.
A negated mention ("no itching", "without fever") is not counted as a symptom."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

CONCERN_TOPICS = ("acne", "acne-marks", "pigmentation", "redness", "rosacea", "dry-skin", "eczema", "product-reactions", "moles")
GENERAL_TOPICS = ("sun-protection", "routine", "ingredients", "habits", "monitoring", "when-to-see", "warning-signs", "photo-limits")
TOPIC_LABEL = {
    "acne": "acne and pimples", "acne-marks": "marks and scars after acne", "pigmentation": "uneven skin tone and dark patches", "redness": "red or irritated skin", "rosacea": "rosacea and flushing",
    "dry-skin": "dry skin", "eczema": "eczema", "product-reactions": "a reaction to a product", "moles": "a mole or spot", "sun-protection": "sun protection", "routine": "a skincare routine",
    "ingredients": "skincare ingredients", "habits": "habits that irritate skin", "monitoring": "keeping track of a skin concern", "when-to-see": "when to see a clinician",
    "warning-signs": "warning signs", "photo-limits": "what a photo can show",
}

_NEG = re.compile(r"(?:\b(?:no|not|without|never|none|nothing|nope|nahi|nahin|nhi|bina|koi)\b|नहीं|बिना|कोई नहीं)\W+(?:\w+\W+){0,2}$", re.I)
SKIN_WORDS = re.compile(
    r"\b(?:skin|face|facial|acne|pimples?|zits?|spots?|marks?|scars?|rash(?:es)?|itch\w*|redness|red|bumps?|pores?|oily|dry|dryness|flak\w+|peel\w*|pigment\w*|melasma|tan(?:ning)?|sun ?screen|spf|"
    r"moistur\w+|cleanser|serum|cream|lotion|face ?wash|toner|retinol|eczema|rosacea|mole|moles|lips?|cheeks?|forehead|chin|nose|neck|scalp|hair|nails?|derm\w*|complexion|wrinkles?|"
    r"glow\w*|blackheads?|whiteheads?|dark circles?|stretch marks?|freckles?|sunburn\w*|hives|blisters?|boil|wart|warts|cyst|breakouts?|skincare|skin ?care|beauty|cosmetic\w*|make-?up|"
    r"twacha|tvacha|chehra|chehre|muhase|muhasa|daag|dhabbe|khujli|jalan|lalpan|sukhi|rukhi|त्वचा|चेहरा|चेहरे|मुंहासे|मुहांसे|दाग|धब्बे|खुजली|जलन|रैश|बाल)\b|त्वचा|चेहर|मुंहास|मुहांस|दाग|धब्बे|खुजली|जलन|झाइयां|झाइयाँ|रूखी|सूखी|पिग्मेंटेशन|सनस्क्रीन",
    re.I)

WANT_CUES: dict[str, re.Pattern] = {
    "what_it_is": re.compile(r"\b(?:what(?:'s| is| are)|explain|meaning|tell me about|describe|define|kya hai|kya hota|samjha\w*)\b|क्या है|समझा", re.I),
    "may_contribute": re.compile(r"\b(?:cause[sd]?|causing|why|reason|contribut\w+|trigger\w*|because|kyun|kyu|kaaran|karan)\b|क्यों|कारण", re.I),
    "self_care": re.compile(r"\b(?:treat\w*|cure|fix|get rid|remove|clear|reduce|heal\w*|manage|routine|care|how (?:do|can|to|should)|what (?:can|should) i (?:do|use)|suggest|recommend|help|improve|better|gentle|kaise|ilaj|upay|"
                            r"theek|hatao|hatane)\b|कैसे|इलाज|उपाय|ठीक", re.I),
    "avoid": re.compile(r"\b(?:avoid|worse|worsen\w*|make(?:s)? (?:it )?worse|mistakes?|bad for|harm\w*|damage|don'?t|do not|should not|shouldn'?t|nuksan|bachna|bachein|parhez)\b|बचना|नुकसान", re.I),
    "monitor": re.compile(r"\b(?:monitor\w*|track\w*|progress|keep an eye|watch|record|diary|log|compare|before and after|follow ?up)\b", re.I),
    "see_clinician": re.compile(r"\b(?:doctor|dermatologist|clinician|specialist|appointment|serious|dangerous|worried|worry|urgent|emergency|when (?:to|should)|should i see|need to see|safe)\b|डॉक्टर", re.I),
}

_WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "few": 3, "couple": 2, "several": 4,
            "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10}
_UNIT = {"day": 1, "days": 1, "din": 1, "dino": 1, "week": 7, "weeks": 7, "hafte": 7, "haphte": 7, "hafta": 7, "month": 30, "months": 30, "mahine": 30, "mahina": 30, "mahino": 30,
         "year": 365, "years": 365, "saal": 365, "sal": 365, "varsh": 365}
_DURATION = re.compile(r"\b(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|few|couple|several|ek|do|teen|char|paanch|panch|chhe|saat|aath|nau|das)\s+(?:of\s+)?"
                       r"(days?|din|dino|weeks?|hafte|haphte|hafta|months?|mahine|mahina|mahino|years?|saal|sal|varsh)\b", re.I)
_SINCE = re.compile(r"\bsince (?:yesterday|last night|this morning|today)\b|\bfrom (?:yesterday|last night)\b|\b(?:just|recently) (?:started|appeared|noticed)\b|\bnew\b", re.I)
_LONG = re.compile(r"\b(?:for ages|long time|since childhood|since i was (?:a )?(?:kid|child|teen\w*)|for years|always had)\b", re.I)

SYMPTOMS: dict[str, re.Pattern] = {
    "itch": re.compile(r"\bitch\w*|\bkhujli\b|खुजली", re.I),
    "pain": re.compile(r"\bpain\w*|\bsore\b|\btender\w*|\bhurts?\b|\bdard\b|दर्द", re.I),
    "swelling": re.compile(r"\bswell\w*|\bswollen\b|\bpuffy\b|\bsujan\b|सूजन", re.I),
    "burning": re.compile(r"\bburn(?:s|ing)?\b|\bsting\w*|\bjalan\b|जलन", re.I),
    "bleeding": re.compile(r"\bbleed\w*|\bblood\b|\bkhoon\b|खून", re.I),
    "spreading": re.compile(r"\bspread\w*|\bgetting bigger\b|\bgrowing\b|\bmore and more\b", re.I),
    "fever": re.compile(r"\bfever\w*|\btemperature\b|\bchills\b|\bbukhar\b|बुखार", re.I),
    "discharge": re.compile(r"\bpus\b|\booz\w+|\bweeping\b|\bdischarge\b|\bcrust\w*|\bmawad\b|मवाद", re.I),
    "blisters": re.compile(r"\bblister\w*|\bfluid[- ]filled\b|\bchhale\b|छाले", re.I),
    "change": re.compile(r"\bchang(?:e|ed|ing)\b|\bdarker\b|\bbigger\b|\bnew\b|\bdifferent\b", re.I),
}
NONE_OF = re.compile(r"\bnone of (?:these|those|the above)\b|\bno (?:other )?symptoms?\b|\bnothing like that\b|\bno itch\w*,? (?:no )?pain\b", re.I)
PRODUCTS = re.compile(r"(?<!should )(?<!can )(?<!could )(?<!shall )(?<!would )(?<!do )(?<!to )\b(?:i(?:'m| am)? (?:currently )?(?:using|applying|use|apply|used|applied|started|tried|trying)|(?:been|am|currently) (?:using|applying)|using)\b"
                      r".{0,60}\b(?:cream|gel|serum|face ?wash|cleanser|moisturi\w+|sunscreen|retinol|vitamin c|benzoyl|salicylic|acid|toner|lotion|oil|ointment|product\w*|soap)\b|\b(?:my|current) (?:routine|products?)\b", re.I)
PRIOR_REACTION = re.compile(r"\b(?:allergic to|allergy to|reacted to|reaction to|reactions? (?:to|after)|broke out (?:from|after|in)|sensitive to|bad reaction)\b|\bpast reactions?\b", re.I)
NO_PRIOR_REACTION = re.compile(r"\b(?:never|no|not) (?:reacted|had (?:a )?reaction|allergic|had allergies)\b|\bno (?:known )?(?:allergies|reactions?)\b", re.I)
PREGNANCY = re.compile(r"\b(?:pregnan\w*|expecting|breast ?feed\w*|nursing|trying to conceive|planning (?:a )?(?:pregnancy|baby))\b|गर्भवती|प्रेग्नेंट|स्तनपान", re.I)
CHILD = re.compile(r"\bmy (?:son|daughter|child|baby|infant|toddler|kid|newborn)\b|\b(?:baby|infant|newborn|toddler)\b|\b\d{1,2}[- ]?(?:year|yr)s?[- ]?old (?:boy|girl|child|kid)\b|\bmy (?:\d{1,2}|one|two|three|four|five)[- ]?(?:month|year)s?[- ]?old\b", re.I)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[^\w\sऀ-ॿ']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _has_word(norm: str, kw: str) -> bool:
    kw = normalize(kw)
    if not kw:
        return False
    if re.search(r"[ऀ-ॿ]", kw):
        return kw in norm
    return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", norm) is not None


def _negated(text: str, start: int) -> bool:
    return bool(_NEG.search(text[max(0, start - 36):start]))


def detect_topics(text: str, keyword_index: dict[str, list[str]]) -> list[tuple[str, int]]:
    """Topics the message is about, best first. `keyword_index` maps a topic to its keywords (from the pack). A longer keyword counts for more than a short one."""
    norm = normalize(text)
    scored: list[tuple[str, int]] = []
    for topic, kws in keyword_index.items():
        score = 0
        for kw in kws:
            if _has_word(norm, kw):
                score += 1 + 2 * (len(kw.split()) - 1) + (1 if len(kw) >= 8 else 0)
        if score:
            scored.append((topic, score))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored


def is_skin_related(text: str) -> bool:
    return bool(SKIN_WORDS.search(text or ""))


def wanted_sections(text: str) -> list[str]:
    """Which sections of an article the question asks for. No cue at all: what it is, gentle self-care, and when to see a clinician."""
    found = [sec for sec, rx in WANT_CUES.items() if rx.search(text or "")]
    if "see_clinician" in found:
        found = [f for f in found if f != "see_clinician"] + ["see_clinician", "urgent"]
    return found or ["what_it_is", "self_care", "see_clinician"]


def duration_days(text: str) -> int | None:
    best = None
    for m in _DURATION.finditer(text or ""):
        n = float(_WORDNUM.get(m.group(1).lower(), m.group(1)) if not m.group(1).replace(".", "").isdigit() else m.group(1))
        d = int(round(n * _UNIT[m.group(2).lower()]))
        best = d if best is None else max(best, d)
    if best is None and _LONG.search(text or ""):
        best = 365
    if best is None and _SINCE.search(text or ""):
        best = 1
    return best


@dataclass
class Slots:
    duration_days: int | None = None
    symptoms: set[str] = field(default_factory=set)
    none_of_symptoms: bool = False
    products: bool = False
    prior_reaction: bool | None = None
    pregnant: bool = False
    child: bool = False

    def known_symptoms(self) -> bool:
        return bool(self.symptoms) or self.none_of_symptoms

    def summary(self) -> list[str]:
        """What the person said, in neutral words, for the summary a dermatologist reads. Never more than they said."""
        out = []
        if self.duration_days is not None:
            d = self.duration_days
            out.append("present for about " + (f"{d} day(s)" if d < 14 else f"{round(d / 7)} week(s)" if d < 90 else f"{round(d / 30)} month(s)" if d < 730 else f"{round(d / 365)} year(s)"))
        if self.symptoms:
            out.append("mentioned: " + ", ".join(sorted(self.symptoms)))
        elif self.none_of_symptoms:
            out.append("no itching, pain or swelling mentioned")
        if self.products:
            out.append("mentioned using skincare products")
        if self.prior_reaction is True:
            out.append("mentioned earlier reactions to products")
        elif self.prior_reaction is False:
            out.append("no earlier reactions to products reported")
        if self.pregnant:
            out.append("mentioned pregnancy or breastfeeding")
        return out


def extract_slots(texts: list[str]) -> Slots:
    """Facts the person stated in their own messages (the most recent ones). A negated mention is not a symptom; nothing is inferred beyond the words."""
    s = Slots()
    for text in texts:
        if not text:
            continue
        d = duration_days(text)
        if d is not None:
            s.duration_days = d if s.duration_days is None else max(s.duration_days, d)
        for name, rx in SYMPTOMS.items():
            for m in rx.finditer(text):
                if not _negated(text, m.start()):
                    s.symptoms.add(name)
                    break
        if NONE_OF.search(text):
            s.none_of_symptoms = True
        if PRODUCTS.search(text):
            s.products = True
        if NO_PRIOR_REACTION.search(text):                         # "I have never reacted to ..." also contains the words of a reaction: the negation is read first, and the latest statement wins
            s.prior_reaction = False
        elif PRIOR_REACTION.search(text):
            s.prior_reaction = True
        if PREGNANCY.search(text):
            s.pregnant = True
        if CHILD.search(text):
            s.child = True
    return s

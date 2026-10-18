"""Rule-based extraction of printed medicine entries from prescriptions and clinical notes.

What it does: finds lines that look like a medicine entry and splits each into the components the document actually prints: name, form, strength(s), dose per administration, a
dose pattern such as 1-0-1, route, frequency, duration, instructions, and anything it could not place. Every component carries the ORIGINAL text and its position in the page text.

What it never does:
* recommend, start, stop, change or adjust anything; an instruction printed in the document is quoted as printed and flagged, nothing more;
* substitute, translate or "correct" a name: no brand/generic mapping exists here; a name printed in brackets beside another is recorded as printed ("alias"), not asserted equal;
* fill a missing component: an absent strength/frequency/duration stays absent and is flagged;
* convert units, or choose between readings: ambiguous shorthand (a "5/7" duration, a "1-0-1" slot pattern, "OD" next to an eye medicine, a bare number after a name) keeps its raw
  text, gets a stated ambiguity code and an empty or explicitly-assumed normalisation. Shorthand expansion ("BD" -> "twice daily") uses a fixed lexicon and is recorded as an expansion.
Normalised values and inferences are separate from the extracted text and are labelled as such. Every record is a draft for clinician review (`needs_review`)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import languages

VERSION = "medparse-1.1.0"

FORMS = {"tab": "tablet", "tabs": "tablet", "tablet": "tablet", "tablets": "tablet", "cap": "capsule", "caps": "capsule", "capsule": "capsule", "capsules": "capsule",
         "syp": "syrup", "syrup": "syrup", "susp": "suspension", "suspension": "suspension", "inj": "injection", "injection": "injection", "oint": "ointment", "ointment": "ointment",
         "crm": "cream", "cream": "cream", "gel": "gel", "lotion": "lotion", "drop": "drops", "drops": "drops", "shampoo": "shampoo", "spray": "spray", "soln": "solution",
         "solution": "solution", "sachet": "sachet", "powder": "powder", "patch": "patch", "soap": "soap", "serum": "serum"}
ABBREVIATED_FORMS = {"tab", "tabs", "cap", "caps", "syp", "susp", "inj", "oint", "crm", "soln"}
_FORM_ALT = "|".join(sorted(FORMS, key=len, reverse=True))
SITE_WORD = {"eye": "ophthalmic", "ear": "otic", "nasal": "nasal"}
PREFIX = re.compile(rf"^\s*(?:\d{{1,2}}\s*[.)]\s*)?(?:rx\s*[:.]?\s*)?(?:(?P<site>eye|ear|nasal)\s+)?(?P<form>{_FORM_ALT})\b\.?\s*", re.I)
T_PREFIX = re.compile(r"^\s*(?:\d{1,2}\s*[.)]\s*)?(?:rx\s*[:.]?\s*)?(?P<form>T)\.\s+(?=[A-Z])")
LEAD = re.compile(r"^\s*(?:\d{1,2}\s*[.)]\s*)?(?:rx\s*[:.]?\s*)?", re.I)
FORM_AFTER = re.compile(rf"\b(?P<form>{_FORM_ALT})\b", re.I)

FREQ_ABBR = {"od": ("once daily", 1, None), "qd": ("once daily", 1, None), "bd": ("twice daily", 2, None), "bid": ("twice daily", 2, None), "tds": ("three times daily", 3, None),
             "tid": ("three times daily", 3, None), "qid": ("four times daily", 4, None), "qds": ("four times daily", 4, None), "hs": ("at bedtime", 1, None), "sos": ("as needed", None, None),
             "prn": ("as needed", None, None), "stat": ("immediately, once", None, None), "qod": ("every other day", None, None), "q4h": ("every 4 hours", 6, None),
             "q6h": ("every 6 hours", 4, None), "q8h": ("every 8 hours", 3, None), "q12h": ("every 12 hours", 2, None)}
ABBR_RE = re.compile(r"\b(" + "|".join(sorted(FREQ_ABBR, key=len, reverse=True)) + r")\b\.?", re.I)
FREQ_PHRASES = [(re.compile(p, re.I), plain, per_day, per_week) for p, plain, per_day, per_week in (
    (r"\bonce (?:a|per) day\b|\bonce daily\b", "once daily", 1, None), (r"\btwice (?:a|per) day\b|\btwice daily\b", "twice daily", 2, None),
    (r"\b(?:thrice|three times) (?:a|per) day\b|\b(?:thrice|three times) daily\b", "three times daily", 3, None), (r"\bfour times (?:a|per) day\b|\bfour times daily\b", "four times daily", 4, None),
    (r"\bonce (?:a|per) week\b|\bonce weekly\b", "once weekly", None, 1), (r"\btwice (?:a|per) week\b|\btwice weekly\b", "twice weekly", None, 2),
    (r"\b(?:thrice|three times) (?:a|per) week\b|\b(?:thrice|three times) weekly\b", "three times weekly", None, 3), (r"\bweekly\b", "weekly", None, None),
    (r"\bonce (?:a|per) month\b|\bonce monthly\b|\bmonthly\b", "monthly", None, None), (r"\balternate days\b|\bevery other day\b", "every other day", None, None),
    (r"\bat (?:night|bedtime)\b", "at night", None, None), (r"\bdaily\b", "daily", None, None),
    (r"\b(?:as|when|if)\s+(?:needed|required|necessary)\b", "as needed", None, None))]
EVERY_N_HOURS = re.compile(r"\bevery\s+(\d{1,2})\s*(?:hours?|hrs?|h)\b", re.I)

# strengths: concentrations (mg/ml) first, then plain amounts. A bare volume (5 ml) is a DOSE, never a strength.
CONCENTRATION = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(mg|mcg|µg|μg|ug|g|gm|iu|units?)\s*/\s*(\d+(?:[.,]\d+)?)?\s*(ml|l|g)\b", re.I)
STRENGTH = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(mg|mcg|µg|μg|ug|gm|gms|g|iu|units?|meq|mmol|%)(?![\w])(?!\s*/\s*(?:dl|l|ml|min|hr|h|kg|\d))", re.I)
LETTER_STRENGTH = re.compile(r"(?<![\w.,])((?=[0-9OolI]*[0-9])(?=[0-9OolI]*[OolI])[0-9OolI]{2,6})\s*(mg|mcg|gm|iu)\b", re.I)
STRENGTH_UNIT = {"mg": "mg", "mcg": "mcg", "µg": "mcg", "μg": "mcg", "ug": "mcg", "g": "g", "gm": "g", "gms": "g", "iu": "IU", "unit": "unit", "units": "unit", "meq": "mEq", "mmol": "mmol", "%": "%"}
DOSE = re.compile(r"(?<![\w.,/-])(\d+(?:[./]\d+)?|½|¼)\s*(tabs?|tablets?|caps?|capsules?|ml|drops?|puffs?|sachets?|spoons?|tsp|tbsp|units?|pumps?)\b(?!\s*/)", re.I)
SLOTS = re.compile(r"(?<![\w./-])(\d(?:[./]\d)?|½|¼)\s*-\s*(\d(?:[./]\d)?|½|¼)\s*-\s*(\d(?:[./]\d)?|½|¼)(?![\w./-])")
DURATION_NUM = re.compile(r"(?:\bx\b|×|\bfor\b|\bup\s*to\b|\bupto\b|\bnext\b)\s*(\d{1,3})\s*(days?|d|weeks?|wks?|months?|mos?|years?|yrs?)\b", re.I)
DURATION_SHORT = re.compile(r"(?:\bx\b|×|\bfor\b)\s*(\d{1,2})\s*/\s*(7|52|12)\b", re.I)
DURATION_OPEN = re.compile(r"\b(?:till|until)\s+(?:review|further notice|follow[- ]?up|finished|next visit)\b|\bcontinue\b|\bongoing\b|\blong[- ]term\b|\blife[- ]?long\b", re.I)
DURATION_UNIT = {"d": "day", "day": "day", "days": "day", "wk": "week", "wks": "week", "week": "week", "weeks": "week", "mo": "month", "mos": "month", "month": "month", "months": "month",
                 "yr": "year", "yrs": "year", "year": "year", "years": "year"}
ROUTES = [(re.compile(p, re.I), norm) for p, norm in (
    (r"\b(?:p\.?o\.?|orally|by mouth)\b", "oral"), (r"\b(?:topical(?:ly)?|external use|for external use)\b", "topical"), (r"\b(?:i\.?m\.?|intramuscular(?:ly)?)\b", "intramuscular"),
    (r"\b(?:i\.?v\.?|intravenous(?:ly)?)\b", "intravenous"), (r"\b(?:s\.?c\.?|s\.?q\.?|subcutaneous(?:ly)?)\b", "subcutaneous"), (r"\bintralesional(?:ly)?\b", "intralesional"),
    (r"\bintradermal(?:ly)?\b", "intradermal"), (r"\b(?:inhal(?:ed|ation)|by inhaler)\b", "inhaled"), (r"\bnasal(?:ly)?\b", "nasal"), (r"\b(?:ophthalmic|(?:in |into |to )?(?:the )?(?:left|right|both)\s+eyes?|in (?:the )?eyes?)\b", "ophthalmic"),
    (r"\b(?:otic|in (?:the )?ears?)\b", "otic"), (r"\b(?:p\.?r\.?|rectal(?:ly)?)\b", "rectal"), (r"\b(?:s\.?l\.?|sublingual(?:ly)?)\b", "sublingual"))]
FOOD = re.compile(r"\b(?:before|after|with|without|during)\s+(?:food|meals?|breakfast|lunch|dinner|eating)\b|\b(?:on\s+an?\s+)?empty\s+stomach\b|\bwith\s+(?:water|milk)\b", re.I)
MEAL_ABBR = re.compile(r"\b(a\.?c|p\.?c|i\.?c)\b\.?", re.I)
MEAL_EXPANSION = {"ac": "before meals", "pc": "after meals", "ic": "between meals"}
TIMING = re.compile(r"\b(?:in\s+the\s+)?(?:morning|evening|afternoon)\b|\b(?:before|after)\s+(?:night|bedtime|sleep)\b", re.I)
APPLY = re.compile(r"\b(?:apply|massage|rub|spread|dab|wash\s+off|leave\s+on)\b[^.;,\n]{0,80}", re.I)
CAUTION = re.compile(r"\b(?:avoid|do\s+not|don't|shake\s+well|keep\s+(?:away|in)|protect\s+from)\b[^.;\n]{0,70}", re.I)
CHANGE_DIRECTIVE = re.compile(r"\b(?:stop|discontinue|omit|hold|withhold|increase|decrease|reduce|taper|switch|replace)\b[^.;\n]{0,60}", re.I)
EYE_CONTEXT = re.compile(r"\b(?:eye|eyes|ophthalmic|ocular)\b", re.I)
PAREN = re.compile(r"\(([^()]{2,60})\)")
UNKNOWN_ABBR = re.compile(r"\b[A-Z]{2,5}\b")
KNOWN_UPPER = {"OD", "QD", "BD", "BID", "TDS", "TID", "QID", "QDS", "HS", "SOS", "PRN", "STAT", "QOD", "IM", "IV", "SC", "SQ", "PO", "PR", "SL", "IU", "MG", "ML", "MCG", "RX", "AC", "PC", "IC", "UV", "SPF"}
FILLER = {"take", "takes", "use", "and", "the", "of", "a", "an", "to", "each", "per", "then", "tablet", "capsule", "tablets", "capsules", "with", "on", "in", "for", "by", "at", "or", "x"}
SECTION_END = re.compile(r"^\s*(?:signature|signed|doctor|dr\.?\s|follow[- ]?up|review|advice|investigations?|diagnosis|date|next visit|reg\.?\s*no|registration|allerg)", re.I)


@dataclass
class Component:
    raw: str
    span: tuple[int, int]
    normalized: object = None
    note: str = ""

    def to_dict(self) -> dict:
        return {"raw": self.raw, "span": list(self.span), "normalized": self.normalized, "note": self.note}


@dataclass
class Medication:
    page: int
    span: tuple[int, int]
    raw: str
    line_spans: list = field(default_factory=list)
    name: Component | None = None
    alias: Component | None = None
    extra_names: list = field(default_factory=list)
    form: Component | None = None
    strengths: list = field(default_factory=list)
    dose: Component | None = None
    dose_pattern: Component | None = None
    route: Component | None = None
    frequency: Component | None = None
    duration: Component | None = None
    instructions: list = field(default_factory=list)
    remainder: Component | None = None
    ambiguity: list = field(default_factory=list)
    inferences: list = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict:
        c = lambda x: x.to_dict() if x is not None else None  # noqa: E731
        return {"page": self.page, "span": list(self.span), "raw": self.raw, "line_spans": [list(s) for s in self.line_spans], "name": c(self.name), "alias": c(self.alias),
                "extra_names": [x.to_dict() for x in self.extra_names], "form": c(self.form), "strengths": [s.to_dict() for s in self.strengths], "dose": c(self.dose),
                "dose_pattern": c(self.dose_pattern), "route": c(self.route), "frequency": c(self.frequency), "duration": c(self.duration),
                "instructions": [i.to_dict() for i in self.instructions], "remainder": c(self.remainder), "ambiguity": self.ambiguity, "inferences": self.inferences,
                "confidence": round(self.confidence, 2), "needs_review": True, "parser": VERSION}


class _Text:
    """The item's lines joined with single spaces, with a map from every character back to its offset in the page text (so spans point at the original)."""

    def __init__(self, lines: list[tuple[str, int]]):
        self.text = ""
        self.map: list[int] = []
        for i, (ln, start) in enumerate(lines):
            if i:
                self.text += " "
                self.map.append(self.map[-1] + 1)
            for j, ch in enumerate(ln):
                self.text += ch
                self.map.append(start + j)

    def span(self, a: int, b: int) -> tuple[int, int]:
        return (self.map[a], self.map[b - 1] + 1) if b > a and self.map else (0, 0)

    def comp(self, a: int, b: int, normalized=None, note: str = "") -> Component:
        return Component(self.text[a:b], self.span(a, b), normalized, note)


def _num(s: str) -> float | None:
    s = languages.to_ascii_digits(s)
    if "," in s:
        return None                                          # a decimal comma and a thousands comma cannot be told apart: no value
    try:
        return float(s)
    except ValueError:
        return None


def _is_item_start(line: str, doc_type: str) -> bool:
    if PREFIX.match(line):
        return True
    if doc_type in ("prescription", "consultation_note", "treatment_history"):
        if T_PREFIX.match(line) and (STRENGTH.search(line) or DOSE.search(line)) and (ABBR_RE.search(line) or SLOTS.search(line)):
            return True                                        # "T. Zincovit 1 tab OD": the common shorthand for a tablet, accepted only when the rest of the line looks like an entry
        return bool((STRENGTH.search(line) or CONCENTRATION.search(line)) and (ABBR_RE.search(line) or SLOTS.search(line) or DURATION_NUM.search(line))
                    and re.match(r"^\s*(?:\d{1,2}\s*[.)]\s*)?(?:rx\s*[:.]?\s*)?[A-Za-z]", line))
    return False


def _continuation(line: str, doc_type: str) -> bool:
    s = line.strip()
    if not s or _is_item_start(line, doc_type) or SECTION_END.match(line):
        return False
    return bool(line[:1] in (" ", "\t") or s[0].islower() or s[0] in "-(–—" or re.match(r"(?i)^(?:x\s|for\s|after|before|with|apply|at\s|\d+\s*-\s*\d)", s))


def parse_item(lines: list[tuple[str, int]], page: int, page_conf: float | None, method: str) -> Medication:
    t = _Text(lines)
    s = t.text
    med = Medication(page, (t.map[0], t.map[-1] + 1), s, [(st, st + len(ln)) for ln, st in lines])
    used: list[tuple[int, int]] = []

    def take(a: int, b: int):
        used.append((a, b))

    def overlaps(a: int, b: int) -> bool:
        return any(a < y and x < b for x, y in used)

    # ---- the printed prefix: numbering, "Rx", an optional site word ("Eye drops") and the form. Abbreviations are expanded from a fixed list and that is recorded.
    pm = PREFIX.match(s)
    tm = None if pm else T_PREFIX.match(s)
    if tm:
        med.form = t.comp(tm.start("form"), tm.end("form"), "tablet", "'T.' read as 'tablet' (a common shorthand, not stated in the document)")
        med.ambiguity += ["abbreviation_expanded:form", "ambiguous_form_abbreviation"]
        pos = tm.end()
    elif pm:
        f = pm.group("form")
        med.form = t.comp(pm.start("form"), pm.end("form"), FORMS[f.lower()], "abbreviation expanded from a fixed list" if f.lower() in ABBREVIATED_FORMS else "")
        if f.lower() in ABBREVIATED_FORMS:
            med.ambiguity.append("abbreviation_expanded:form")
        if pm.group("site"):
            site = pm.group("site")
            med.route = t.comp(pm.start("site"), pm.end("site"), SITE_WORD[site.lower()], "from the printed product name (for example 'Eye drops'), not a separate route statement")
        pos = pm.end()
    else:
        pos = LEAD.match(s).end()
    if pos:
        take(0, pos)
    # ---- strengths: concentrations, then amounts; OCR-confusable digits are kept raw and flagged
    for m in CONCENTRATION.finditer(s, pos):
        med.strengths.append(t.comp(m.start(), m.end(), {"value": _num(m.group(1)), "unit": f"{STRENGTH_UNIT.get(m.group(2).lower(), m.group(2))}/{m.group(3) or ''}{m.group(4).lower()}"}))
        take(m.start(), m.end())
    for m in STRENGTH.finditer(s, pos):
        if overlaps(m.start(), m.end()):
            continue
        unit = STRENGTH_UNIT[m.group(2).lower()]
        note = "decimal comma: value not normalised" if "," in m.group(1) else "unit symbol expanded from a fixed list" if m.group(2).lower() in ("µg", "μg", "ug", "gm", "gms") else ""
        med.strengths.append(t.comp(m.start(), m.end(), {"value": _num(m.group(1)), "unit": unit}, note))
        take(m.start(), m.end())
        if "," in m.group(1):
            med.ambiguity.append("decimal_comma")
    for m in LETTER_STRENGTH.finditer(s, pos):
        if overlaps(m.start(), m.end()):
            continue
        med.strengths.append(t.comp(m.start(), m.end(), {"value": None, "unit": STRENGTH_UNIT[m.group(2).lower()]}, "digits mixed with look-alike letters: value not normalised"))
        take(m.start(), m.end())
        med.ambiguity.append("possible_letter_digit_confusion")
    med.strengths.sort(key=lambda c: c.span)
    if len(med.strengths) > 1:
        a, b = med.strengths[0], med.strengths[1]
        gap = s[s.index(a.raw, 0) + len(a.raw): s.index(b.raw, s.index(a.raw) + len(a.raw))] if a.raw in s and b.raw in s else ""
        combo = bool(re.search(r"[+&]|\band\b", gap))
        med.ambiguity.append("combination_or_multiple_strengths" if combo or len(gap.strip()) <= 1 else "multiple_strengths_unexplained")
        if combo:
            ingredient = re.sub(r"^[\s+&]*(?:and\s+)?", "", gap).strip(" +&-")
            if ingredient and re.search(r"[A-Za-z]{3}", ingredient):
                i0 = s.index(ingredient, s.index(a.raw))
                med.extra_names.append(t.comp(i0, i0 + len(ingredient), None, "second ingredient printed after '+' in a combination; not asserted to be a separate product"))
                take(i0, i0 + len(ingredient))
    # ---- frequency (abbreviation or phrase); conflicting readings are reported, not chosen between silently
    cands = []
    for m in ABBR_RE.finditer(s, pos):
        if not overlaps(m.start(), m.end()):
            plain, n, w = FREQ_ABBR[m.group(1).lower()]
            cands.append((m.start(), m.end(), plain, n, w, True))
    for rx, plain, per_day, per_week in FREQ_PHRASES:
        for m in rx.finditer(s, pos):
            if not overlaps(m.start(), m.end()):
                cands.append((m.start(), m.end(), plain, per_day, per_week, False))
    for m in EVERY_N_HOURS.finditer(s, pos):
        h = int(m.group(1))
        cands.append((m.start(), m.end(), f"every {h} hours", round(24 / h, 1) if h and 24 % h == 0 else None, None, False))
    cands.sort(key=lambda c: (c[0], -(c[1] - c[0])))
    kept = []
    for c in cands:                                           # drop a phrase contained in a longer one ("weekly" inside "twice weekly")
        if not any(o is not c and o[0] <= c[0] and c[1] <= o[1] and (o[1] - o[0]) > (c[1] - c[0]) for o in cands):
            kept.append(c)
    as_needed = [c for c in kept if c[2] == "as needed"]
    others = [c for c in kept if c[2] != "as needed"]
    if as_needed and others:                                  # "q6h prn", "BD as needed": one frequency with an as-needed condition, when the two are adjacent
        f0, an = others[0], as_needed[0]
        lo, hi = (f0, an) if f0[0] < an[0] else (an, f0)
        if not s[lo[1]:hi[0]].strip():
            kept = [(lo[0], hi[1], f"{f0[2]}, as needed", f0[3], f0[4], True)]
            as_needed = []
    if kept:
        a, b, plain, per_day, per_week, abbr = kept[0]
        med.frequency = t.comp(a, b, {"plain": plain, "times_per_day": per_day, "times_per_week": per_week}, "abbreviation expanded from a fixed list" if abbr else "")
        take(a, b)
        if abbr:
            med.ambiguity.append("abbreviation_expanded:frequency")
        distinct = {c[2] for c in kept}
        if len(distinct) > 1:
            med.ambiguity.append("conflicting_frequencies:" + "|".join(sorted(distinct)))
        if plain == "as needed":
            med.ambiguity.append("as_needed_no_fixed_frequency")
        elif plain.endswith(", as needed"):
            med.ambiguity.append("as_needed_condition")
        if abbr and med.frequency.raw.rstrip(".").lower() == "od" and EYE_CONTEXT.search(s):
            med.ambiguity.append("od_could_mean_right_eye_or_once_daily")
    # ---- slot pattern such as 1-0-1: the convention (morning-afternoon-night) is NOT stated in the document
    sm = SLOTS.search(s, pos)
    if sm and not overlaps(sm.start(), sm.end()):
        med.dose_pattern = t.comp(sm.start(), sm.end(), {"morning": sm.group(1), "afternoon": sm.group(2), "night": sm.group(3)},
                                  "three-slot pattern; the slot order is assumed to be morning-afternoon-night (not stated in the document)")
        take(sm.start(), sm.end())
        med.ambiguity.append("slot_pattern_convention_assumed")
        if med.form and med.form.normalized == "tablet":
            med.inferences.append("Each number in the slot pattern is read as a number of tablets because the printed form is a tablet (inferred, not printed).")
    # ---- dose per administration
    for dm in DOSE.finditer(s, pos):
        if not overlaps(dm.start(), dm.end()):
            med.dose = t.comp(dm.start(), dm.end(), {"quantity": dm.group(1), "unit": dm.group(2).lower()})
            take(dm.start(), dm.end())
            break
    # ---- duration
    ds, dn, do = DURATION_SHORT.search(s, pos), DURATION_NUM.search(s, pos), DURATION_OPEN.search(s, pos)
    if ds and not dn and not overlaps(ds.start(), ds.end()):
        med.duration = t.comp(ds.start(), ds.end(), None, "shorthand N/7 (read as N days) is a convention: kept as printed, not normalised")
        take(ds.start(), ds.end())
        med.ambiguity.append("ambiguous_duration_shorthand")
    elif dn and not overlaps(dn.start(), dn.end()):
        med.duration = t.comp(dn.start(), dn.end(), {"value": int(dn.group(1)), "unit": DURATION_UNIT[dn.group(2).lower()]},
                              "abbreviation expanded from a fixed list" if dn.group(2).lower() in ("d", "wk", "wks", "mo", "mos", "yr", "yrs") else "")
        take(dn.start(), dn.end())
    elif do and not overlaps(do.start(), do.end()):
        med.duration = t.comp(do.start(), do.end(), None, "open-ended wording kept as printed")
        take(do.start(), do.end())
        med.ambiguity.append("open_ended_duration")
    # ---- route: explicit text only (an eye/ear/nasal product name already set it above)
    if med.route is None:
        for rx, norm in ROUTES:
            m = rx.search(s, pos)
            if m and not overlaps(m.start(), m.end()):
                med.route = t.comp(m.start(), m.end(), norm, "abbreviation expanded from a fixed list" if len(m.group(0)) <= 3 else "")
                take(m.start(), m.end())
                break
    # ---- a form printed AFTER the name and strength ("Doxycycline 100 mg tablet")
    if med.form is None:
        for fm in FORM_AFTER.finditer(s, pos):
            if not overlaps(fm.start(), fm.end()):
                med.form = t.comp(fm.start("form"), fm.end("form"), FORMS[fm.group("form").lower()])
                take(fm.start(), fm.end())
                break
    # ---- instructions: quoted as printed
    for rx, kind in ((FOOD, "food"), (MEAL_ABBR, "meal_abbreviation"), (TIMING, "timing"), (APPLY, "application"), (CAUTION, "caution"), (CHANGE_DIRECTIVE, "directive_as_printed")):
        for m in rx.finditer(s, pos):
            if kind in ("timing", "meal_abbreviation") and overlaps(m.start(), m.end()):
                continue
            norm = kind
            if kind == "meal_abbreviation":
                norm = {"text": MEAL_EXPANSION[re.sub(r"[.]", "", m.group(1)).lower()], "kind": "food"}
                med.ambiguity.append("abbreviation_expanded:instruction")
            med.instructions.append(t.comp(m.start(), m.end(), norm, "abbreviation expanded from a fixed list" if kind == "meal_abbreviation" else ""))
            if kind != "timing" or True:
                take(m.start(), m.end())
            if kind == "directive_as_printed":
                med.ambiguity.append("contains_change_directive_as_printed")
    # ---- name: the printed text after the prefix up to the first recognised component, in the printed spelling
    cut = min([a for a, b in used if a >= pos] + [len(s)])
    seg = s[pos:cut]
    pa = PAREN.search(seg)
    if pa:
        med.alias = Component(pa.group(1).strip(), t.span(pos + pa.start(1), pos + pa.end(1)), None, "printed in brackets beside the name; not asserted to be equivalent")
        take(pos + pa.start(), pos + pa.end())
        seg = seg[:pa.start()] + seg[pa.end():]
    head = re.split(r"\s[-–—:]\s|,|;", seg)[0]
    trailing_number = re.search(r"\s(\d{1,4})\s*$", head)
    nm_txt = head[:trailing_number.start()] if trailing_number else head
    lead = len(nm_txt) - len(nm_txt.lstrip())
    nm_clean = nm_txt.strip().strip(".:-–—,;")
    if nm_clean:
        a = pos + lead
        med.name = t.comp(a, a + len(nm_txt.rstrip()), nm_clean)
        take(a, a + len(nm_txt.rstrip()))
        if re.search(r"[A-Za-z]\d{2,}$", nm_clean):
            med.ambiguity.append("digits_attached_to_name")
        elif re.search(r"\d", nm_clean):
            med.ambiguity.append("digits_in_name")
    else:
        med.ambiguity.append("name_missing")
    if trailing_number and not med.strengths and med.name:           # "Folvite 5": a number with no unit may be a strength whose unit was lost or never printed
        a = pos + trailing_number.start(1)
        med.strengths.append(t.comp(a, a + len(trailing_number.group(1)), {"value": float(trailing_number.group(1)), "unit": None}, "no unit printed"))
        take(a, a + len(trailing_number.group(1)))
        med.ambiguity.append("strength_unit_missing")
    elif trailing_number:
        pass
    # ---- checks
    if languages.has_non_latin_digits(s):
        med.ambiguity.append("non_latin_digits")
    known_words = {w.upper() for c in ([med.name, med.alias, *med.extra_names] if med.name else []) if c for w in re.findall(r"[A-Za-z]+", c.raw)}
    unknown = sorted({w for w in UNKNOWN_ABBR.findall(s) if w not in KNOWN_UPPER and w not in known_words})
    if unknown:
        med.ambiguity.append("unrecognised_abbreviation:" + ",".join(unknown[:4]))
    if not med.strengths and not med.dose:
        med.ambiguity.append("strength_and_dose_missing")
    if not med.frequency and not med.dose_pattern:
        med.ambiguity.append("frequency_missing")
    if method == "ocr" and (page_conf or 0) < 0.6:
        med.ambiguity.append("ocr_low_confidence")
    if s.rstrip().endswith("-"):
        med.ambiguity.append("line_may_be_truncated")
    # ---- what was not understood is shown, not dropped
    keep = [True] * len(s)
    for a, b in used:
        for i in range(max(a, 0), min(b, len(s))):
            keep[i] = False
    rest = "".join(ch if keep[i] else " " for i, ch in enumerate(s))
    words = [w for w in re.split(r"[\s.,;:()\-–—+/&]+", rest) if w and w.lower() not in FILLER]
    if len(re.sub(r"[^A-Za-z0-9]", "", "".join(words))) >= 3:
        first = next(i for i, k in enumerate(keep) if k and s[i].isalnum() and rest[i:].split()[0].lower() not in FILLER)
        med.remainder = Component(" ".join(words), t.span(first, first + 1), None, "text on the line that was not recognised as any component")
        med.ambiguity.append("unparsed_text_remains")
    # ---- confidence: how complete the entry is, how readable the page was, less a little per ambiguity
    base = 0.9 if method in ("embedded", "plain") else min(0.9, page_conf or 0.5) * 0.85
    complete = sum(bool(x) for x in (med.name, med.strengths or med.dose, med.frequency or med.dose_pattern)) / 3
    med.confidence = max(0.1, base * (0.6 + 0.4 * complete) - 0.04 * len([a for a in med.ambiguity if not a.startswith("abbreviation_expanded")]))
    return med


def extract_medications(pages, doc_type: str = "other_uncertain") -> list[Medication]:
    """Medicine entries from page text. Lab-style documents yield nothing unless a line carries an explicit form prefix (Tab., Cap., Syp. ...)."""
    out: list[Medication] = []
    for p in pages:
        raw_lines = p.text.splitlines(keepends=True)
        offs, pos = [], 0
        for rl in raw_lines:
            offs.append(pos)
            pos += len(rl)
        lines = [(rl.rstrip("\r\n"), o) for rl, o in zip(raw_lines, offs)]
        prefixed = any(PREFIX.match(l) for l, _ in lines)
        if doc_type == "laboratory_report" and not prefixed:
            continue
        i = 0
        while i < len(lines):
            ln, off = lines[i]
            if ln.strip() and _is_item_start(ln, doc_type):
                group = [(ln, off)]
                j = i + 1
                while j < len(lines) and len(group) < 3 and _continuation(lines[j][0], doc_type):
                    group.append(lines[j])
                    j += 1
                out.append(parse_item(group, p.page_no, p.confidence, p.method))
                i = j
            else:
                i += 1
    return out

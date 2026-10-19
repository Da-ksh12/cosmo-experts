"""Typed facts printed in the header and body of reports and prescriptions: report and collection dates, the laboratory or provider, a referring or reporting clinician, an
explicit allergy statement and an explicit follow-up instruction.

Rules (tests/test_documents_structured.py):
* A fact exists ONLY when the document says it. Nothing is inferred from silence: no allergy line means no allergy fact (it does NOT mean "no allergies"); no follow-up wording means no
  follow-up fact.
* The printed text is kept exactly (`value`), with its page and character span. A normalised reading, when one is safe, sits beside it in `normalized`.
* A date whose day/month order cannot be known (12/03/2024) is NOT silently resolved: `normalized` carries both candidate readings and `iso` is empty. Two-digit years are likewise
  not guessed. An unambiguous date (day > 12, month name, ISO order) gets an `iso`.
* Names of clinicians are copied as printed with any printed qualifications; no credential is verified here.
* The patient's own identifiers (name, age, ids, phone) are deliberately NOT extracted: they are not needed for any feature, and not collecting them is data minimisation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
DATE_TOKEN = (r"(?:\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{2,4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})")
REPORT_DATE = re.compile(rf"(?i)\b(?:report(?:ed)?\s*(?:date|on)?|date\s+of\s+report)\b\s*[:\-]?\s*(?P<d>{DATE_TOKEN})")
COLLECTION_DATE = re.compile(rf"(?i)\b(?:collect(?:ed|ion)(?:\s+(?:date|on))?|sample\s+(?:collected|date)|specimen\s+(?:collected|date)|date\s+of\s+collection)\b\s*[:\-]?\s*(?P<d>{DATE_TOKEN})")
GENERIC_DATE = re.compile(rf"(?i)^\s*date\s*[:\-]\s*(?P<d>{DATE_TOKEN})")
PROVIDER_WORDS = re.compile(r"(?i)\b(laborator(?:y|ies)|diagnostics?|pathology|path\s*labs?|clinic|hospital|medical\s+cent(?:re|er)|health\s*care|imaging|labs?)\b")
NOT_PROVIDER = re.compile(r"(?i)^\s*(?:patient|name|age|sex|gender|ref|referred|reg|uhid|sample|specimen|phone|tel|mobile|email|address|date|time|dr\b)")
# A printed name is a run of capitalised words; a single-letter initial counts ("Dr. A Example", "Dr. R K Sharma"). Only the title and the cue words ignore case.
_NAME = r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3}"
_QUALS = r"(?:\s*,?\s*(?:MD|MBBS|DNB|DVD|DDVL|MS|PhD|DM)\b[A-Za-z. ,]{0,20})?"
CLINICIAN_CUE = re.compile(r"(?i)\b(?P<cue>referred\s*by|ref\.?\s*by|referring\s+(?:doctor|physician)|prescribed\s*by|consultant|reported\s*by|pathologist|verified\s*by)\b\s*[:\-]?\s*"
                           r"(?P<n>(?:dr\.?\s*)?(?-i:" + _NAME + r")" + _QUALS + r")")
CLINICIAN_LINE = re.compile(r"^\s*(?P<n>Dr\.?\s+" + _NAME + _QUALS + r")\s*$")
QUALIFICATION = re.compile(r"\b(?:MD|MBBS|DNB|DVD|DDVL|MS|PhD|DM)\b")


def _plausible_name(n: str) -> bool:
    """A printed clinician name has a title (Dr), or a printed qualification (MD ...), or at least two capitalised words - and at least one word that is not the title or the qualification.
    "Self", "Lab technician" and "Consultant Dermatologist" are not names; a title or a qualification alone is not a name either."""
    n = n.strip()
    core = re.sub(r"(?i)^dr\b\.?\s*", "", n)
    words = re.findall(r"[A-Z][A-Za-z.'-]*", QUALIFICATION.sub("", core))
    if not words:
        return False
    return core != n or bool(QUALIFICATION.search(n)) or len(words) >= 2
ALLERGY = re.compile(r"(?i)\b(?:drug\s+allerg(?:y|ies)|allerg(?:y|ies)|allergic\s+to)\b\s*[:\-]?\s*(?P<v>[^\n]{0,120})")
ALLERGY_NONE = re.compile(r"(?i)^(?:nil|nill|none|nkda|nka|no\s+known(?:\s+drug)?\s+allerg(?:y|ies)|no\s+allerg(?:y|ies)|no)\b")
NKDA = re.compile(r"\bNKDA\b")
FOLLOW = re.compile(r"(?i)\b(?:follow[- ]?up|revisit|re-?check|next\s+visit|come\s+back|review\s+(?:after|in|on|with|at|if))\b[^\n]{0,100}")
INTERVAL = re.compile(r"(?i)\b(?P<rel>after|in|within|every)\s+(?P<n>\d{1,2})\s*(?P<u>days?|weeks?|wks?|months?)\b")
CONDITIONAL = re.compile(r"(?i)\b(?:if|when)\s+(?:symptoms?|needed|required|worse|persist\w*)[^\n]{0,40}|\bSOS\b")


@dataclass
class Fact:
    kind: str                     # report_date | collection_date | document_date | laboratory | clinician_name | allergy | follow_up
    label: str                    # a plain name for the kind
    value: str                    # the printed text
    page: int
    span: tuple[int, int]
    source_text: str
    confidence: float
    normalized: dict = field(default_factory=dict)
    ambiguity: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def parse_date(raw: str) -> dict:
    """{'iso': 'YYYY-MM-DD' | None, 'candidates': [...], 'ambiguity': [...]}. Day/month order and two-digit years are never guessed."""
    r = raw.strip()
    amb: list[str] = []
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", r)
    if m:
        y, mo, d = map(int, m.groups())
        return _ok(y, mo, d, amb)
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", r)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if len(y) == 2:
            amb.append("two_digit_year")
            y_c = [2000 + int(y)]
        else:
            y_c = [int(y)]
        cands = []
        if 1 <= b <= 12 and 1 <= a <= 31:
            cands.append(("day_first", y_c[0], b, a))
        if 1 <= a <= 12 and 1 <= b <= 31 and a != b:
            cands.append(("month_first", y_c[0], a, b))
        if not cands:
            return {"iso": None, "candidates": [], "ambiguity": ["invalid_date"]}
        if len(cands) == 1 and not amb:
            return _ok(cands[0][1], cands[0][2], cands[0][3], amb)
        if len(cands) > 1:
            amb.append("day_month_order_ambiguous")
        return {"iso": None, "candidates": [{"reading": n, "iso": f"{yy:04d}-{mo:02d}-{dd:02d}"} for n, yy, mo, dd in cands], "ambiguity": amb}
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{2,4})$", r) or re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$", r)
    if m:
        g = m.groups()
        day_first = g[0].isdigit()
        d, mon, y = (int(g[0]), g[1], g[2]) if day_first else (int(g[1]), g[0], g[2])
        mo = MONTHS.get(mon.lower()[:3])
        if mo is None:
            return {"iso": None, "candidates": [], "ambiguity": ["invalid_date"]}
        if len(y) == 2:
            return {"iso": None, "candidates": [{"reading": "two_digit_year_2000s", "iso": f"{2000 + int(y):04d}-{mo:02d}-{d:02d}"}], "ambiguity": ["two_digit_year"]}
        return _ok(int(y), mo, d, amb)
    return {"iso": None, "candidates": [], "ambiguity": ["unrecognised_date_format"]}


def _ok(y: int, mo: int, d: int, amb: list) -> dict:
    if not (1900 < y < 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return {"iso": None, "candidates": [], "ambiguity": ["invalid_date"]}
    return {"iso": f"{y:04d}-{mo:02d}-{d:02d}", "candidates": [], "ambiguity": amb}


def _conf(method: str, page_conf: float | None, base: float) -> float:
    return base if method in ("embedded", "plain") else round(min(base, (page_conf or 0.5)) * 0.85, 2)


def _date_fact(kind: str, label: str, m: re.Match, line: str, off: int, p) -> Fact:
    raw = m.group("d")
    pd = parse_date(raw)
    amb = list(pd["ambiguity"])
    s, e = off + m.start("d"), off + m.end("d")
    return Fact(kind, label, raw, p.page_no, (s, e), line.strip(), _conf(p.method, p.confidence, 0.85 if pd["iso"] else 0.7),
                {"iso": pd["iso"], "candidates": pd["candidates"]}, amb)


def extract_facts(pages, doc_type: str = "other_uncertain") -> list[Fact]:
    out: list[Fact] = []
    for p in pages:
        raw_lines = p.text.splitlines(keepends=True)
        offs, pos = [], 0
        for rl in raw_lines:
            offs.append(pos)
            pos += len(rl)
        lines = [(rl.rstrip("\r\n"), o) for rl, o in zip(raw_lines, offs)]
        provider_done = False
        for idx, (ln, off) in enumerate(lines):
            if not ln.strip():
                continue
            if (m := REPORT_DATE.search(ln)):
                out.append(_date_fact("report_date", "Report date", m, ln, off, p))
            if (m := COLLECTION_DATE.search(ln)):
                out.append(_date_fact("collection_date", "Collection date", m, ln, off, p))
            elif not REPORT_DATE.search(ln) and (m := GENERIC_DATE.search(ln)):
                out.append(_date_fact("document_date", "Date on the document", m, ln, off, p))
            if p.page_no == 1 and idx < 15 and not provider_done and PROVIDER_WORDS.search(ln) and not NOT_PROVIDER.match(ln) and len(ln.strip()) <= 90:
                kw = PROVIDER_WORDS.search(ln).group(1).lower()
                s0 = off + len(ln) - len(ln.lstrip())
                out.append(Fact("laboratory", "Laboratory or provider", ln.strip(), p.page_no, (s0, s0 + len(ln.strip())), ln.strip(), _conf(p.method, p.confidence, 0.6),
                                {"type_keyword": kw}, ["provider_type_from_keyword_only"]))
                provider_done = True
            if (m := CLINICIAN_CUE.search(ln)) and _plausible_name(m.group("n").strip(" ,.")):
                n = m.group("n").strip(" ,.")
                out.append(Fact("clinician_name", "Clinician named in the document", n, p.page_no, (off + m.start("n"), off + m.start("n") + len(n)), ln.strip(),
                                _conf(p.method, p.confidence, 0.75), {"cue": re.sub(r"\s+", " ", m.group("cue").lower())}, ["credentials_not_verified"]))
            elif p.page_no == 1 and idx < 8 and (m := CLINICIAN_LINE.match(ln)):
                n = m.group("n").strip(" ,.")
                out.append(Fact("clinician_name", "Clinician named in the document", n, p.page_no, (off + m.start("n"), off + m.start("n") + len(n)), ln.strip(),
                                _conf(p.method, p.confidence, 0.65), {"cue": "named in the header"}, ["credentials_not_verified"]))
            if (m := ALLERGY.search(ln)):
                v = m.group("v").strip(" .;:-")
                if v:
                    none = bool(ALLERGY_NONE.match(v))
                    items = [] if none else [x.strip() for x in re.split(r"[,;/]|\band\b", v) if x.strip()]
                    out.append(Fact("allergy", "Allergy statement", v, p.page_no, (off + m.start("v"), off + m.start("v") + len(m.group("v").rstrip())), ln.strip(),
                                    _conf(p.method, p.confidence, 0.8), {"statement": "none_stated" if none else "listed", "items": items},
                                    ["absence_stated_by_the_document_not_inferred"] if none else []))
            elif (m := NKDA.search(ln)):
                out.append(Fact("allergy", "Allergy statement", "NKDA", p.page_no, (off + m.start(), off + m.end()), ln.strip(), _conf(p.method, p.confidence, 0.8),
                                {"statement": "none_stated", "items": []}, ["abbreviation_not_expanded", "absence_stated_by_the_document_not_inferred"]))
            if (m := FOLLOW.search(ln)):
                v = m.group(0).strip(" .;")
                norm: dict = {}
                amb = []
                iv = INTERVAL.search(v)
                dt = re.search(DATE_TOKEN, v)
                cond = CONDITIONAL.search(v)
                if iv:
                    unit = iv.group("u").lower()
                    norm["interval"] = {"relation": iv.group("rel").lower(), "value": int(iv.group("n")), "unit": "day" if unit.startswith("d") else "week" if unit.startswith("w") else "month"}
                if dt:
                    pd = parse_date(dt.group(0))
                    norm["date"] = {"raw": dt.group(0), "iso": pd["iso"], "candidates": pd["candidates"]}
                    amb += pd["ambiguity"]
                if cond:
                    norm["condition"] = cond.group(0)
                if not norm:
                    amb.append("no_specific_time_stated")
                out.append(Fact("follow_up", "Follow-up instruction", v, p.page_no, (off + m.start(), off + m.start() + len(m.group(0).rstrip())), ln.strip(), _conf(p.method, p.confidence, 0.7), norm, amb))
    return out

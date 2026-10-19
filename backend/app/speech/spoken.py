"""Turning an explanation into something a voice can read aloud WITHOUT losing what the screen says.

The rules:
* every statement that is spoken is a statement that is shown, word for word (the one change is that unit symbols are spoken in words for English: "ng/mL" is read "nanograms per millilitre");
* every number, date and quoted printed text is spoken exactly as shown - nothing is rounded, translated or dropped;
* the page each statement comes from is part of the statement, so it is spoken too;
* what is still uncertain, the request for a clinician's review, and the statement that this is not a diagnosis are ALWAYS spoken, whatever else is left out;
* if there are more statements than are read, the voice says how many are left on the screen. Nothing is silently dropped.
`check` verifies those promises on a finished script; tests and the voice evaluation call it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ai import multilingual

# unit symbols that are spoken in words (English only). Units containing digits (10^3/uL ...) stay as written: expanding them would change which digits are spoken.
UNIT_WORDS = {"g/dL": "grams per decilitre", "mg/dL": "milligrams per decilitre", "ng/mL": "nanograms per millilitre", "pg/mL": "picograms per millilitre", "ug/dL": "micrograms per decilitre",
              "µg/dL": "micrograms per decilitre", "ng/dL": "nanograms per decilitre", "mIU/L": "milli international units per litre", "uIU/mL": "micro international units per millilitre",
              "µIU/mL": "micro international units per millilitre", "mmol/L": "millimoles per litre", "U/L": "units per litre", "IU/L": "international units per litre", "fL": "femtolitres",
              "g/L": "grams per litre", "mEq/L": "milliequivalents per litre", "mm/hr": "millimetres per hour", "nmol/L": "nanomoles per litre", "pmol/L": "picomoles per litre"}
_UNIT_RX = re.compile("(?<![A-Za-z0-9])(" + "|".join(re.escape(u) for u in sorted(UNIT_WORDS, key=len, reverse=True)) + ")(?![A-Za-z0-9])")
MORE = {"en": "There are {n} more statement(s) on the screen.", "hi": "स्क्रीन पर {n} और कथन हैं।", "hinglish": "Screen par {n} aur statement(s) hain."}


def speak_units(text: str, language: str = "en") -> str:
    return _UNIT_RX.sub(lambda m: UNIT_WORDS[m.group(1)], text) if language == "en" else text


@dataclass
class Script:
    language: str
    text: str
    sentences: list[dict] = field(default_factory=list)       # {"kind": summary|item|uncertainty|safety|more, "spoken": ..., "visible": the statement as shown (or None for 'more')}
    omitted: int = 0

    @property
    def complete(self) -> bool:
        return self.omitted == 0


def script(explanation: dict, *, max_items: int = 12) -> Script:
    lang = explanation.get("language", "en")
    secs = explanation.get("sections", [])
    by_id = {s.get("id"): s for s in secs}
    parts: list[dict] = []

    def add(kind, visible):
        parts.append({"kind": kind, "visible": visible, "spoken": speak_units(visible, lang)})
    found = by_id.get("found") or (secs[0] if secs else {})
    if found.get("summary"):
        add("summary", found["summary"])
    items = [i["text"] for sid in ("found", "facts", "medicines", "background") for i in (by_id.get(sid) or {}).get("items", []) if i.get("text")]       # what the document says, then approved background
    for t in items[:max_items]:
        add("item", t)
    omitted = max(0, len(items) - max_items)
    if omitted:
        parts.append({"kind": "more", "visible": None, "spoken": MORE.get(lang, MORE["en"]).format(n=omitted)})
    unc = by_id.get("uncertain")
    if unc and unc.get("summary"):
        add("uncertainty", unc["summary"])
    if explanation.get("clinician_review_message"):
        add("safety", explanation["clinician_review_message"])
    if explanation.get("disclaimer"):
        add("safety", explanation["disclaimer"])
    return Script(lang, " ".join(p["spoken"] for p in parts), parts, omitted)


def shown_statements(explanation: dict) -> set[str]:
    out = {i["text"] for s in explanation.get("sections", []) for i in s.get("items", []) if i.get("text")}
    out |= {s["summary"] for s in explanation.get("sections", []) if s.get("summary")}
    out |= {explanation.get("clinician_review_message"), explanation.get("disclaimer")}
    return {x for x in out if x}


def check(explanation: dict, sc: Script) -> list[str]:
    """What a finished script breaks: a spoken statement that is not shown, a changed number or quoted text, a missing safety statement, an unannounced omission. Empty = all promises kept."""
    bad = []
    shown = shown_statements(explanation)
    for p in sc.sentences:
        if p["kind"] == "more":
            continue
        if p["visible"] not in shown:
            bad.append(f"spoken but not shown: {p['visible'][:60]}")
        lost = multilingual.preserved(speak_units(p["visible"], sc.language), p["spoken"])            # what must survive: every number, date and quoted text of the statement (units, in words)
        if lost:
            bad.append(f"changed in speech: {lost}")
    kinds = [p["kind"] for p in sc.sentences]
    if "uncertainty" not in kinds and any(s.get("id") == "uncertain" and s.get("summary") for s in explanation.get("sections", [])):
        bad.append("the statement of what is uncertain was not spoken")
    if explanation.get("clinician_review_message") and not any(p["visible"] == explanation["clinician_review_message"] for p in sc.sentences):
        bad.append("the request for a clinician's review was not spoken")
    if sc.omitted and "more" not in kinds:
        bad.append("statements were left out without saying so")
    return bad

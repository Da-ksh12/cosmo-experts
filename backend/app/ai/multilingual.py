"""Keeping the facts of a medical text intact when it is shown in another language, and saying plainly what has and has not been checked.

Three things are kept apart and labelled:
* the ORIGINAL  - exactly what the document or the person said, in its own language and script;
* the NORMALIZED form - a cleaned copy used only for matching and processing (spacing, Devanagari digits read as ASCII digits). It is never shown as if it were the original;
* a TRANSLATION - text in another language, with HOW it was made (fixed template, machine, human) and whether anyone who reads that language has reviewed it.

A translation must carry every PROTECTED token of the text it renders: numbers, dates, units, the words a document printed (quoted), and the medicine and test names it names. A rendering
that drops or alters one is rejected (`preserved` returns what is missing), so that "8 ng/mL" can never become "80 ng/mL" or lose its unit in another language.

No Hindi or Hinglish quality is claimed. The fixed Hindi and Hinglish templates in documents/explain.py were written without review by a speaker-clinician and are labelled unreviewed; no machine
translation exists in this system; speech recognition has been checked on synthetic English speech only. `language_status` reports exactly that and nothing more."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..documents import languages

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
DATE = re.compile(r"\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
QUOTED = re.compile(r'"([^"]{2,300})"')
UNITS = ("g/dL", "mg/dL", "ng/mL", "pg/mL", "ug/dL", "µg/dL", "ng/dL", "mIU/L", "uIU/mL", "µIU/mL", "mmol/L", "U/L", "IU/L", "fL", "pg", "g/L", "mEq/L", "mm/hr", "ug/L", "nmol/L", "pmol/L",
         "10^3/uL", "10^6/uL", "10^9/L", "10^12/L", "mg", "mcg", "ml", "%")
_UNIT_RX = re.compile("(?<![A-Za-z0-9])(?:" + "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True)) + ")(?![A-Za-z0-9])")

# What has been checked, per language. Anything not listed here has not been checked.
EVIDENCE = {
    "en": {"speech_recognition": "checked on synthetic English speech only (docs/eval/voice.json); not on real patients' voices or accents", "explanations": "fixed English templates, written by the project's author",
           "voice": "an installed offline English voice, when present"},
    "hi": {"speech_recognition": "not checked", "explanations": "fixed Hindi templates, written without review by a Hindi-speaking clinician: UNREVIEWED", "voice": "only if a Hindi voice is installed; never substituted by another language's voice"},
    "hinglish": {"speech_recognition": "not checked", "explanations": "fixed Hinglish templates, written without review: UNREVIEWED", "voice": "read with an English voice, which may pronounce Hindi words poorly"},
}


def language_status(lang: str) -> dict:
    e = EVIDENCE.get(lang)
    if e is None:
        return {"language": lang, "supported": False, "note": "No part of this system has been checked for this language."}
    return {"language": lang, "supported": True, **e, "reviewed_translations": lang == "en", "claim": "no claim of reliability is made for Hindi or Hinglish" if lang != "en" else "English only, as described"}


def protected_tokens(text: str, names: tuple[str, ...] | list[str] = ()) -> list[str]:
    """Everything in `text` that must survive rendering unchanged, in order of appearance: dates, numbers, units, quoted printed text and the given medicine/test names."""
    t = languages.to_ascii_digits(text or "")
    found: list[tuple[int, str]] = []
    for rx in (DATE, NUMBER, _UNIT_RX):
        found += [(m.start(), m.group(0)) for m in rx.finditer(t)]
    found += [(m.start(1), m.group(1)) for m in QUOTED.finditer(t)]
    for n in names:
        if n and n.lower() in t.lower():
            found.append((t.lower().index(n.lower()), n))
    seen, out = set(), []
    for _, tok in sorted(found):
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def preserved(original: str, rendered: str, names: tuple[str, ...] | list[str] = ()) -> list[str]:
    """The protected tokens of `original` that `rendered` does not carry unchanged. Empty = nothing was lost. A number inside a longer number does not count as carried (80 is not 8)."""
    r = languages.to_ascii_digits(rendered or "")
    missing = []
    for tok in protected_tokens(original, names):
        if NUMBER.fullmatch(tok):
            ok = re.search(rf"(?<![\d.,]){re.escape(tok)}(?!\d|[.,]\d)", r) is not None
        else:
            ok = tok.lower() in r.lower()
        if not ok:
            missing.append(tok)
    return missing


@dataclass
class Rendering:
    language: str
    text: str
    method: str                       # template | machine | human
    reviewed: bool
    reviewed_by: str | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def faithful(self) -> bool:
        return not self.missing


def bundle(original: str, *, language: str = "en", renderings: list[Rendering] | None = None, names: tuple[str, ...] | list[str] = ()) -> dict:
    """Original, normalized and each rendering, kept apart. A rendering that dropped a protected token is marked unfaithful and carries what is missing; an unreviewed one says so."""
    norm = languages.to_ascii_digits(" ".join((original or "").split()))
    out_r = []
    for r in renderings or []:
        r.missing = preserved(original, r.text, names)
        out_r.append({"language": r.language, "text": r.text, "method": r.method, "reviewed": r.reviewed, "reviewed_by": r.reviewed_by, "faithful": r.faithful, "missing": r.missing,
                      "notice": None if r.reviewed else "Not reviewed by a speaker of this language. Check the original."})
    return {"original": {"language": language, "text": original}, "normalized": {"text": norm, "changed": norm != original, "note": "used for matching only; not the original"},
            "protected": protected_tokens(original, names), "renderings": out_r}

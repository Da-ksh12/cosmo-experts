"""OCR language configuration and script awareness.

* `COSMO_OCR_LANGS` (default `eng`; `eng+hin` or `eng,hin` accepted) says what the operator WANTS. The plan intersects that with the Tesseract language packs actually installed:
  a requested pack that is missing is reported, never silently replaced by guesswork, and recognition runs only with the packs that exist.
* Script detection is a statement about CHARACTERS (Latin / Devanagari), not a language identifier. It is used to warn when a document contains a script the installed packs cannot read.
* Numbers, dates, units and medicine names must survive recognition unchanged: `has_non_latin_digits` flags digits outside 0-9 so extraction records them as ambiguous instead of
  silently treating them as ordinary numbers.
Hindi recognition is NOT claimed to be clinically reliable: it has not been evaluated on real documents. Where it runs, its output is labelled research-only (registry: document.ocr.hindi)."""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field

_CODE = re.compile(r"^[a-z]{3}(_[a-z]{3,4})?$")
DEVANAGARI = (0x0900, 0x097F)


@dataclass
class OcrPlan:
    requested: list[str]
    used: list[str]
    missing: list[str]
    lang_arg: str                      # the value passed to Tesseract, for example "eng" or "eng+hin"
    warnings: list[str] = field(default_factory=list)


def configured(env=os.environ) -> list[str]:
    raw = (env.get("COSMO_OCR_LANGS") or "eng").strip().lower()
    langs = [x for x in re.split(r"[+,\s]+", raw) if x]
    bad = [x for x in langs if not _CODE.match(x)]
    if bad or not langs:
        raise SystemExit("COSMO_OCR_LANGS must list Tesseract language codes such as eng or eng+hin")
    return list(dict.fromkeys(langs))


def make_plan(installed: list[str], requested: list[str] | None = None) -> OcrPlan:
    want = requested or configured()
    have = [l for l in want if l in installed]
    missing = [l for l in want if l not in installed]
    warnings = [f"The OCR language pack '{m}' is not installed; text in that language may be unreadable or wrong." for m in missing]
    if not have:
        # nothing requested is installed: fall back to English if it exists (and say so), otherwise recognition is unavailable
        if "eng" in installed:
            have = ["eng"]
            warnings.append("Falling back to English recognition only.")
    return OcrPlan(want, have, missing, "+".join(have), warnings)


def script_fractions(text: str) -> dict:
    """Share of letters in each script among all letters (digits and punctuation are ignored)."""
    latin = dev = other = 0
    for ch in text:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        if DEVANAGARI[0] <= cp <= DEVANAGARI[1]:
            dev += 1
        elif "LATIN" in unicodedata.name(ch, ""):
            latin += 1
        else:
            other += 1
    n = latin + dev + other
    return {"letters": n, "latin": round(latin / n, 3) if n else 0.0, "devanagari": round(dev / n, 3) if n else 0.0, "other": round(other / n, 3) if n else 0.0}


def script_hint(text: str) -> str:
    """latin | devanagari | mixed | unknown: which script dominates. Not a language identifier."""
    f = script_fractions(text)
    if f["letters"] < 10:
        return "unknown"
    if f["devanagari"] >= 0.6:
        return "devanagari"
    if f["latin"] >= 0.8:
        return "latin"
    return "mixed" if f["devanagari"] >= 0.1 else "unknown"


def has_non_latin_digits(s: str) -> bool:
    return any(ch.isdigit() and not ("0" <= ch <= "9") for ch in s)


def to_ascii_digits(s: str) -> str:
    """Latin-digit rendering of a string for NORMALISED values only (the raw text is always kept untouched)."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and not ("0" <= ch <= "9") else ch for ch in s)

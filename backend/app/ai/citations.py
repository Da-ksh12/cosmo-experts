"""Citations: pointing at the exact text a statement rests on, and checking that the text really supports the statement.

A citation is data, not decoration. Every statement the explanation builder makes about a document is produced from fixed wording plus values taken from the extracted record, and
before it is shown the values are checked against the source line they cite: the test name, the number, the unit, the printed range and the printed flag must all be present in that
line (or the field must carry a clinician's correction). A statement whose citation does not support it is dropped and counted, never shown. Statements written by a language model
go through the same check (assistant/grounded.py: the cited words must occur in the cited text, numbers must match, interpretive words must be printed there)."""
from __future__ import annotations

import json
import re

from ..assistant import grounded


# ---------------------------------------------------------------- building citations
def document_citation(document_id: str, *, page: int | None, excerpt: str, item: str = "text", item_id: str | None = None, span=None, version: str | None = None,
                      review_status: str | None = None, note: str | None = None) -> dict:
    c = {"kind": "document", "document_id": document_id, "page": page, "excerpt": (excerpt or "")[:300], "span": list(span) if span else None, "item": item, "item_id": item_id,
         "processing_version": version, "review_status": review_status}
    if note:
        c["note"] = note
    return c


def as_list(v) -> list:
    """Stored lists arrive as JSON text from the database and as lists from the extractor."""
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v) if isinstance(v, str) and v else []
    except ValueError:
        return []
    return out if isinstance(out, list) else []


def as_dict(v) -> dict:
    if isinstance(v, dict):
        return v
    try:
        out = json.loads(v) if isinstance(v, str) and v else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


# ---------------------------------------------------------------- small text helpers
def squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def numbers(s: str) -> list[str]:
    return [n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", s or "")]


def token_in(source: str, token: str) -> bool:
    """`token` occurs in `source` as a whole number or word, never as a piece of a longer number (a value of 8 is not supported by a line that only says 18 or 80)."""
    t = (token or "").strip().lstrip("<>").strip()
    return bool(t) and re.search(rf"(?<![\d.,]){re.escape(t)}(?!\d|[.,]\d)", source or "") is not None


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ".").strip("<> ").strip())
    except ValueError:
        return None


def range_bounds(ref: str) -> tuple[float | None, float | None]:
    nums = numbers(ref)
    if re.search(r"-|–|to", ref or "") and len(nums) >= 2:
        return _num(nums[0]), _num(nums[1])
    if (ref or "").strip().startswith("<") and nums:
        return None, _num(nums[0])
    if (ref or "").strip().startswith(">") and nums:
        return _num(nums[0]), None
    return None, None


FLAG_MARK = {"printed_flag_high": r"(?:^|\s)(?:HH|H|HIGH)(?:\s|$)", "printed_flag_low": r"(?:^|\s)(?:LL|L|LOW)(?:\s|$)"}


# ---------------------------------------------------------------- statements about a laboratory field
def field_statement_problem(kind: str, f: dict) -> str | None:
    """Why the statement of this `kind` about field `f` is not supported by the line it cites (None = supported).
    kinds: value (name, number, unit) | range (the printed reference range) | printed_high | printed_low | outside | within.
    A field a clinician has corrected is no longer compared with the machine-read line: its value and range are the clinician's, and the comparison statements are recomputed."""
    src = f.get("source_text") or ""
    corrected = f.get("review_status") == "corrected"
    amb = as_list(f.get("ambiguity"))
    if kind == "value":
        if corrected:
            return None
        if squash(f.get("name", "")) not in squash(src):
            return "the test name is not in the cited line"
        if not token_in(src, f.get("value", "")):
            return "the value is not in the cited line"
        unit = f.get("unit") or ""
        if unit and squash(unit) not in squash(src) and "unit_ocr_corrected" not in amb:
            return "the unit is not in the cited line"
        return None
    if kind == "range":
        ref = f.get("ref_range") or ""
        if corrected:
            return None
        if not ref or any(not token_in(src, n) for n in numbers(ref)):
            return "the reference range is not in the cited line"
        if ref.strip()[:1] in ("<", ">") and ref.strip()[:1] not in src:
            return "the range qualifier is not in the cited line"
        return None
    if kind in FLAG_MARK:
        if corrected:
            return "the line was corrected after the flag was read"
        return None if re.search(FLAG_MARK[kind], src) else "the printed flag is not in the cited line"
    if kind in ("outside", "within"):
        lo, hi = range_bounds(f.get("ref_range") or "")
        v = _num(f.get("value") or "")
        if v is None or (lo is None and hi is None) or str(f.get("value", "")).strip().startswith(("<", ">")):
            return "the value or range cannot be compared"
        outside = (lo is not None and v < lo) or (hi is not None and v > hi)
        if outside != (kind == "outside"):
            return "the comparison does not follow from the value and the printed range"
        return None
    return "unknown statement kind"


def fact_statement_problem(fact: dict) -> str | None:
    """A header fact states that the document prints `value`; the value must be in the cited line."""
    src, val = fact.get("source_text") or "", fact.get("value") or ""
    if fact.get("review_status") == "corrected":
        return None
    return None if val and squash(val) in squash(src) else "the value is not in the cited line"


def medication_statement_problem(med: dict, page_text: str | None) -> str | None:
    """A medicine entry states that the document prints `raw`; those words must occur on the cited page (when the page text is available)."""
    raw = med.get("raw") or ""
    if not raw:
        return "the entry has no printed text"
    if page_text is None:
        return None
    return None if squash(raw) in squash(page_text) else "the printed entry is not on the cited page"


# ---------------------------------------------------------------- statements written by a model
def claim_support(text: str, quote: str, source_text: str) -> str | None:
    """The same check a model's claim must pass in the assistant: quote present in the cited text, numbers match, interpretive words printed there, no prescriptive wording."""
    return grounded.claim_problem(text, quote, source_text)

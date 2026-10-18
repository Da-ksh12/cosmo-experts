"""Grounded answer generation with citation verification.

The model sees ONLY numbered passages the requesting user may access. It must return structured claims, each citing source ids
and quoting the supporting text. Every claim is then verified here (deterministically, no second model):
  1. each cited id exists in the context,
  2. the quoted span occurs in the cited passage (whitespace/case-normalised),
  3. every number in the claim text occurs in the cited passage,
  4. the claim text contains no prescriptive/dosing language.
Unverified claims are dropped. If nothing survives, the caller falls back to the extractive answer. Claims are labelled by provenance:
report_fact (user's own document), approved_reference (clinician-approved KB), quotation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .llm import LLMBackend, LLMError

SYSTEM = (
    "You answer a patient's question using ONLY the numbered SOURCES provided. You are not a doctor. Rules: "
    "never diagnose; never recommend, start, stop or dose any medicine; never interpret a result beyond what a source states; "
    "if the sources do not answer the question, return an empty claims list. "
    "Return JSON: {\"claims\":[{\"text\":\"one short plain-language sentence\",\"source_ids\":[n],\"quote\":\"exact words copied from that source\"}]}."
)

SCHEMA = {"type": "object", "properties": {"claims": {"type": "array", "items": {"type": "object", "properties": {
    "text": {"type": "string"}, "source_ids": {"type": "array", "items": {"type": "integer"}}, "quote": {"type": "string"}},
    "required": ["text", "source_ids", "quote"]}}}, "required": ["claims"]}

PRESCRIPTIVE = re.compile(r"\b(take|start|stop|increase|decrease|prescribe|dose|dosage|mg\b|tablet|should (?:take|use|stop)|you have (?:a |an )?(?:deficiency|disease|anemia|anaemia|diabetes|cancer))", re.I)
# Words that interpret a result. A claim may only use one if the cited source itself says it (a printed H/L flag licenses "high"/"low").
INTERPRETIVE = re.compile(r"\b(normal|abnormal|within|inside|outside|healthy|deficien\w*|elevated|safe|fine|good|bad|concerning|worrying|severe|mild|serious|risk\w*|"
                          r"optimal|adequate|sufficient|excess\w*|improv\w*|worsen\w*|low|high)\b", re.I)
NUM = re.compile(r"\d+(?:[.,]\d+)?")
KIND_LABEL = {"document": "report_fact", "knowledge_base": "approved_reference", "quotation": "quotation"}


def _licensed(word: str, norm_source: str, raw_source: str) -> bool:
    if word in norm_source:
        return True
    if word in ("low", "high") and re.search(r"(?:^|\s)(L|LOW|Low)\s*$" if word == "low" else r"(?:^|\s)(H|HIGH|High)\s*$", raw_source.strip()):
        return True       # the report itself printed the flag
    if word in ("within", "inside", "outside") and "range" in norm_source and re.search(r"\b(within|inside|outside)\b", norm_source):
        return word in norm_source
    return False


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.,/%-]", "", s.lower())).strip()


@dataclass
class Verified:
    text: str
    provenance: str
    source_index: int
    quote: str


@dataclass
class Result:
    claims: list[Verified] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    raw: str = ""
    json_ok: bool = False
    seconds: float = 0.0
    tokens: int = 0
    model: str = ""


def build_prompt(question: str, passages: list[dict]) -> str:
    src = "\n".join(f"[{i + 1}] ({p['kind']}) {p['text'][:500]}" for i, p in enumerate(passages))
    return f"SOURCES:\n{src}\n\nQUESTION: {question}\n\nReturn only the JSON."


def verify(parsed: dict, passages: list[dict]) -> tuple[list[Verified], list[dict]]:
    ok, bad = [], []
    for c in parsed.get("claims", []) if isinstance(parsed, dict) else []:
        try:
            text, ids, quote = str(c["text"]).strip(), [int(i) for i in c["source_ids"]], str(c["quote"]).strip()
        except (KeyError, TypeError, ValueError):
            bad.append({"claim": str(c)[:120], "reason": "malformed"}); continue
        reason = None
        if not text or not ids or not quote:
            reason = "missing text, source or quote"
        elif any(i < 1 or i > len(passages) for i in ids):
            reason = "cites a source that does not exist"
        elif PRESCRIPTIVE.search(text):
            reason = "prescriptive or diagnostic wording"
        else:
            src = _norm(" ".join(passages[i - 1]["text"] for i in ids))
            if _norm(quote) not in src:
                reason = "quote not found in cited source"
            elif any(not _licensed(w.lower(), src, " ".join(passages[i - 1]["text"] for i in ids)) for w in INTERPRETIVE.findall(text)):
                reason = "interpretive wording not stated in the cited source"
            elif any(n.replace(",", ".") not in src.replace(",", ".") for n in NUM.findall(text)):
                reason = "claim contains a number absent from the cited source"
        if reason:
            bad.append({"claim": text[:120], "reason": reason})
        else:
            ok.append(Verified(text, KIND_LABEL.get(passages[ids[0] - 1]["kind"], "report_fact"), ids[0] - 1, quote))
    return ok, bad


def generate(backend: LLMBackend, question: str, passages: list[dict], max_tokens: int = 350) -> Result:
    res = Result()
    try:
        g = backend.generate(SYSTEM, build_prompt(question, passages), max_tokens=max_tokens, json_schema=SCHEMA)
    except LLMError as e:
        res.dropped.append({"claim": "", "reason": f"backend error: {e}"}); return res
    res.raw, res.seconds, res.tokens, res.model = g.text, g.seconds, g.completion_tokens, g.model
    try:
        parsed = json.loads(g.text)
        res.json_ok = True
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", g.text, re.S)
        try:
            parsed = json.loads(m.group(0)) if m else {}
            res.json_ok = bool(m)
        except json.JSONDecodeError:
            parsed = {}
    res.claims, res.dropped = verify(parsed, passages)
    return res

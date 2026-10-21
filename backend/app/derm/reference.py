"""Which articles may be shown, in which review tier, and how a question finds its article.

Two tiers, never mixed up and always labelled:
    approved   - the clinic's review table holds the article, a verified clinician approved it with an attestation, the approval has not lapsed, and the stored text still hashes to the text that
                 was approved (clinical_content.approved_articles has the same rules). The label says so. If the clinician EDITED the text before approving it, the clinician's text is what is shown.
    reference  - the article ships with the software, is traced to its sources and has not been approved by a clinician yet. It is shown only when the deployment allows it (COSMO_KB_REFERENCE:
               'labelled' shows it with a plain label that the clinic's dermatologist has not reviewed it yet; 'off' never shows it). The default is 'labelled' everywhere except production, where
               the default is 'off' until the clinic decides: showing unreviewed health information to patients is the clinic's decision, not the software's.
Retrieval is the one the rest of the assistant uses (assistant/retrieval.py); it searches ONLY this pack's passages, never a patient's documents, and abstains below a threshold that was measured
on questions with and without an answer in the pack (eval/derm_retrieval_eval.py, docs/eval/derm_retrieval.json)."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from .. import clinical_content, db
from ..assistant.retrieval import HybridRetriever, get_retriever
from . import lexicon, pack as packmod

REFERENCE_LABEL = "General information based on the sources listed below. The clinic's dermatologist has not reviewed this wording yet."
APPROVED_LABEL = "Reviewed and approved by the clinic."
# Abstention thresholds per retriever mode for this pack's passages. Measured by eval/derm_retrieval_eval.py (results in docs/eval/derm_retrieval.json);
# tests/test_derm_knowledge.py checks that these constants equal the recorded values. An unknown mode uses the strictest value.
REF_THRESHOLDS: dict[str, float] = {"lexical": 0.2601, "hybrid(0.5)": 0.4412, "dense": 0.6917}
REF_THRESHOLD_SOURCE = "docs/eval/derm_retrieval.json"
# Measured held-out behaviour (docs/eval/derm_retrieval.json): dense answers 28 of 35 in-scope questions with the right article and wrongly answers 0 of 20 out-of-scope ones; hybrid(0.5) 25/35 and
# 2/20; lexical only 13/35 and 3/20. Lexical matching is therefore NOT used as a fallback: without the embedding model a question is found by its topic keywords or not at all.
RETRIEVAL_FALLBACK_MODES = ("dense", "hybrid(0.5)")
COMBINABLE = ("routine", "habits", "sun-protection", "monitoring", "when-to-see", "ingredients")      # general topics a concern can tailor
MAX_QUERY_CHARS = 400


def reference_mode() -> str:
    v = (os.environ.get("COSMO_KB_REFERENCE") or "").strip().lower()
    if v in ("labelled", "off"):
        return v
    return "off" if os.environ.get("COSMO_MODE") == "production" else "labelled"


def threshold_for(ret: HybridRetriever) -> float:
    return REF_THRESHOLDS.get(ret.mode, max(REF_THRESHOLDS.values()))


@dataclass
class Resolved:
    article: packmod.Article
    tier: str                                  # approved | reference
    label: str
    sections: dict[str, list[str]]             # what is shown (the pack's text, or the clinician's edited text as one section)
    summary: str
    edited_by_clinician: bool = False
    reviewed_at: float | None = None
    review_due: float | None = None
    reviewer_id: str = ""
    version: int = 1
    sha256: str = ""


def _rows(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = db.q(f"SELECT id,title,body,language,version,review_status,reviewed_by,reviewed_at,review_due,content_sha256 FROM kb_articles WHERE id IN ({marks})", tuple(ids))
    return {r["id"]: r for r in rows}


def _approved_valid(row: dict | None) -> bool:
    if not row or row["review_status"] != "approved":
        return False
    if row.get("review_due") and row["review_due"] < time.time():
        return False                                            # an approval lapses on its re-review date: fail closed
    return bool(row.get("content_sha256")) and row["content_sha256"] == clinical_content.article_hash(row["title"], row["body"], row["language"] or "en")


def _paragraphs(body: str) -> tuple[str, list[str]]:
    parts = [p.strip() for p in (body or "").replace("\r", "").split("\n") if p.strip()]
    lines = [p.lstrip("- ").strip() for p in parts]
    return (lines[0] if lines else ""), lines[1:]


def resolve_all() -> dict[str, Resolved]:
    """Every article the person may be shown right now, keyed by article id. Reads the review table afresh, so an article approved a moment ago, edited, or lapsed is reflected at once."""
    try:
        pk = packmod.load()
    except packmod.PackError:
        return {}
    rows = _rows(list(pk.articles))
    mode = reference_mode()
    out: dict[str, Resolved] = {}
    for a in pk.articles.values():
        row = rows.get(a.id)
        if _approved_valid(row):
            same = row["title"] == a.title and (row["body"] or "").strip() == packmod.render_body(a).strip()
            if same:
                out[a.id] = Resolved(a, "approved", APPROVED_LABEL, {k: list(v) for k, v in a.sections.items()}, a.summary, False, row.get("reviewed_at"), row.get("review_due"),
                                     row.get("reviewed_by") or "", int(row["version"] or 1), row["content_sha256"])
            else:                                                # the clinician rewrote it: their text is what patients see, as they approved it
                head, rest = _paragraphs(row["body"])
                out[a.id] = Resolved(a, "approved", APPROVED_LABEL, {"clinic_text": rest} if rest else {}, head or a.summary, True, row.get("reviewed_at"), row.get("review_due"),
                                     row.get("reviewed_by") or "", int(row["version"] or 1), row["content_sha256"])
        elif mode == "labelled":
            out[a.id] = Resolved(a, "reference", REFERENCE_LABEL, {k: list(v) for k, v in a.sections.items()}, a.summary, False, None, None, "", a.version, packmod.content_hash(a))
    return out


def by_topic(topic: str | None) -> Resolved | None:
    """The article for a topic id, if it may be shown right now."""
    if not topic:
        return None
    return next((r for r in resolve_all().values() if r.article.topic == topic), None)


def keyword_index() -> dict[str, list[str]]:
    try:
        pk = packmod.load()
    except packmod.PackError:
        return {}
    return {a.topic: list(a.keywords) + [a.title.lower()] for a in pk.articles.values()}


def topics_in(text: str) -> list[tuple[str, int]]:
    return lexicon.detect_topics(text, keyword_index())


# ---------------------------------------------------------------- retrieval over the pack
_lock = threading.Lock()
_pool_cache: dict = {"key": None, "pool": []}


def passages_of(article_id: str, title: str, summary: str, sections: dict[str, list[str]]) -> list[dict]:
    """The passages retrieval searches for one article: its summary and every statement, each prefixed with the title (the title carries the topic). Shared with the evaluation script."""
    out = [{"text": f"{title}. {summary}", "kind": "derm", "article_id": article_id, "section": "summary"}]
    for sec, items in sections.items():
        out += [{"text": f"{title}. {item}", "kind": "derm", "article_id": article_id, "section": sec} for item in items]
    return out


def _pool(resolved: dict[str, Resolved]) -> list[dict]:
    key = (packmod.load().sha256, tuple(sorted((k, r.tier, r.sha256[:8]) for k, r in resolved.items())))
    with _lock:
        if _pool_cache["key"] == key:
            return _pool_cache["pool"]
        pool = []
        for aid, r in resolved.items():
            pool += passages_of(aid, r.article.title, r.summary, r.sections)
        _pool_cache.update(key=key, pool=pool)
        return pool


@dataclass
class Found:
    resolved: Resolved                         # the article the answer is about
    how: str                                   # topic | retrieval | context
    score: float | None = None
    topics: list[str] = field(default_factory=list)
    secondary: Resolved | None = None          # a second article that adds to the answer (see find)
    secondary_role: str = ""                   # general: a general article requested next to a concern (routine, avoid ...) | concern: the established concern tailoring a general answer


def find(text: str, *, prefer: str | None = None, concern: str | None = None, retriever: HybridRetriever | None = None) -> Found | None:
    """The article a question is about.
    1) By topic keywords. A concern (acne, pigmentation ...) is the subject when the message names one; a general topic (routine, sunscreen, what to avoid ...) is then added as a second article.
       When the message names only a general topic and the conversation has already established a concern, the concern article is added to tailor the answer (never to replace it).
    2) By retrieval: the best passage, if it clears the measured threshold and the embedding model is in use.
    3) By `prefer`: the concern the conversation established, for a follow-up that names nothing.
    None when nothing in the pack answers it: the caller then says so honestly."""
    resolved = resolve_all()
    if not resolved:
        return None
    by_topic = {r.article.topic: r for r in resolved.values()}
    ranked = [(t, s) for t, s in topics_in(text) if t in by_topic]
    if ranked:
        concerns = [t for t, _ in ranked if t in lexicon.CONCERN_TOPICS]
        generals = [t for t, _ in ranked if t in lexicon.GENERAL_TOPICS]
        score = float(dict(ranked)[(concerns or generals)[0]])
        if concerns:
            sec = by_topic.get(generals[0]) if generals else None
            return Found(by_topic[concerns[0]], "topic", score, [t for t, _ in ranked], sec, "general" if sec else "")
        sec = by_topic.get(concern) if concern and concern in lexicon.CONCERN_TOPICS and generals[0] in COMBINABLE else None
        return Found(by_topic[generals[0]], "topic", score, [t for t, _ in ranked], sec, "concern" if sec else "")
    q = lexicon.normalize(text)[:MAX_QUERY_CHARS]
    ret = retriever or get_retriever()
    if len(q) >= 3 and ret.mode in RETRIEVAL_FALLBACK_MODES:
        pool = _pool(resolved)
        hits = [h for h in ret.search(q, pool, k=3) if h.score >= threshold_for(ret)]
        if hits:
            best = pool[hits[0].index]
            return Found(resolved[best["article_id"]], "retrieval", hits[0].score, [])
    if prefer and prefer in by_topic:
        return Found(by_topic[prefer], "context", None, [prefer])
    return None


def status() -> dict:
    """For the clinic's status screen: how the library is being used, without any article text."""
    pk = packmod.self_check()
    res = resolve_all()
    return {**pk, "reference_mode": reference_mode(), "shown_to_patients": len(res), "approved": sum(1 for r in res.values() if r.tier == "approved"),
            "awaiting_review": sum(1 for r in res.values() if r.tier == "reference")}

"""Grounded retrieval over clinician-approved knowledge: passage-level, cited, and willing to say "I have nothing reliable for this".

What may be retrieved: only articles that are approved, unexpired and still byte-identical to what the reviewer approved (clinical_content.approved_articles). Draft text shipped with the
software is never searched here; unreviewed content cannot enter the approved set by any route this module has.

What a hit carries: the passage, its character span inside the article body, the article id/title/version, who reviewed it and when, when the review lapses, the source note and the
content hash, so a statement built from it can point at exactly the words it rests on. A passage that addresses an AI system (ai/injection.py) is kept out of the results and reported
in `quarantined`; retrieved text is never an instruction.

Abstention: a query is answered only when its best passage clears a threshold that was measured, per retriever configuration, on questions with and without an answer in the corpus
(eval/kb_retrieval_eval.py, docs/eval/kb_retrieval.json; tests/test_ai_knowledge.py checks that the constants below equal the recorded values). A query in a script the corpus is not
written in is declined as unsupported rather than matched on noise."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import clinical_content
from ..assistant.retrieval import HybridRetriever, get_retriever
from ..documents import languages
from . import injection

VERSION = "retrieval-2.0.0"
MAX_QUERY_CHARS = 500
MIN_QUERY_CHARS = 3
PASSAGE_MAX_CHARS = 240        # sentences are packed into passages of about this size; a longer single sentence stays whole
MAX_PASSAGES = 2000            # a hard bound on the corpus held in one search

# Abstention thresholds per retriever mode for 240-character passages, chosen on the development queries of eval/kb_retrieval_eval.py and recorded in docs/eval/kb_retrieval.json
# (results "<mode>@passage240"). On the held-out queries the dense setting answered 29 of 33 in-scope questions with a supporting passage and wrongly answered 1 of 30 out-of-scope ones
# (95% interval 0.6-16.7%); the sets are small and written by one author. An unknown mode uses the strictest value. tests/test_ai_knowledge.py checks these against the record.
KB_THRESHOLDS: dict[str, float] = {"lexical": 0.2105, "hybrid(0.5)": 0.4033, "dense": 0.6478}
KB_THRESHOLD_SOURCE = "docs/eval/kb_retrieval.json"

_SENTENCE = re.compile(r"\S.*?(?:[.!?](?=\s|$)|$)", re.S)


def threshold_for(retriever: HybridRetriever) -> float:
    return KB_THRESHOLDS.get(retriever.mode, max(KB_THRESHOLDS.values()))


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat() if ts else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def clean_query(text: str) -> str:
    """The question as it is searched: normalised, control characters removed, spaces collapsed, bounded."""
    t = unicodedata.normalize("NFKC", text or "")
    t = "".join(ch if (ch.isprintable() or ch in " \n\t") else " " for ch in t)
    return re.sub(r"\s+", " ", t).strip()[:MAX_QUERY_CHARS]


def chunk(body: str, max_chars: int = PASSAGE_MAX_CHARS) -> list[tuple[str, int, int]]:
    """Split an article body into passages of whole sentences: (text, start, end) with offsets into `body`, so body[start:end] == text. No sentence is cut."""
    sents = [(m.group(0), m.start(), m.end()) for m in _SENTENCE.finditer(body or "") if m.group(0).strip()]
    out: list[tuple[str, int, int]] = []
    cur: list[tuple[str, int, int]] = []

    def flush():
        if cur:
            a, b = cur[0][1], cur[-1][2]
            out.append((body[a:b], a, b))
            cur.clear()
    for s in sents:
        if cur and (s[2] - cur[0][1]) > max_chars:
            flush()
        cur.append(s)
    flush()
    return out


@dataclass(frozen=True)
class Passage:
    id: str
    article_id: str
    title: str
    version: int
    text: str
    span: tuple[int, int]
    index: int
    count: int
    language: str
    reviewed_by: str
    reviewed_at: float | None
    review_due: float | None
    source_note: str
    content_origin: str
    content_sha256: str

    def pool_entry(self) -> dict:
        """The form the retriever searches: title and passage together (the title carries the topic)."""
        return {"text": f"{self.title}. {self.text}", "kind": "knowledge_base", "passage": self}

    def citation(self, score: float | None = None, *, staff: bool = False) -> dict:
        c = {"kind": "knowledge_base", "passage_id": self.id, "article_id": self.article_id, "title": self.title, "version": self.version, "span": list(self.span),
             "passage_index": self.index, "passage_count": self.count, "excerpt": self.text, "language": self.language, "reviewed": True, "reviewed_at": _iso(self.reviewed_at),
             "review_due": _iso(self.review_due), "source_note": self.source_note, "content_origin": self.content_origin, "content_sha256": self.content_sha256,
             "provenance": "approved_reference"}
        if score is not None:
            c["score"] = round(float(score), 3)
        if staff:
            c["reviewed_by"] = self.reviewed_by
        return c


@dataclass
class Corpus:
    passages: list[Passage] = field(default_factory=list)
    articles: int = 0
    quarantined: list[dict] = field(default_factory=list)


def load(language: str | None = "en", max_chars: int = PASSAGE_MAX_CHARS) -> Corpus:
    """Every searchable passage right now. Reads the approved set afresh, so an article that was edited, un-approved or has lapsed disappears at once."""
    corpus = Corpus()
    for a in clinical_content.approved_articles():
        lang = a.get("language") or "en"
        if language and lang != language:
            continue
        corpus.articles += 1
        parts = chunk(a["body"] or "", max_chars)
        for n, (text, start, end) in enumerate(parts):
            p = Passage(f"kb:{a['id']}:v{a['version']}:{start}-{end}", a["id"], a["title"], int(a["version"] or 0), text, (start, end), n, len(parts), lang, a.get("reviewed_by") or "",
                        a.get("reviewed_at"), a.get("review_due"), a.get("source_note") or "", a.get("content_origin") or "", a.get("content_sha256") or "")
            if injection.is_instruction_like(text) or injection.is_instruction_like(a["title"] or ""):
                corpus.quarantined.append({"passage_id": p.id, "article_id": a["id"], "codes": [f.code for f in injection.scan(text) + injection.scan(a["title"] or "")]})
                continue
            if len(corpus.passages) < MAX_PASSAGES:
                corpus.passages.append(p)
    return corpus


@dataclass
class Hit:
    passage: Passage
    score: float

    def to_dict(self, staff: bool = False) -> dict:
        return {**self.passage.citation(self.score, staff=staff), "text": self.passage.text}


@dataclass
class SearchResult:
    status: str                           # answered | abstained
    reason: str = ""                      # insufficient_evidence | unsupported_language | empty_query | no_approved_content
    hits: list[Hit] = field(default_factory=list)
    best_score: float | None = None
    threshold: float | None = None
    mode: str = ""
    articles: int = 0
    passages: int = 0
    quarantined: list[dict] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.status == "answered"

    def to_dict(self, staff: bool = False) -> dict:
        d = {"status": self.status, "reason": self.reason, "hits": [h.to_dict(staff) for h in self.hits], "retrieval": {"mode": self.mode, "threshold": self.threshold,
             "best_score": None if self.best_score is None else round(self.best_score, 3), "version": VERSION, "calibration": KB_THRESHOLD_SOURCE},
             "corpus": {"articles": self.articles, "passages": self.passages}}
        if staff:
            d["quarantined"] = self.quarantined
        elif self.quarantined:
            d["quarantined"] = len(self.quarantined)
        return d


def search(query: str, *, k: int = 3, language: str | None = "en", retriever: HybridRetriever | None = None, threshold: float | None = None, corpus: Corpus | None = None) -> SearchResult:
    """Passages that answer `query`, or an abstention that says why. Never raises for an empty corpus or an unsupported language: those are honest answers, not errors."""
    q = clean_query(query)
    if len(q) < MIN_QUERY_CHARS:
        return SearchResult("abstained", "empty_query")
    if languages.script_hint(q) in ("devanagari", "mixed"):
        return SearchResult("abstained", "unsupported_language")           # the approved articles are English; matching other scripts would be matching noise
    corpus = corpus or load(language)
    res = SearchResult("abstained", articles=corpus.articles, passages=len(corpus.passages), quarantined=corpus.quarantined)
    if not corpus.passages:
        res.reason = "no_approved_content"
        return res
    ret = retriever or get_retriever()
    th = threshold_for(ret) if threshold is None else threshold
    res.mode, res.threshold = ret.mode, th
    hits = ret.search(q, [p.pool_entry() for p in corpus.passages], k=max(1, min(k, 10)))
    if hits:
        res.best_score = hits[0].score
    kept = [Hit(corpus.passages[h.index], h.score) for h in hits if h.score >= th]
    if not kept:
        res.reason = "insufficient_evidence"
        return res
    res.status, res.hits = "answered", kept
    return res


def health() -> dict:
    """What the approved set looks like right now, without any article text: how much is searchable and why anything is not."""
    from .. import db
    rows = db.q("SELECT id,title,body,language,review_status,content_sha256,review_due FROM kb_articles")
    import time
    now = time.time()
    out = {"total": len(rows), "draft": 0, "usable": 0, "excluded": {"expired": 0, "changed_since_approval": 0, "no_approval_record": 0}}
    for a in rows:
        if a["review_status"] != "approved":
            out["draft"] += 1
        elif a["review_due"] and a["review_due"] < now:
            out["excluded"]["expired"] += 1
        elif not a["content_sha256"]:
            out["excluded"]["no_approval_record"] += 1
        elif a["content_sha256"] != clinical_content.article_hash(a["title"], a["body"], a["language"] or "en"):
            out["excluded"]["changed_since_approval"] += 1
        else:
            out["usable"] += 1
    c = load(None)
    out["passages"] = len(c.passages)
    out["quarantined_passages"] = len(c.quarantined)
    return out

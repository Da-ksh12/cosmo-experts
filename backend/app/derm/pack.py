"""The dermatology education pack: versioned content files with their sources, validated before use.

Files (all under CONTENT_DIR/derm, so COSMO_CONTENT_DIR moves them together with the rest of the clinical content):
    sources.json       every source the articles rest on: publisher, title, URL, the dates the page itself showed, and the reuse terms that were checked.
    articles/*.json    one article each: plain-language statements in sections (what it is, what may contribute, self-care, avoid, monitor, see a clinician, urgent, photo limits),
                       its limitations and contraindications, the claims a clinician must confirm, and its review record.

What this module guarantees:
* an article that names a source that is not in sources.json, or has no limitations, no claims to verify, or a review status that claims more than the record supports, does not load
  (validate() lists the problems; load() refuses the pack, so a half-checked pack is never served);
* the pack never marks anything approved. Approval happens only in the clinic's review table (kb_articles, by a verified clinician with an attestation), by the existing workflow;
* render_body() is deterministic: the text that is seeded into the review table, and the text whose hash a clinician approves, is exactly the text of the article."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .. import clinical_content, db

VERSION = "derm-pack-1.0.0"
SECTION_ORDER = ("what_it_is", "may_contribute", "self_care", "avoid", "monitor", "see_clinician", "urgent", "photo_limits")
SECTION_TITLES = {
    "what_it_is": "What it is", "may_contribute": "What may contribute", "self_care": "Gentle self-care", "avoid": "What to avoid", "monitor": "How to keep track",
    "see_clinician": "When to see a clinician", "urgent": "Signs that need prompt help", "photo_limits": "What a photo cannot show",
}
REVIEW_STATUSES = ("pending_clinician_review",)          # the pack itself can only ever be pending; approval lives in kb_articles
EVIDENCE_TYPES = ("patient_education_guidance",)
TERMS_BASES = ("ogl_v3", "us_gov_public_domain", "restricted_facts_only")
TOPICS = ("acne", "acne-marks", "pigmentation", "redness", "rosacea", "dry-skin", "eczema", "product-reactions", "sun-protection", "routine", "ingredients", "habits", "monitoring",
          "when-to-see", "moles", "warning-signs", "photo-limits")
_ID = re.compile(r"^derm\.[a-z0-9-]+$")
_SRC = re.compile(r"^[a-z0-9-]+$")
MAX_ITEM_CHARS = 520


class Terms(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis: str
    url: str
    checked: str
    note: str | None = None

    @field_validator("basis")
    @classmethod
    def _basis(cls, v):
        if v not in TERMS_BASES:
            raise ValueError(f"unknown terms basis {v!r}")
        return v


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publisher: str
    title: str
    url: str
    kind: str
    page_reviewed: str | None = None
    next_review_due: str | None = None
    accessed: str
    source_review_overdue: bool = False
    terms: Terms

    @field_validator("url")
    @classmethod
    def _https(cls, v):
        if not re.fullmatch(r"https://[a-z0-9.-]+(/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?", v):
            raise ValueError("a source URL must be a plain https address")
        return v


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    reviewed_by: str | None = None
    reviewed_on: str | None = None
    review_due: str | None = None
    note: str = ""

    @model_validator(mode="after")
    def _honest(self):
        if self.status not in REVIEW_STATUSES:
            raise ValueError("the pack may only carry the status pending_clinician_review; approval is recorded in the clinic's review table")
        if self.reviewed_by or self.reviewed_on:
            raise ValueError("the pack must not name a reviewer: no clinician has reviewed it through the approval workflow")
        return self


class Term(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str
    plain: str


class Authored(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str
    on: str


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int = Field(ge=1)
    language: str = "en"
    topic: str
    title: str = Field(min_length=3, max_length=120)
    audience: str = "patient"
    applies_to: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(min_length=3)
    summary: str = Field(min_length=40, max_length=700)
    sections: dict[str, list[str]]
    terms: list[Term] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(min_length=1)
    evidence_type: str
    source_ids: list[str] = Field(min_length=1)
    claims_to_verify: list[str] = Field(min_length=1)
    review: Review
    authored: Authored

    @field_validator("id")
    @classmethod
    def _id_ok(cls, v):
        if not _ID.fullmatch(v):
            raise ValueError("article ids look like derm.topic-name")
        return v

    @field_validator("topic")
    @classmethod
    def _topic_ok(cls, v):
        if v not in TOPICS:
            raise ValueError(f"unknown topic {v!r}")
        return v

    @field_validator("evidence_type")
    @classmethod
    def _evidence_ok(cls, v):
        if v not in EVIDENCE_TYPES:
            raise ValueError("unknown evidence type")
        return v

    @field_validator("source_ids")
    @classmethod
    def _src_ids(cls, v):
        if len(set(v)) != len(v) or not all(_SRC.fullmatch(s) for s in v):
            raise ValueError("source ids must be unique and well formed")
        return v

    @field_validator("sections")
    @classmethod
    def _sections_ok(cls, v):
        bad = [k for k in v if k not in SECTION_ORDER]
        if bad:
            raise ValueError(f"unknown section(s): {', '.join(bad)}")
        for k, items in v.items():
            for i in items:
                if not i.strip() or len(i) > MAX_ITEM_CHARS:
                    raise ValueError(f"section {k}: an item is empty or longer than {MAX_ITEM_CHARS} characters")
        if not v.get("what_it_is") or not v.get("see_clinician"):
            raise ValueError("every article needs 'what_it_is' and 'see_clinician'")
        return v

    def items(self, section: str) -> list[str]:
        return list(self.sections.get(section, []))


@dataclass
class Pack:
    sources: dict[str, Source]
    articles: dict[str, Article]
    sha256: str
    meta: dict = field(default_factory=dict)

    def by_topic(self, topic: str) -> Article | None:
        return next((a for a in self.articles.values() if a.topic == topic), None)

    def source_list(self, article: Article) -> list[dict]:
        out = []
        for sid in article.source_ids:
            s = self.sources[sid]
            out.append({"id": sid, "publisher": s.publisher, "title": s.title, "url": s.url, "page_reviewed": s.page_reviewed, "next_review_due": s.next_review_due,
                        "source_review_overdue": s.source_review_overdue, "terms": s.terms.basis})
        return out


class PackError(Exception):
    """The pack cannot be used; the message names every problem found."""


def pack_dir() -> Path:
    return clinical_content.CONTENT_DIR / "derm"


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def validate(directory: Path | None = None) -> tuple[Pack | None, list[str]]:
    """Load and check the pack. Returns (pack, problems); the pack is None when anything is wrong."""
    d = directory or pack_dir()
    problems: list[str] = []
    sources: dict[str, Source] = {}
    articles: dict[str, Article] = {}
    meta: dict = {}
    sp = d / "sources.json"
    if not sp.is_file():
        return None, [f"missing {sp.name}"]
    try:
        raw = _read(sp)
        meta = raw.get("_meta", {})
        for sid, s in raw.get("sources", {}).items():
            if not _SRC.fullmatch(sid):
                problems.append(f"source id {sid!r} is not well formed")
                continue
            try:
                sources[sid] = Source(**s)
            except ValidationError as e:
                problems.append(f"source {sid}: {e.errors()[0]['msg']} ({'.'.join(str(x) for x in e.errors()[0]['loc'])})")
    except (ValueError, OSError) as e:
        return None, [f"sources.json cannot be read: {type(e).__name__}"]
    files = sorted((d / "articles").glob("*.json")) if (d / "articles").is_dir() else []
    if not files:
        problems.append("no articles found")
    for f in files:
        try:
            a = Article(**_read(f))
        except (ValidationError, ValueError, OSError) as e:
            msg = e.errors()[0]["msg"] + " (" + ".".join(str(x) for x in e.errors()[0]["loc"]) + ")" if isinstance(e, ValidationError) else type(e).__name__
            problems.append(f"{f.name}: {msg}")
            continue
        if a.id in articles:
            problems.append(f"{f.name}: duplicate article id {a.id}")
            continue
        missing = [s for s in a.source_ids if s not in sources]
        if missing:
            problems.append(f"{f.name}: unknown source(s) {', '.join(missing)}")
        articles[a.id] = a
    topics = [a.topic for a in articles.values()]
    for t in set(topics):
        if topics.count(t) > 1:
            problems.append(f"topic {t} has more than one article")
    if problems:
        return None, problems
    digest = hashlib.sha256(json.dumps({"s": raw, "a": [a.model_dump() for a in articles.values()]}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return Pack(sources, articles, digest, meta), []


_lock = threading.Lock()
_cache: dict = {"key": None, "pack": None}


def _key(d: Path):
    try:
        return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in [d / "sources.json", *sorted((d / "articles").glob("*.json"))])
    except OSError:
        return None


def load() -> Pack:
    """The pack, re-read when a file changes. Raises PackError rather than serving a pack that failed validation."""
    d = pack_dir()
    k = _key(d)
    with _lock:
        if k is not None and _cache["key"] == k and _cache["pack"] is not None:
            return _cache["pack"]
        pack, problems = validate(d)
        if pack is None:
            raise PackError("; ".join(problems[:8]))
        _cache.update(key=k, pack=pack)
        return pack


def available() -> bool:
    try:
        load()
        return True
    except PackError:
        return False


# ---------------------------------------------------------------- rendering (the text a clinician reviews and approves)
def render_body(a: Article) -> str:
    """Deterministic plain text of the whole article. The review table stores exactly this, and approval records its hash."""
    out = [a.summary, ""]
    for sec in SECTION_ORDER:
        items = a.items(sec)
        if items:
            out.append(SECTION_TITLES[sec] + ":")
            out += [f"- {i}" for i in items]
            out.append("")
    if a.terms:
        out.append("Terms in plain words:")
        out += [f"- {t.term}: {t.plain}" for t in a.terms]
        out.append("")
    if a.contraindications:
        out.append("When this general advice may not apply:")
        out += [f"- {c}" for c in a.contraindications]
        out.append("")
    out.append("Limits of this information:")
    out += [f"- {x}" for x in a.limitations]
    return "\n".join(out).strip()


def content_hash(a: Article) -> str:
    return clinical_content.article_hash(a.title, render_body(a), a.language)


def source_note(pack: Pack, a: Article) -> str:
    """What a reviewer sees as the source note: the sources with their URLs and dates. Stored with the draft; the reviewer replaces it with what they checked."""
    lines = []
    for sid in a.source_ids:
        s = pack.sources[sid]
        lines.append(f"{s.publisher}: {s.title} <{s.url}> (page review date {s.page_reviewed or 'not shown'}; checked {s.accessed}; reuse basis {s.terms.basis})")
    return "\n".join(lines)


def seed_drafts() -> int:
    """Insert the pack's articles into the clinic's review table as DRAFT rows (never approved), so a clinician can review, edit and approve them with the existing workflow.
    Idempotent by id; an article that already has a row is left exactly as it is (a clinician's edit or approval is never overwritten). Returns the number inserted."""
    pack = load()
    n = 0
    for a in pack.articles.values():
        if db.q1("SELECT 1 x FROM kb_articles WHERE id=?", (a.id,)):
            continue
        db.x("INSERT INTO kb_articles(id,title,body,language,review_status,reviewed_by,version,content_origin,claims_to_verify,source_note) VALUES(?,?,?,?, 'draft','',?,'ai_draft',?,?)",
             (a.id, a.title, render_body(a), a.language, a.version, json.dumps(a.claims_to_verify), source_note(pack, a)))
        n += 1
    return n


def self_check() -> dict:
    """For the clinic's status screen and the health check: is the pack usable, how big, and how much of it is approved."""
    try:
        pack = load()
    except PackError as e:
        return {"usable": False, "problem": str(e)[:300]}
    return {"usable": True, "version": VERSION, "articles": len(pack.articles), "sources": len(pack.sources), "sha256": pack.sha256[:16], "all_pending_review": all(a.review.status == "pending_clinician_review" for a in pack.articles.values())}

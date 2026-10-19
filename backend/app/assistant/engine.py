"""Retrieval-grounded assistant. It never generates medical facts: answers are assembled from
(1) the asking patient's own document text/fields, (2) clinician-APPROVED knowledge-base articles and
(3) the patient's clinician-approved quotations. Out-of-evidence questions abstain."""
from __future__ import annotations

import json
import logging
import re

import os

from . import grounded
from .llm import get_backend
from .retrieval import get_retriever

from .. import clinical_content, db
from ..ai import gates, injection, knowledge, safety
from ..ai.citations import document_citation
from ..quotes import service as quotes_service


log = logging.getLogger("cosmo.assistant")

REFUSE = re.compile(r"\b(prescribe|prescription for|what (medicine|medication|drug|tablet)|which (medicine|medication|drug|tablet)|"
                    r"how (much|many) (mg|tablet|dose)|dosage|dose of|should i (take|start|stop|use)|can i (take|stop|start)|"
                    r"diagnos(e|is) me|do i have|what (disease|condition) do i)\b", re.I)
QUOTE_Q = re.compile(r"\b(quote|quotation|price|cost|estimate|charges?|fee|how much)\b", re.I)

REFUSE_MSG = safety.REFUSE_MSG          # one wording for every surface (ai/safety.py)
ABSTAIN_MSG = safety.ABSTAIN_MSG


def document_passages(owner_id: str) -> list[dict]:
    """The person's own processed documents as line-level passages (used by the assistant and by the agent's search tool)."""
    out = []
    for d in db.q("SELECT id,filename,doc_type FROM documents WHERE owner_id=? AND deleted=0 AND status='processed'", (owner_id,)):
        for p in db.q("SELECT page_no,text FROM doc_pages WHERE document_id=? ORDER BY page_no", (d["id"],)):
            for chunk in re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z])", p["text"]):   # line-level passages keep matches specific
                chunk = chunk.strip()
                if len(chunk) >= 8:
                    out.append({"text": chunk, "kind": "document", "document_id": d["id"], "filename": d["filename"], "page": p["page_no"]})
        for f in db.q("SELECT name,source_page,source_text FROM doc_fields WHERE document_id=? AND review_status!='rejected'", (d["id"],)):
            # the test name is the retrieval key; the displayed excerpt is the exact source line
            out.append({"text": f["source_text"], "match": f'{f["name"]} result {f["name"]}', "kind": "document",
                        "document_id": d["id"], "filename": d["filename"], "page": f["source_page"]})
    return out


def _passages(owner_id: str) -> list[dict]:
    out = document_passages(owner_id)
    for kp in knowledge.load("en").passages:                       # approved, unexpired and unchanged articles, split into cited passages (instruction-like passages already excluded)
        out.append({**kp.pool_entry(), "article_id": kp.article_id, "title": kp.title, "version": kp.version})
    return out


def _quotes(owner_id: str) -> list[dict]:
    qs = [q for q in db.q("SELECT * FROM quotes WHERE patient_id=? AND status='approved' ORDER BY updated_at DESC", (owner_id,)) if quotes_service.effective_status(q) == "approved"]
    out = []
    for qu in qs:
        lines = json.loads(qu["lines"])
        txt = "; ".join(f'{l["name"]} x{l["qty"]} = {l["line_total"]:.2f} {qu["currency"]}' for l in lines)
        out.append({"text": f'Approved quotation {qu["id"][:8]}: {txt}. Total {qu["total"]:.2f} {qu["currency"]}.',
                    "kind": "quotation", "quote_id": qu["id"]})
    return out


def answer(owner_id: str, conversation_id: str, question: str) -> dict:
    history = db.q("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 4", (conversation_id,))
    policy = clinical_content.urgent_policy()
    if policy.regex.search(question):
        return {"answer": policy.message, "sources": [], "status": "clinician_review_required", "abstained": True, "reason": "urgent"}
    if REFUSE.search(question) or safety.classify_request(question).refuse:        # this assistant's own pattern, and the shared classification every other surface uses
        return {"answer": REFUSE_MSG, "sources": [], "status": "clinician_review_required", "abstained": True, "reason": "clinical_decision"}

    # follow-ups: short questions borrow the previous patient question as context
    query, prev = question, ""
    if len(question.split()) < 5:
        said = [m["content"] for m in history if m["role"] == "patient"]
        if said and said[0] == question:      # the current message is already stored; the PREVIOUS question is the next one
            said = said[1:]
        prev = said[0] if said else ""
        query = f"{prev} {question}".strip()

    pool, quarantined = injection.partition(_passages(owner_id))     # text that addresses software is data we do not use: not retrieved, not quoted, not sent to a model
    notice = ({"notice": "Some text in your documents looks like instructions to software, so it was not used in this answer.", "quarantined_text": len(quarantined)} if quarantined else {})
    if QUOTE_Q.search(question):
        qp = _quotes(owner_id)
        if qp:
            return {"answer": "Here is what your clinic-approved quotation says:\n- " + "\n- ".join(p["text"] for p in qp[:2])
                    + "\nThis is an estimate from the clinic's approved price list; final cost depends on your doctor's plan.",
                    "sources": [{"kind": "quotation", "quote_id": p["quote_id"]} for p in qp[:2]],
                    "status": "available", "abstained": False}
        return {"answer": "You don't have an approved quotation yet. You can request one and the clinic team will prepare it.",
                "sources": [], "status": "available", "abstained": False}
    if not pool:
        return {"answer": ABSTAIN_MSG, "sources": [], "status": "clinician_review_required", "abstained": True, "reason": "no_evidence", **notice}

    ret = get_retriever()

    def passing(q):
        return [(pool[h.index], h.score) for h in ret.search(q, pool, k=3) if h.score >= ret.threshold]
    # a short follow-up such as "and its range?" refers to the previous question, so its evidence takes precedence over a bare keyword match
    hits = (passing(query) or passing(prev)) if prev else passing(question)
    if not hits:
        return {"answer": ABSTAIN_MSG, "sources": [], "status": "clinician_review_required", "abstained": True, "reason": "no_evidence", **notice}

    # Optional experimental mode: a local LLM drafts claims that are then verified against the cited text. Off unless explicitly enabled
    # (COSMO_ASSISTANT_MODE=llm + a model); on any failure or when no claim survives verification the extractive answer below is used.
    llm_note: dict = {}
    if os.environ.get("COSMO_ASSISTANT_MODE") == "llm":
        allowed, why = gates.llm_answers_status()          # a model writes patient-facing text only after it passed its evaluation gates AND an administrator approved it
        if not allowed:
            llm_note = {"llm_mode": "not_enabled"}
            log.info("model-drafted answers requested but not enabled: %s", "; ".join(why))
        be = get_backend() if allowed else None
        if be is not None:
            passages = [p for p, _ in hits]
            g = grounded.generate(be, question, passages)
            if g.claims:
                lines, srcs, seen_i = [], [], set()
                for c in g.claims:
                    p = passages[c.source_index]
                    label = {"report_fact": "From your report", "approved_reference": "Clinic-approved reference", "quotation": "From your quotation"}[c.provenance]
                    lines.append(f"{label}: {c.text}")
                    if c.source_index not in seen_i:
                        seen_i.add(c.source_index)
                        srcs.append(_source(p, 0.0, c.provenance))
                return _screen({"answer": "\n- ".join(["Here is what I found that relates to your question (each statement was checked against its source):"] + lines)
                                + "\nThis is not a diagnosis. Your doctor can explain what it means for you.", "sources": srcs, "status": "clinician_review_required",
                                "abstained": False, "mode": "llm_verified", "model": g.model, "dropped_unverified_claims": len(g.dropped), **notice}, [p["text"] for p in passages])

    bullets, sources, seen = [], [], set()
    for p, s in hits:
        key = (p.get("document_id") or p.get("article_id"), p.get("page"), " ".join(p["text"].split()))
        if key in seen:
            continue
        seen.add(key)
        if p["kind"] == "document":
            bullets.append(f'Your document "{p["filename"]}" (page {p["page"]}) says: "{p["text"][:300]}"')
        else:
            bullets.append(f'Clinic-approved information, "{p["title"]}": "{p["passage"].text[:400]}"')
        sources.append(_source(p, s, "report_fact" if p["kind"] == "document" else "approved_reference"))
    return _screen({"answer": "Here is what I found that relates to your question:\n- " + "\n- ".join(bullets)
                    + "\nThis repeats what the sources say; it is not a diagnosis. Your doctor can explain what it means for you.",
                    "sources": sources, "status": "clinician_review_required", "abstained": False, "mode": "extractive", **notice, **llm_note},
                   [p["text"] for p, _ in hits] + [p["passage"].text for p, _ in hits if p["kind"] != "document"])


def _screen(out: dict, evidence: list[str]) -> dict:
    """The shared safety policy over the finished answer. What the sources say is quoted (and so allowed, as the source speaking); anything the assistant itself wrote that reads as a diagnosis,
    a medicine change, a certainty claim or emergency guidance is withheld and replaced by the standard abstention - never edited into something that still looks approved."""
    rev = safety.review_text(out["answer"], kind="education", evidence=evidence)
    if rev.allowed:
        return out
    log.warning("assistant answer withheld by the safety policy: %s", ",".join(rev.codes()))
    kept = {k: out[k] for k in ("notice", "quarantined_text", "llm_mode") if k in out}
    return {"answer": rev.replacement or safety.UNSUPPORTED_MSG, "sources": [], "status": "clinician_review_required", "abstained": True, "reason": "safety_policy", **kept}


def _source(p: dict, score: float, provenance: str) -> dict:
    """The source entries the screens have always received, plus a `citation` with the passage-level detail (page and text, or article version, span and review record)."""
    if p["kind"] == "document":
        return {"kind": "document", "document_id": p["document_id"], "page": p["page"], "excerpt": p["text"][:300], "score": round(score, 2), "provenance": provenance,
                "citation": document_citation(p["document_id"], page=p["page"], excerpt=p["text"], item="text")}
    return {"kind": "knowledge_base", "article_id": p["article_id"], "title": p["title"], "version": p["version"], "score": round(score, 2), "provenance": provenance,
            "citation": p["passage"].citation(score)}

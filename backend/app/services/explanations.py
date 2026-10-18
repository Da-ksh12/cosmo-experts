"""Builds a document explanation for ONE authenticated user. The REST endpoint and the engine capability `document.explain` both call this, so the access rule, the audit record, the
untrusted-text handling and the safety screening are the same whichever door is used.

Access: the owner, any clinician, and an administrator only when administrator clinical access is on (security.can_access_patient_data, the rule the database policies repeat). A
document that does not exist and a document the caller may not see look the same (`NotFound`). Opening someone else's document is audited.
Untrusted text: every line of the document that an explanation statement would repeat is scanned (ai/injection.py); a line that addresses software is left out and counted.
Background: clinic-approved passages come from fixed questions the system itself asks (never from document text), and only when approved content exists."""
from __future__ import annotations

from .. import db
from ..ai import injection, knowledge, safety
from ..documents import explain, store
from ..security import can_access_patient_data

# questions the SYSTEM asks of the approved articles, chosen by what the document contains; the patient's own words are never used to search here
BACKGROUND_QUERIES = (("range", "what does reference range mean on my lab report"), ("flag", "what does the H next to my result mean"), ("unit", "why does the unit matter"))


class NotFound(Exception):
    pass


def _background(fields: list[dict], corpus: knowledge.Corpus) -> list[dict]:
    wanted = []
    if any(f.get("ref_range") for f in fields):
        wanted.append("range")
    if any(f.get("flag") in ("printed_flag_high", "printed_flag_low", "outside_printed_range") for f in fields):
        wanted.append("flag")
    if any(f.get("unit") for f in fields):
        wanted.append("unit")
    out, seen = [], set()
    for key, q in BACKGROUND_QUERIES:
        if key not in wanted:
            continue
        for h in knowledge.search(q, k=1, corpus=corpus).hits:
            if h.passage.id not in seen:
                seen.add(h.passage.id)
                out.append(h.to_dict())
    return out


def _contradictions(owner_id: str, doc_id: str) -> list[dict]:
    rows = db.q("SELECT f.id,f.name,f.value,f.unit,f.source_page,d.doc_date,d.id AS document_id FROM doc_fields f JOIN documents d ON d.id=f.document_id "
                "WHERE d.owner_id=? AND d.deleted=0 AND f.review_status!='rejected'", (owner_id,))
    return [c for c in safety.detect_contradictions(rows) if any(v.get("document_id") == doc_id for v in c["values"])]


def build(user: dict, doc_id: str, lang: str = "en") -> dict:
    d = db.q1("SELECT * FROM documents WHERE id=? AND deleted=0", (doc_id,))
    if not d or not can_access_patient_data(user, d["owner_id"]):
        raise NotFound(doc_id)
    if user["id"] != d["owner_id"]:
        db.audit(user["id"], "document.staff_access", doc_id, {"owner": d["owner_id"]})
    fields = db.q("SELECT * FROM doc_fields WHERE document_id=? AND review_status!='rejected' ORDER BY source_page,field_no", (doc_id,))
    pages = db.q("SELECT page_no,text,method,ocr_confidence FROM doc_pages WHERE document_id=?", (doc_id,))
    facts, meds = store.load_facts(doc_id), store.load_medications(doc_id)

    flagged = 0

    def clean(rows, text_of):
        nonlocal flagged
        kept = []
        for r in rows:
            if injection.is_instruction_like(text_of(r)):
                flagged += 1
            else:
                kept.append(r)
        return kept
    fields, facts, meds = clean(fields, lambda r: r["source_text"] or ""), clean(facts, lambda r: r["source_text"] or ""), clean(meds, lambda r: r["raw"] or "")
    flagged += sum(1 for p in pages if injection.is_instruction_like(p["text"] or ""))

    corpus = knowledge.load("en")
    background = None
    if corpus.passages:
        background = _background(fields, corpus) if lang == "en" else []
    out = explain.explain_document(d, fields, [{"method": p["method"], "ocr_confidence": p["ocr_confidence"]} for p in pages], lang, facts=facts, medications=meds, background=background,
                                   contradictions=_contradictions(d["owner_id"], doc_id), quarantined=flagged, page_texts={p["page_no"]: p["text"] or "" for p in pages})
    review = safety.review_text(out["spoken_text"], kind="extraction")
    out["safety"] = {"allowed": review.allowed, "labels": review.labels, "disclaimers": review.disclaimers, "violations": review.codes()}
    out["document_review_status"] = d["review_status"]
    out["extraction_warnings"] = [w for w in (d["error"] or "").split("; ") if w]
    return out

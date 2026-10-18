"""Persistence helpers for what the document pipeline extracts: one place that knows every table that holds extracted content, so a deletion can never leave some of it behind."""
from __future__ import annotations

import json

from .. import db

CHILD_TABLES = ("doc_pages", "doc_fields", "doc_facts", "doc_medications")


def delete_children(tx_or_db, document_id: str) -> None:
    """Remove all extracted content of one document (pages, lab fields, header facts, medicine entries). `tx_or_db`: an open transaction or the db module."""
    for table in CHILD_TABLES:
        tx_or_db.x(f"DELETE FROM {table} WHERE document_id=?", (document_id,))


def save_extraction(t, doc_id: str, res) -> None:
    """Insert pages, fields, facts and medicine entries of a processed document inside the caller's transaction."""
    for p in res.pages:
        t.x("INSERT INTO doc_pages(id,document_id,page_no,text,method,ocr_confidence,layout,script) VALUES(?,?,?,?,?,?,?,?)",
            (db.uid(), doc_id, p.page_no, p.text, p.method, p.confidence, json.dumps(p.layout) if p.layout else None, p.script))
    for n, f in enumerate(res.fields):
        t.x("INSERT INTO doc_fields(id,document_id,name,value,unit,ref_range,source_page,source_text,confidence,flag,review_status,corrected_by,processing_version,field_no,provenance,normalized,ambiguity)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (db.uid(), doc_id, f.name, f.value, f.unit, f.ref_range, f.source_page, f.source_text, f.confidence, f.flag, f.review_status, "",
                                                            res.version, n, json.dumps(f.provenance), json.dumps(f.normalized), json.dumps(f.ambiguity)))
    for n, fact in enumerate(res.facts):
        t.x("INSERT INTO doc_facts(id,document_id,kind,label,value,source_page,source_text,confidence,provenance,normalized,ambiguity,field_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.uid(), doc_id, fact.kind, fact.label, fact.value, fact.page, fact.source_text, fact.confidence,
             json.dumps({"page": fact.page, "span": list(fact.span), "extractor": res.version}), json.dumps(fact.normalized), json.dumps(fact.ambiguity), n))
    for n, md in enumerate(res.medications):
        d = md.to_dict()
        t.x("INSERT INTO doc_medications(id,document_id,item_no,source_page,raw,name,entry,ambiguity,inferences,confidence,parser) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (db.uid(), doc_id, n, md.page, d["raw"], (d["name"] or {}).get("raw", ""), json.dumps(d, ensure_ascii=False), json.dumps(md.ambiguity), json.dumps(md.inferences), md.confidence, d["parser"]))


def _j(v, default):
    try:
        return json.loads(v) if isinstance(v, str) and v else default
    except ValueError:
        return default


def load_fields(document_id: str) -> list[dict]:
    rows = db.q("SELECT * FROM doc_fields WHERE document_id=? ORDER BY source_page,field_no", (document_id,))
    for r in rows:
        r["provenance"], r["normalized"], r["ambiguity"] = _j(r.get("provenance"), {}), _j(r.get("normalized"), {}), _j(r.get("ambiguity"), [])
    return rows


def load_facts(document_id: str) -> list[dict]:
    rows = db.q("SELECT * FROM doc_facts WHERE document_id=? ORDER BY source_page,field_no", (document_id,))
    for r in rows:
        r["provenance"], r["normalized"], r["ambiguity"] = _j(r.get("provenance"), {}), _j(r.get("normalized"), {}), _j(r.get("ambiguity"), [])
    return rows


def load_medications(document_id: str) -> list[dict]:
    rows = db.q("SELECT * FROM doc_medications WHERE document_id=? ORDER BY item_no", (document_id,))
    return [{"id": r["id"], "item_no": r["item_no"], "page": r["source_page"], "raw": r["raw"], "entry": _j(r["entry"], {}), "ambiguity": _j(r["ambiguity"], []), "inferences": _j(r["inferences"], []),
             "confidence": r["confidence"], "review_status": r["review_status"], "reviewed_by": r["reviewed_by"], "corrected": bool(r["original"]), "parser": r["parser"]} for r in rows]

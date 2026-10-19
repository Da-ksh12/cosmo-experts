"""Business operations shared by the synchronous routes and the asynchronous gateway, so both paths run identical logic."""
from __future__ import annotations

import hashlib
import re

from .. import db, malware, storage
from . import notify
from ..config import UPLOAD_DIR
from ..documents import pipeline, store
from ..imaging import quality, suitability


def store_private(owner: str, data: bytes, ext: str) -> str:
    return storage.put(owner, data, ext)


DOC_CATEGORIES = ("", "laboratory_report", "prescription", "consultation_note", "other")


def check_and_store_image(owner_id: str, region: str, data: bytes, request_id: str = "") -> dict:
    t = db.now()
    storage.check_quota(owner_id, len(data))
    scan = malware.check(data)                              # before any parser touches the bytes
    rep = suitability.assess(data, region)
    qa = quality.assess(data, region, legacy=rep)           # additive: warnings, advisory checks and a document-routing hint; patient categories are never rejected by v2
    iid, path = db.uid(), ""
    if rep.verdict != "rejected":   # data minimisation: rejected photos are not kept
        path = store_private(owner_id, data, "jpg")
    try:
        with db.tx() as tx:                                   # the row and its audit event commit together or not at all
            tx.x("INSERT INTO images(id,owner_id,region,stored_path,verdict,report,created_at,deleted,scan_status,size_bytes) VALUES(?,?,?,?,?,?,?,0,?,?)",
                 (iid, owner_id, region, path, rep.verdict, db.jdump(rep.to_dict()), db.now(), scan, len(data) if path else 0))
            db.audit(owner_id, "image.upload", iid, {"region": region, "verdict": rep.verdict, "reason": rep.reason, "request_id": request_id}, t=tx)
    except Exception:
        storage.delete(path)                                  # never leave a private file behind without its database record
        raise
    if path:                                              # only photos the clinic can actually open
        notify.to_role("doctor", "submission", "New photo to review", "A patient submitted a photo.", "/clinic", f"photo-submitted:{iid}")
    d = rep.to_dict()
    d.update(request_id=request_id, processing_ms=round((db.now() - t) * 1000), timestamp=db.now(), corrective_action=None if rep.verdict == "accepted" else rep.message)
    d["quality_v2"] = {"version": qa.version, "verdict": qa.verdict, "warnings": qa.warnings, "document_like": qa.document_like,
                       "checks": [c for c in qa.checks if c.get("source") is None], "validated_checks": [c["id"] for c in qa.checks if c.get("validated")]}
    if qa.document_like:
        d["routing"] = {"route": "document_pipeline", "message": "This looks like a document. Please upload it under Reports so it can be read and checked."}
        if rep.verdict != "accepted":
            d["corrective_action"] = d["routing"]["message"]
    return {"id": iid, "report": d,
            "capability_status": {"status": "available", "label": "Available", "note": "Photo check only. No condition is assessed."},
            "next": ("A clinician reviews accepted photos together with your history. No automatic diagnosis is made." if rep.verdict == "accepted" else rep.message)}


def ingest_document(owner_id: str, filename: str, data: bytes, category: str = "", request_id: str = "") -> dict:
    """Validate, extract, and persist a document. Raises pipeline.DocumentError with a patient-safe message."""
    if category not in DOC_CATEGORIES:
        raise pipeline.DocumentError("Unknown document category.")
    did = db.uid()
    storage.check_quota(owner_id, len(data))
    scan = malware.check(data)                              # before any parser touches the bytes
    try:
        res = pipeline.process(data, filename or "upload")
    except pipeline.DocumentError as e:
        db.audit(owner_id, "document.rejected", did, {"error": str(e), "request_id": request_id})
        raise
    ext = pipeline.validate(data, filename or "upload")
    path = store_private(owner_id, data, ext)
    try:
        with db.tx() as t:                                    # document, pages, fields and audit event: one atomic operation
            t.x("INSERT INTO documents(id,owner_id,filename,stored_path,mime,sha256,doc_type,doc_date,page_count,status,processing_version,"
                "review_status,language,error,created_at,deleted,size_bytes,user_category,scan_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                (did, owner_id, re.sub(r"[^\w .()-]", "_", filename or "upload")[:120], path, pipeline.ALLOWED[ext], hashlib.sha256(data).hexdigest(), res.doc_type,
                 res.doc_date, len(res.pages), "processed", res.version, "pending_clinician_review", "en", "; ".join(res.warnings), db.now(), len(data), category, scan))
            store.save_extraction(t, did, res)
            db.audit(owner_id, "document.upload", did, {"type": res.doc_type, "pages": len(res.pages), "fields": len(res.fields), "facts": len(res.facts), "medications": len(res.medications), "request_id": request_id}, t=t)
    except Exception:
        storage.delete(path)                                  # never leave a private file behind without its database record
        raise
    notify.send(owner_id, "report_ready", "Your report is ready", "It was processed and is waiting for clinician review.", f"/reports/{did}", f"report-ready:{did}")
    notify.to_role("doctor", "submission", "New report to review", "A patient uploaded a report.", "/clinic", f"doc-submitted:{did}")
    return {"id": did, "doc_type": res.doc_type, "doc_date": res.doc_date, "user_category": category, "pages": len(res.pages), "fields": len(res.fields),
            "facts": len(res.facts), "medications": len(res.medications), "readable": res.readable, "warnings": res.warnings, "status": "clinician_review_required"}


# ---------------------------------------------------------------- patient requests (used by the REST routes and by the assistant's confirmed actions: one implementation, one set of rules)
def request_quote(user: dict, items: list[dict], notes: str = "") -> dict:
    """A patient's request for a quotation: priced from the clinic's approved list, stored as a DRAFT that a clinic doctor must approve, and the clinic team is told. Raises quotes.QuoteError."""
    import json
    from ..quotes import service as quotes
    t = quotes.build_lines(items)
    qid = db.uid()
    db.x("INSERT INTO quotes(id,patient_id,status,lines,total,currency,notes,created_by,approved_by,created_at,updated_at,subtotal,tax_percent,tax_amount)"
         " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (qid, user["id"], "draft", json.dumps(t["lines"]), t["total"], t["currency"], notes[:500], user["id"], "", db.now(), db.now(), t["subtotal"], t["tax_percent"], t["tax_amount"]))
    db.audit(user["id"], "quote.request", qid)
    notify.to_role("doctor", "quote_requested", "New quotation request", "A patient asked for a quotation.", "/clinic", f"quote-request:{qid}")
    return {"id": qid, "status": "draft", **{k: t[k] for k in ("subtotal", "tax_amount", "total", "currency")},
            "message": "Your request was sent. The clinic team will review and approve a quotation."}


def flag_field(user: dict, doc_id: str, field_id: str, reason: str = "") -> dict:
    """A patient marks an extracted value as possibly wrong. LookupError when the document or value is not theirs (or does not exist)."""
    d = db.q1("SELECT owner_id FROM documents WHERE id=? AND deleted=0", (doc_id,))
    f = db.q1("SELECT id FROM doc_fields WHERE id=? AND document_id=?", (field_id, doc_id))
    if not d or d["owner_id"] != user["id"] or not f:
        raise LookupError("Not found.")
    db.x("UPDATE doc_fields SET patient_flag=? WHERE id=?", (reason or "flagged", field_id))
    db.audit(user["id"], "field.patient_flag", field_id)
    return {"flagged": True}


def request_review(user: dict, doc_id: str) -> dict:
    """A patient asks the clinic team to look at one of their documents. The team gets a generic notification (no file name, no values); asking again the same day does not notify twice.
    LookupError when the document is not theirs (or does not exist)."""
    import time
    d = db.q1("SELECT id,owner_id FROM documents WHERE id=? AND deleted=0", (doc_id,))
    if not d or d["owner_id"] != user["id"]:
        raise LookupError("Not found.")
    day = time.strftime("%Y%m%d")
    n = notify.to_role("doctor", "review_requested", "Document review requested", "A patient asked the clinic team to look at a document.", "/clinic", f"review-request:{doc_id}:{day}")
    db.audit(user["id"], "document.review_requested", doc_id, {})
    return {"requested": True, "document_id": doc_id, "clinic_notified": n > 0}

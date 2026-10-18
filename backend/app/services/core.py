"""Business operations shared by the synchronous routes and the asynchronous gateway, so both paths run identical logic."""
from __future__ import annotations

import hashlib
import re

from .. import db, malware, storage
from . import notify
from ..config import UPLOAD_DIR
from ..documents import pipeline
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
            for p in res.pages:
                t.x("INSERT INTO doc_pages VALUES(?,?,?,?,?,?)", (db.uid(), did, p.page_no, p.text, p.method, p.confidence))
            for n, f in enumerate(res.fields):
                t.x("INSERT INTO doc_fields(id,document_id,name,value,unit,ref_range,source_page,source_text,confidence,flag,review_status,corrected_by,processing_version,field_no)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (db.uid(), did, f.name, f.value, f.unit, f.ref_range, f.source_page, f.source_text, f.confidence, f.flag, f.review_status, "", res.version, n))
            db.audit(owner_id, "document.upload", did, {"type": res.doc_type, "pages": len(res.pages), "fields": len(res.fields), "request_id": request_id}, t=t)
    except Exception:
        storage.delete(path)                                  # never leave a private file behind without its database record
        raise
    notify.send(owner_id, "report_ready", "Your report is ready", "It was processed and is waiting for clinician review.", f"/reports/{did}", f"report-ready:{did}")
    notify.to_role("doctor", "submission", "New report to review", "A patient uploaded a report.", "/clinic", f"doc-submitted:{did}")
    return {"id": did, "doc_type": res.doc_type, "doc_date": res.doc_date, "user_category": category, "pages": len(res.pages), "fields": len(res.fields),
            "warnings": res.warnings, "status": "clinician_review_required"}

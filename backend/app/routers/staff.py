import json
import shutil
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import clinical_content, db, retention
from ..services import notify
from ..config import DATASET_DIR
from ..datasets import importer, registry
from ..quotes import service as quotes
from ..security import require
from ..training import orchestrator, pipeline, tasks, trainer

router = APIRouter(prefix="/api")
doctor = require("doctor", "admin")
admin = require("admin")


# ---------------- clinician review ----------------

@router.get("/clinic/submissions")
def submissions(user=Depends(doctor)):
    return {"documents": db.q("SELECT d.id,d.owner_id,u.name patient,d.filename,d.doc_type,d.review_status,d.created_at,"
                              "(SELECT COUNT(*) FROM doc_fields f WHERE f.document_id=d.id AND f.patient_flag!='') flagged FROM documents d "
                              "JOIN users u ON u.id=d.owner_id WHERE d.deleted=0 ORDER BY d.created_at DESC LIMIT 200"),
            "images": [dict(r, report=json.loads(r["report"])) for r in db.q(
                "SELECT i.id,i.owner_id,u.name patient,i.region,i.verdict,i.report,i.created_at,i.clinician_review,i.reviewed_by,i.stored_path!='' has_file "
                "FROM images i JOIN users u ON u.id=i.owner_id WHERE i.deleted=0 ORDER BY i.created_at DESC LIMIT 200")],
            "quotes": db.q("SELECT q.id,q.patient_id,u.name patient,q.status,q.total,q.currency FROM quotes q JOIN users u ON u.id=q.patient_id ORDER BY q.created_at DESC LIMIT 200")}


@router.post("/clinic/images/{iid}/review")
def review_image(iid: str, user=Depends(require("doctor"))):
    r = db.q1("SELECT id,stored_path FROM images WHERE id=? AND deleted=0", (iid,))
    if not r or not r["stored_path"]:
        raise HTTPException(404, "Photo not found.")
    db.x("UPDATE images SET clinician_review='reviewed', reviewed_by=?, reviewed_at=? WHERE id=?", (user["id"], db.now(), iid))
    db.audit(user["id"], "image.reviewed", iid)
    own = db.q1("SELECT owner_id FROM images WHERE id=?", (iid,))
    notify.send(own["owner_id"], "photo_reviewed", "A clinician reviewed your photo", "Open your history to see the review.", "/history", f"photo-reviewed:{iid}")
    return {"clinician_review": "reviewed"}


@router.get("/clinic/cases/{patient_id}")
def case(patient_id: str, user=Depends(doctor)):
    p = db.q1("SELECT id,name FROM users WHERE id=? AND role='patient'", (patient_id,))
    if not p:
        raise HTTPException(404, "Patient not found.")
    db.audit(user["id"], "case.opened", patient_id)
    return {"patient": p,
            "photos": [dict(r, report=json.loads(r["report"])) for r in db.q(
                "SELECT id,region,verdict,report,created_at,clinician_review,stored_path!='' has_file FROM images WHERE owner_id=? AND deleted=0 ORDER BY created_at DESC", (patient_id,))],
            "documents": db.q("SELECT d.id,d.filename,d.doc_type,d.review_status,d.created_at,d.mime,(SELECT COUNT(*) FROM doc_fields f WHERE f.document_id=d.id AND f.patient_flag!='') flagged "
                              "FROM documents d WHERE d.owner_id=? AND d.deleted=0 ORDER BY d.created_at DESC", (patient_id,)),
            "quotes": [dict(r, lines=json.loads(r["lines"])) for r in db.q("SELECT id,status,total,currency,lines,notes FROM quotes WHERE patient_id=? ORDER BY created_at DESC", (patient_id,))]}


class FieldEdit(BaseModel):
    value: str | None = None
    unit: str | None = None
    ref_range: str | None = None
    review_status: str = Field(pattern="^(confirmed|corrected|rejected)$")


@router.patch("/clinic/fields/{fid}")
def review_field(fid: str, body: FieldEdit, user=Depends(doctor)):
    f = db.q1("SELECT * FROM doc_fields WHERE id=?", (fid,))
    if not f:
        raise HTTPException(404, "Field not found.")
    before = {k: f[k] for k in ("value", "unit", "ref_range")}
    db.x("UPDATE doc_fields SET value=?, unit=?, ref_range=?, review_status=?, corrected_by=? WHERE id=?",
         (body.value if body.value is not None else f["value"], body.unit if body.unit is not None else f["unit"],
          body.ref_range if body.ref_range is not None else f["ref_range"], body.review_status, user["id"], fid))
    after = db.q1("SELECT * FROM doc_fields WHERE id=?", (fid,))
    # versioned, attributable correction record; NEVER eligible for training until governance approval is recorded
    db.x("INSERT INTO field_corrections(id,field_id,document_id,before,after,status,reviewer,ts,processing_version) VALUES(?,?,?,?,?,?,?,?,?)",
         (db.uid(), fid, f["document_id"], json.dumps({**before, "review_status": f["review_status"]}),
          json.dumps({k: after[k] for k in ("value", "unit", "ref_range", "review_status")}), body.review_status, user["id"], db.now(), f["processing_version"]))
    db.audit(user["id"], "field.review", fid, {"before": before, "status": body.review_status})
    return after


class MedReview(BaseModel):
    status: str = Field(pattern="^(confirmed|corrected|rejected)$")
    corrections: dict[str, str] = Field(default_factory=dict, max_length=12)     # component -> the text as it should read


MED_COMPONENTS = {"name", "form", "dose", "dose_pattern", "route", "frequency", "duration"}


@router.post("/clinic/medications/{mid}/review")
def review_medication(mid: str, body: MedReview, user=Depends(require("doctor"))):
    """A clinician confirms, rejects or corrects an automatically read medicine entry. The original reading is kept; a correction replaces the printed text of the named components,
    drops their source position (it no longer points at the document) and says who changed it. Nothing is recommended or changed about the medicine itself."""
    row = db.q1("SELECT * FROM doc_medications WHERE id=?", (mid,))
    if not row:
        raise HTTPException(404, "Medicine entry not found.")
    entry = json.loads(row["entry"])
    if body.status == "corrected":
        bad = sorted(set(body.corrections) - MED_COMPONENTS - {"strength"})
        if not body.corrections or bad:
            raise HTTPException(422, "Name the components to correct" + (f"; unknown: {bad}" if bad else "") + f" (allowed: {sorted(MED_COMPONENTS | {'strength'})}).")
        for k, v in body.corrections.items():
            v = v.strip()[:120]
            comp = {"raw": v, "span": None, "normalized": None, "note": f"corrected by {user['id']}"} if v else None
            if k == "strength":
                entry["strengths"] = [comp] if comp else []
            else:
                entry[k] = comp
        entry["ambiguity"] = [a for a in entry.get("ambiguity", [])] + ["corrected_by_clinician"]
    original = row["original"] or (row["entry"] if body.status == "corrected" else "")
    name = (entry.get("name") or {}).get("raw", "") if body.status == "corrected" else row["name"]
    db.x("UPDATE doc_medications SET entry=?, review_status=?, reviewed_by=?, original=?, name=? WHERE id=?",
         (json.dumps(entry, ensure_ascii=False), body.status, user["id"], original, name, mid))
    db.audit(user["id"], "medication.review", mid, {"status": body.status, "components": sorted(body.corrections)})
    return {"id": mid, "review_status": body.status, "entry": entry}


@router.post("/clinic/documents/{doc_id}/review")
def review_document(doc_id: str, user=Depends(doctor)):
    if not db.q1("SELECT id FROM documents WHERE id=? AND deleted=0", (doc_id,)):
        raise HTTPException(404, "Document not found.")
    db.x("UPDATE documents SET review_status='reviewed_by_clinician' WHERE id=?", (doc_id,))
    db.audit(user["id"], "document.reviewed", doc_id)
    own = db.q1("SELECT owner_id FROM documents WHERE id=?", (doc_id,))
    notify.send(own["owner_id"], "report_reviewed", "A clinician reviewed your report", "Open the report to see what was checked.", f"/reports/{doc_id}", f"report-reviewed:{doc_id}")
    return {"review_status": "reviewed_by_clinician"}


# ---------------- price list & quotations ----------------

class PriceItem(BaseModel):
    name: str
    description: str = ""
    unit_price: float = Field(ge=0)
    currency: str = "INR"
    category: str = "General"
    sessions: int = Field(default=1, ge=1, le=100)
    duration_min: int = Field(default=0, ge=0, le=600)
    compare_at_price: float | None = Field(default=None, ge=0)


@router.post("/clinic/price-items")
def add_price_item(body: PriceItem, user=Depends(doctor)):
    pid = db.uid()
    db.x("INSERT INTO price_items(id,name,description,unit_price,currency,active,approved_by,category,sessions,duration_min,compare_at_price)"
         " VALUES(?,?,?,?,?,1,?,?,?,?,?)", (pid, body.name, body.description, body.unit_price, body.currency, user["id"], body.category,
                                          body.sessions, body.duration_min, body.compare_at_price))
    db.audit(user["id"], "price.add", pid, body.model_dump())
    return {"id": pid}


class QuoteEdit(BaseModel):
    items: list[dict]
    notes: str = ""


@router.put("/clinic/quotes/{qid}")
def edit_quote(qid: str, body: QuoteEdit, user=Depends(doctor)):
    qu = db.q1("SELECT * FROM quotes WHERE id=?", (qid,))
    if not qu:
        raise HTTPException(404, "Quotation not found.")
    try:
        t = quotes.build_lines(body.items)
    except quotes.QuoteError as e:
        raise HTTPException(422, str(e))
    db.x("UPDATE quotes SET lines=?, total=?, currency=?, notes=?, status='draft', approved_by='', updated_at=?, subtotal=?, tax_percent=?, tax_amount=? WHERE id=?",
         (json.dumps(t["lines"]), t["total"], t["currency"], body.notes[:500], db.now(), t["subtotal"], t["tax_percent"], t["tax_amount"], qid))
    db.audit(user["id"], "quote.edit", qid)
    return {"id": qid, "status": "draft", "total": t["total"]}


class QuoteConfig(BaseModel):
    tax_percent: float = Field(default=0, ge=0, le=100)
    validity_days: int = Field(default=30, ge=0, le=730)      # 0 = never expires
    payment_options: list[str] = []
    inclusions: list[str] = []
    terms: str = ""


@router.put("/clinic/quote-config")
def set_quote_config(body: QuoteConfig, user=Depends(doctor)):
    quotes.set_config(body.model_dump())
    db.audit(user["id"], "quote.config", "", body.model_dump())
    return quotes.get_config()


@router.post("/clinic/quotes/{qid}/approve")
def approve_quote(qid: str, user=Depends(require("doctor"))):
    if not db.q1("SELECT id FROM quotes WHERE id=?", (qid,)):
        raise HTTPException(404, "Quotation not found.")
    db.x("UPDATE quotes SET status='approved', approved_by=?, updated_at=?, valid_until=? WHERE id=?", (user["id"], db.now(), quotes.valid_until_for_approval(db.now()), qid))
    db.audit(user["id"], "quote.approve", qid)
    q = db.q1("SELECT patient_id FROM quotes WHERE id=?", (qid,))
    notify.send(q["patient_id"], "quote_approved", "Your quotation is approved", "You can now view and download it.", "/quote", f"quote-approved:{qid}")
    return {"status": "approved"}


# ---------------- knowledge base ----------------

class KB(BaseModel):
    title: str
    body: str
    language: str = "en"


class KBEdit(BaseModel):
    title: str
    body: str


class KBApproval(BaseModel):
    attest: bool = False
    sources_checked: str = ""          # what the reviewer verified the text against (required)


@router.get("/clinic/kb")
def kb_list(user=Depends(doctor)):
    rows = db.q("SELECT * FROM kb_articles ORDER BY title")
    for r in rows:
        r["claims_to_verify"] = json.loads(r.get("claims_to_verify") or "[]")
        overdue = bool(r["review_status"] == "approved" and r.get("review_due") and r["review_due"] < db.now())
        r["review_overdue"] = overdue
        r["usable_by_assistant"] = r["review_status"] == "approved" and not overdue and r.get("content_sha256") == clinical_content.article_hash(r["title"], r["body"], r["language"] or "en")
    return rows


@router.post("/clinic/kb")
def kb_add(body: KB, user=Depends(doctor)):
    kid = db.uid()
    db.x("INSERT INTO kb_articles(id,title,body,language,review_status,reviewed_by,version,content_origin) VALUES(?,?,?,?, 'draft','',1,'clinic')", (kid, body.title, body.body, body.language))
    db.audit(user["id"], "kb.add", kid)
    return {"id": kid, "review_status": "draft"}


@router.put("/clinic/kb/{kid}")
def kb_edit(kid: str, body: KBEdit, user=Depends(doctor)):
    """Any change returns the article to draft and bumps the version: approval applies to one exact text."""
    r = db.q1("SELECT * FROM kb_articles WHERE id=?", (kid,))
    if not r:
        raise HTTPException(404, "Article not found.")
    if not body.title.strip() or not body.body.strip():
        raise HTTPException(422, "Title and text are required.")
    db.x("UPDATE kb_articles SET title=?, body=?, review_status='draft', reviewed_by='', reviewed_at=NULL, review_due=NULL, content_sha256='', attestation='', version=version+1 WHERE id=?",
         (body.title.strip(), body.body.strip(), kid))
    db.audit(user["id"], "kb.edit", kid, {"version": r["version"] + 1, "was": r["review_status"]})
    return {"review_status": "draft", "version": r["version"] + 1}


@router.post("/clinic/kb/{kid}/approve")
def kb_approve(kid: str, body: KBApproval | None = None, user=Depends(require("doctor"))):
    r = db.q1("SELECT * FROM kb_articles WHERE id=?", (kid,))
    if not r:
        raise HTTPException(404, "Article not found.")
    body = body or KBApproval()
    user = {**user, **(db.q1("SELECT credentials,verified FROM users WHERE id=?", (user["id"],)) or {})}
    ok, why = clinical_content.approver_ok(user)
    if not ok:
        raise HTTPException(403, why)
    if not body.attest or len(body.sources_checked.strip()) < 10:
        raise HTTPException(422, "Approval needs your confirmation that you reviewed this exact text, and a note of the source(s) you checked it against.")
    digest = clinical_content.article_hash(r["title"], r["body"], r["language"] or "en")
    attestation = json.dumps({"reviewer": user["id"], "credentials": user.get("credentials", ""), "verified": bool(user.get("verified")),
                              "statement": "I reviewed this exact text and approve it as accurate, appropriate for patients and non-diagnostic.", "content_sha256": digest})
    days = clinical_content.review_period_days()
    db.x("UPDATE kb_articles SET review_status='approved', reviewed_by=?, reviewed_at=?, content_sha256=?, attestation=?, source_note=?, review_due=? WHERE id=?",
         (user["id"], db.now(), digest, attestation, body.sources_checked.strip(), db.now() + days * 86400 if days else None, kid))
    db.audit(user["id"], "kb.approve", kid, {"version": r["version"], "content_sha256": digest, "origin": r["content_origin"]})
    return {"review_status": "approved", "content_sha256": digest}


@router.get("/clinic/content-status")
def content_status(user=Depends(doctor)):
    return clinical_content.status()


# ---------------- monitoring ----------------

@router.get("/clinic/metrics")
def metrics(user=Depends(doctor)):
    one = lambda sql: db.q1(sql)["c"]
    asks = one("SELECT COUNT(*) c FROM audit WHERE action='assistant.ask'")
    abst = one("SELECT COUNT(*) c FROM audit WHERE action='assistant.ask' AND detail LIKE '%\"abstained\": true%'")
    return {"documents": one("SELECT COUNT(*) c FROM documents WHERE deleted=0"),
            "documents_rejected": one("SELECT COUNT(*) c FROM audit WHERE action='document.rejected'"),
            "image_uploads": one("SELECT COUNT(*) c FROM audit WHERE action='image.upload'"),
            "images_rejected": one("SELECT COUNT(*) c FROM images WHERE verdict='rejected'"),
            "fields_total": one("SELECT COUNT(*) c FROM doc_fields"),
            "fields_corrected": one("SELECT COUNT(*) c FROM doc_fields WHERE review_status='corrected'"),
            "fields_rejected": one("SELECT COUNT(*) c FROM doc_fields WHERE review_status='rejected'"),
            "assistant_questions": asks, "assistant_abstentions": abst}


# ---------------- admin: datasets ----------------

@router.get("/admin/datasets")
def datasets_list(user=Depends(admin)):
    return registry.list_all()


class Verify(BaseModel):
    verified_spdx: str = ""
    permission_document: str = ""
    notes: str = ""
    terms_verified: bool = True


@router.post("/admin/datasets/{did}/fetch-terms")
def datasets_fetch_terms(did: str, user=Depends(admin)):
    try:
        d = registry.fetch_terms(did, user["id"])
    except KeyError:
        raise HTTPException(404, "Unknown dataset.")
    except Exception as e:
        raise HTTPException(502, f"Could not retrieve the official terms page: {e}")
    return {"terms_snapshot_hash": d["terms_snapshot_hash"], "terms_checked_at": d["terms_checked_at"]}


@router.post("/admin/datasets/{did}/verify")
def datasets_verify(did: str, body: Verify, user=Depends(admin)):
    d = registry.get(did)
    if not d:
        raise HTTPException(404, "Unknown dataset.")
    if body.terms_verified and not d.get("terms_snapshot_hash") and not body.permission_document:
        raise HTTPException(422, "Fetch and record the official terms (or attach a permission document reference) before verifying.")
    official = (d.get("official_license_name") or "").strip().lower()
    if body.terms_verified and official and body.verified_spdx and registry.SPDX_ALIASES.get(official) != body.verified_spdx:
        raise HTTPException(422, f'Official record says "{d["official_license_name"]}", which does not match "{body.verified_spdx}".')
    d.update(terms_verified=body.terms_verified, verified_spdx=body.verified_spdx, permission_document=body.permission_document, verify_notes=body.notes,
             verified_by=user["id"])
    registry.save(d)
    db.audit(user["id"], "dataset.verify_terms", did, body.model_dump())
    return registry.get(did)


class Approve(BaseModel):
    approved: bool
    commercial: bool = True
    use: str = "model_training"


@router.post("/admin/datasets/{did}/approve")
def datasets_approve(did: str, body: Approve, user=Depends(admin)):
    d = registry.get(did)
    if not d:
        raise HTTPException(404, "Unknown dataset.")
    if body.approved:
        d["approval_status"] = "approved"
        gate = registry.license_gate(d, body.use, body.commercial)
        # gate is evaluated with approval in place; approval itself cannot override licence problems
        blocking = [r for r in gate["reasons"] if "administrator has not approved" not in r]
        if blocking:
            raise HTTPException(409, {"message": "Licence gate blocked approval.", "reasons": blocking})
    else:
        d["approval_status"] = "rejected"
    registry.save(d)
    db.audit(user["id"], "dataset.approve", did, body.model_dump())
    return registry.get(did)


@router.get("/admin/datasets/{did}/compatibility")
def datasets_compat(did: str, user=Depends(admin)):
    d = registry.get(did)
    if not d:
        raise HTTPException(404, "Unknown dataset.")
    return [registry.compatibility(d, t) for t in tasks.list_tasks()]


class ImportReq(BaseModel):
    folder: str
    labels_csv: str | None = None
    limit: int | None = Field(default=500, ge=1, le=100000)
    commercial: bool = True


@router.post("/admin/datasets/{did}/import-folder")
def datasets_import(did: str, body: ImportReq, user=Depends(admin)):
    d = registry.get(did)
    if not d:
        raise HTTPException(404, "Unknown dataset.")
    gate = registry.license_gate(d, "model_training", body.commercial)
    if not gate["allowed"]:
        db.audit(user["id"], "dataset.import_blocked", did, gate)
        raise HTTPException(409, {"message": "Import blocked by the licence gate.", "reasons": gate["reasons"]})
    folder = Path(body.folder).resolve()
    if not folder.is_dir():
        raise HTTPException(422, "Folder not found.")
    try:
        return importer.ingest_folder(did, folder, user["id"], Path(body.labels_csv) if body.labels_csv else None, body.limit)
    except importer.ImportError_ as e:
        raise HTTPException(507, str(e))


@router.post("/admin/datasets/{did}/cancel")
def datasets_cancel(did: str, user=Depends(admin)):
    importer.cancel(did)
    db.audit(user["id"], "dataset.cancel", did)
    return {"cancelled": True}


@router.get("/admin/datasets/{did}/versions")
def datasets_versions(did: str, user=Depends(admin)):
    rows = db.q("SELECT id,created_at,manifest FROM dataset_versions WHERE dataset_id=? ORDER BY created_at DESC", (did,))
    return [{"id": r["id"], "created_at": r["created_at"], "version": json.loads(r["manifest"])["version"],
             "count": json.loads(r["manifest"])["count"], "duplicates": len(json.loads(r["manifest"])["duplicates_skipped"])} for r in rows]


@router.get("/admin/system")
def system(user=Depends(admin)):
    total, used, free = shutil.disk_usage(DATASET_DIR)
    from ..speech import adapters
    try:
        import torch
        dev = trainer.pick_device()
        tv = torch.__version__
    except ImportError:
        dev, tv = "unavailable", None
    from ..documents.pipeline import ocr_available
    return {"free_disk_gb": round(free / 1e9, 1), "training_device": dev, "torch": tv, "ocr_available": ocr_available(),
            "tts": adapters.get_tts().status(), "stt": adapters.stt_status(), "conversation_retention_days": retention.configured_days().get("conversations")}


# ---------------- admin: tasks, training, models ----------------

@router.get("/tasks")
def tasks_list(user=Depends(doctor)):
    return tasks.list_tasks()


class TrainReq(BaseModel):
    task_id: str
    dataset_id: str
    version_id: str
    architecture: str = "resnet18"
    epochs: int = Field(default=3, ge=1, le=100)
    lr: float = Field(default=1e-3, gt=0, le=0.1)
    batch: int = Field(default=16, ge=1, le=256)
    image_size: int = Field(default=224, ge=64, le=512)
    finetune: str = Field(default="layer4", pattern="^(head|layer4|all)$")
    weights_path: str | None = None
    pretrained_note: str = ""
    ood_dir: str | None = None
    max_per_split: int | None = None
    seed: int = 17
    resume: bool = False          # continue from the last completed epoch of an identical run (same data, split, settings and torch version)
    method: str = Field(default="finetune", pattern="^(finetune|probe)$")
    device: str = Field(default="auto", pattern="^(auto|cpu|cuda|mps)$")
    allow_long_run: bool = False


_jobs: dict[str, dict] = {}
_stop: set[str] = set()


@router.post("/admin/train")
def train(body: TrainReq, user=Depends(admin)):
    t = tasks.get_task(body.task_id)
    d = registry.get(body.dataset_id)
    if not t or not d:
        raise HTTPException(404, "Unknown task or dataset.")
    comp = registry.compatibility(d, t)
    if not comp["compatible"]:
        raise HTTPException(409, {"message": "Dataset is not compatible with this task.", "reasons": comp["reasons_incompatible"]})
    gate = registry.license_gate(d, "model_training", commercial=False)
    if not gate["allowed"]:
        raise HTTPException(409, {"message": "Licence gate blocked training.", "reasons": gate["reasons"]})
    if body.weights_path and not Path(body.weights_path).is_file():
        raise HTTPException(422, "Pretrained weights file not found. Weights are never downloaded implicitly.")
    cfg = _train_config(body)
    pl = orchestrator.plan(cfg)
    if not pl["feasible"]:
        db.audit(user["id"], "training.refused", body.dataset_id, {"blockers": pl["blockers"][:5]})
        raise HTTPException(409, {"message": "This training run cannot start here.", "reasons": pl["blockers"], "plan": pl})
    job = db.uid()
    _jobs[job] = {"status": "running", "phase": "starting", "percent": 0.0, "log": [], "started": db.now(), "plan": pl}
    lic = {"spdx": d.get("verified_spdx"), "terms_snapshot_hash": d.get("terms_snapshot_hash"), "official_license_name": d.get("official_license_name"),
           "source": d.get("url"), "attribution": d.get("attribution")}

    def progress(ev):
        j = _jobs[job]
        if "log" in ev:
            j["log"].append(ev["log"])
        if "percent" in ev:
            j["percent"], j["phase"] = ev["percent"], ev.get("phase", j["phase"])

    def run():
        try:
            res = orchestrator.run_training(cfg, user["id"], progress=progress, should_stop=lambda: job in _stop, license_info=lic)
        except orchestrator.NotFeasible as e:
            _jobs[job].update(status="failed", error="; ".join(e.blockers))
            return
        except Exception as e:  # surfaced to the admin, never silently swallowed
            _jobs[job].update(status="failed", error=str(e)[:300])
            db.audit(user["id"], "model.train_failed", job, {"error": str(e)[:300]})
            return
        if res["status"] == "done":
            _jobs[job].update(status="done", model_id=res["model_id"], percent=100.0, run_id=res["run_id"])
        elif res["status"] == "stopped":
            _jobs[job].update(status="stopped", run_id=res["run_id"])
            db.audit(user["id"], "model.train_stopped", job)
        else:
            _jobs[job].update(status="failed", error=res.get("error", "training failed"), run_id=res["run_id"])
    db.context.spawn(run)
    return {"job_id": job, "plan": pl}


def _train_config(body: "TrainReq") -> orchestrator.TrainConfig:
    return orchestrator.TrainConfig(task_id=body.task_id, dataset_id=body.dataset_id, version_id=body.version_id, method=body.method, architecture=body.architecture, epochs=body.epochs,
                                    lr=body.lr, batch=body.batch, image_size=body.image_size, finetune=body.finetune, weights_path=body.weights_path, pretrained_note=body.pretrained_note,
                                    ood_dir=body.ood_dir, max_per_split=body.max_per_split, seed=body.seed, resume=body.resume, device=body.device, allow_long_run=body.allow_long_run)


@router.post("/admin/train/plan")
def train_plan(body: TrainReq, user=Depends(admin)):
    """What a training run would need and whether it fits this machine, without starting it."""
    return orchestrator.plan(_train_config(body))


@router.get("/admin/training-runs")
def training_runs(limit: int = 50, user=Depends(admin)):
    return orchestrator.list_runs(limit)


@router.get("/admin/tasks/{task}/readiness")
def task_readiness(task: str, user=Depends(admin)):
    """Can a training run for this task start today? If not: exactly why (licence gate, missing data, import)."""
    try:
        return pipeline.readiness(task)
    except KeyError:
        raise HTTPException(404, "Unknown task.")


@router.get("/admin/datasets/{did}/card")
def dataset_card(did: str, markdown: bool = False, user=Depends(admin)):
    c = registry.card(did)
    if c is None:
        raise HTTPException(404, "Unknown dataset.")
    from fastapi.responses import PlainTextResponse
    from ..training import cards
    return PlainTextResponse(cards.dataset_card_markdown(c), media_type="text/markdown") if markdown else c


@router.get("/admin/models/{mid}/card")
def model_card(mid: str, user=Depends(admin)):
    from fastapi.responses import PlainTextResponse
    from ..training import cards
    ms = {m["model_id"]: m for m in trainer.list_models()}
    if mid not in ms:
        raise HTTPException(404, "Unknown model.")
    m = ms[mid]
    return PlainTextResponse(cards.model_card_markdown(m, m["gates"], m.get("status", ""), m.get("evidence_class", "public_research_dataset")), media_type="text/markdown")


@router.post("/admin/datasets/cleanup-partials")
def cleanup_partials(older_than_hours: float = 24.0, user=Depends(admin)):
    """Remove stale incomplete downloads. Recent ones are kept: they are what makes a download resumable."""
    removed = importer.sweep_partials(older_than_s=max(older_than_hours, 1.0) * 3600)
    db.audit(user["id"], "dataset.cleanup_partials", "", {"removed": len(removed)})
    return {"removed": removed}


@router.get("/admin/train/checkpoints")
def train_checkpoints(user=Depends(admin)):
    return trainer.list_checkpoints()


@router.get("/admin/jobs/{job}")
def job_status(job: str, user=Depends(admin)):
    return _jobs.get(job) or {"status": "unknown"}


@router.post("/admin/jobs/{job}/stop")
def job_stop(job: str, user=Depends(admin)):
    if job not in _jobs or _jobs[job]["status"] != "running":
        raise HTTPException(404, "No running job with that id.")
    _stop.add(job)
    db.audit(user["id"], "model.train_stop_requested", job)
    return {"stopping": True}


@router.get("/admin/models")
def models(task_id: str | None = None, user=Depends(admin)):
    return trainer.list_models(task_id)


@router.get("/admin/models/compare")
def models_compare(a: str, b: str, user=Depends(admin)):
    try:
        return trainer.compare(a, b)
    except KeyError:
        raise HTTPException(404, "Unknown model.")


class ApproveModel(BaseModel):
    intended_use: str
    requirements: dict
    regulatory_note: str = ""


@router.post("/admin/models/{mid}/approval")
def model_approval(mid: str, body: ApproveModel, user=Depends(admin)):
    try:
        return trainer.approve(mid, user["id"], body.intended_use, body.requirements, body.regulatory_note)
    except KeyError:
        raise HTTPException(404, "Unknown model.")


@router.post("/admin/models/{mid}/deploy")
def model_deploy(mid: str, user=Depends(admin)):
    try:
        return trainer.deploy(mid, user["id"])
    except PermissionError as e:
        raise HTTPException(409, str(e))


@router.post("/admin/tasks/{task}/rollback")
def model_rollback(task: str, user=Depends(admin)):
    return trainer.rollback(task, user["id"])


@router.get("/admin/audit")
def audit_log(limit: int = 200, user=Depends(admin)):
    return db.q("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (min(limit, 1000),))


class PurgeBody(BaseModel):
    dry_run: bool = True
    confirm: str = ""


@router.get("/admin/retention/plan")
def retention_plan(user=Depends(admin)):
    """What a purge would remove under the configured periods. Categories without an operator-chosen period are reported as not configured and are never purged."""
    try:
        return {"categories": retention.plan(), "holds": len(retention.active_holds())}
    except retention.ConfigError as e:
        raise HTTPException(422, str(e))


@router.post("/admin/retention/purge")
def purge(body: PurgeBody | None = None, user=Depends(admin)):
    """Dry run by default. Deleting needs {"dry_run": false, "confirm": "DELETE"}; it runs in bounded batches and records counts only."""
    body = body or PurgeBody()
    if not body.dry_run and body.confirm != "DELETE":
        raise HTTPException(422, 'To delete data send {"dry_run": false, "confirm": "DELETE"}.')
    try:
        rep = retention.run(dry_run=body.dry_run, actor=user["id"])
    except retention.ConfigError as e:
        raise HTTPException(422, str(e))
    if not body.dry_run:
        db.audit(user["id"], "retention.run", "", {"categories": {c["category"]: c.get("purged", 0) for c in rep["categories"]}})
    return rep


class HoldBody(BaseModel):
    patient_id: str
    reason_code: str


@router.get("/admin/legal-holds")
def holds_list(user=Depends(admin)):
    return {"holds": retention.active_holds()}


@router.post("/admin/legal-holds")
def hold_place(body: HoldBody, user=Depends(admin)):
    try:
        return retention.place_hold(body.patient_id, body.reason_code, user["id"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.post("/admin/legal-holds/{hold_id}/release")
def hold_release(hold_id: str, user=Depends(admin)):
    if not retention.release_hold(hold_id, user["id"]):
        raise HTTPException(404, "No active hold with that id.")
    return {"ok": True}


# ---------------- admin: gated download of official files ----------------

_dl: dict[str, dict] = {}


@router.post("/admin/datasets/{did}/download")
def datasets_download(did: str, user=Depends(admin)):
    d = registry.get(did)
    if not d:
        raise HTTPException(404, "Unknown dataset.")
    files = (d.get("official_manifest") or {}).get("files")
    if not files:
        raise HTTPException(422, "No official file manifest recorded. Retrieve the official terms first (supported repositories only).")
    gate = registry.license_gate(d, "model_training", commercial=True)
    if not gate["allowed"]:
        db.audit(user["id"], "dataset.download_blocked", did, gate)
        raise HTTPException(409, {"message": "Download blocked by the licence gate.", "reasons": gate["reasons"]})
    if _dl.get(did, {}).get("state") == "running":
        raise HTTPException(409, "A download is already running.")
    total = sum(f["size"] for f in files)
    try:
        importer.check_disk(total * 2)
    except importer.ImportError_ as e:
        raise HTTPException(507, str(e))
    raw = DATASET_DIR / did / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    _dl[did] = {"state": "running", "files": {f["name"]: {"done": 0, "total": f["size"], "verified": False} for f in files}, "error": ""}
    db.audit(user["id"], "dataset.download_start", did, {"files": [f["name"] for f in files], "bytes": total})

    def run():
        try:
            for f in files:
                def prog(done, tot, name=f["name"]):
                    _dl[did]["files"][name].update(done=done, total=tot or f["size"])
                importer.download(f["url"], raw / f["name"], f["sha256"], did, on_progress=prog)
                _dl[did]["files"][f["name"]].update(done=f["size"], verified=True)
            _dl[did]["state"] = "done"
            db.audit(user["id"], "dataset.download_done", did, {"sha256_verified": True})
        except Exception as e:
            _dl[did].update(state="failed", error=str(e))
            db.audit(user["id"], "dataset.download_failed", did, {"error": str(e)})
    db.context.spawn(run)
    return {"state": "running"}


@router.get("/admin/datasets/{did}/download")
def datasets_download_status(did: str, user=Depends(admin)):
    return _dl.get(did) or {"state": "idle"}


# ---------------- clinician corrections: monitoring and governed training eligibility ----------------

@router.get("/admin/corrections/summary")
def corrections_summary(user=Depends(admin)):
    rows = db.q("SELECT status, COUNT(*) c FROM field_corrections GROUP BY status")
    def values(x):
        d = json.loads(x)
        return (d.get("value"), d.get("unit"), d.get("ref_range"))
    changed = sum(1 for r in db.q("SELECT before, after FROM field_corrections") if values(r["before"]) != values(r["after"]))
    return {"by_status": {r["status"]: r["c"] for r in rows}, "records_where_values_changed": changed,
            "training_eligible": db.q1("SELECT COUNT(*) c FROM field_corrections WHERE training_eligible=1")["c"],
            "policy": "Corrections are error-analysis evidence by default. They become training-eligible only with a recorded governance reference and confirmed de-identification."}


class Eligibility(BaseModel):
    governance_ref: str = Field(min_length=3, max_length=200)
    deidentified: bool


@router.post("/admin/corrections/{cid}/eligibility")
def corrections_eligibility(cid: str, body: Eligibility, user=Depends(admin)):
    if not body.deidentified:
        raise HTTPException(422, "Records must be de-identified before they can be considered for training.")
    if not db.q1("SELECT id FROM field_corrections WHERE id=?", (cid,)):
        raise HTTPException(404, "Correction not found.")
    db.x("UPDATE field_corrections SET training_eligible=1, governance_ref=?, deidentified=1 WHERE id=?", (body.governance_ref, cid))
    db.audit(user["id"], "correction.training_eligible", cid, {"governance_ref": body.governance_ref})
    return {"training_eligible": True}

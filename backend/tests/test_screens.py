import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_api import lab_pdf, tok


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_validation_checklist_is_real(client):
    p1 = tok(client, "patient1")
    img = np.random.default_rng(5).integers(60, 200, (500, 500, 3), dtype=np.uint8)
    r = client.post("/api/images", data={"region": "face"}, files={"file": ("f.jpg", cv2.imencode(".jpg", img)[1].tobytes())}, headers=p1).json()
    checks = {c["id"]: c for c in r["report"]["checks"]}
    assert list(checks) == ["format", "quality", "lighting", "subject", "framing", "overall"]
    assert checks["subject"]["status"] == "failed" and checks["overall"]["status"] == "failed"
    assert checks["quality"]["status"] == "passed"          # derived from measurements, not hard-coded success
    dark = (img * 0.1).astype(np.uint8)
    r = client.post("/api/images", data={"region": "other"}, files={"file": ("d.jpg", cv2.imencode(".jpg", dark)[1].tobytes())}, headers=p1).json()
    assert {c["id"]: c["status"] for c in r["report"]["checks"]}["lighting"] == "failed"
    bad = client.post("/api/images", data={"region": "face"}, files={"file": ("x.jpg", b"junk")}, headers=p1).json()
    assert bad["report"]["checks"][0]["status"] == "failed" and bad["report"]["checks"][1]["status"] == "skipped"


def test_document_category_size_thumbnail_and_flag(client):
    p1, p2 = tok(client, "patient1"), tok(client, "patient2")
    pdf = lab_pdf(["Hemoglobin     10.2     g/dL      12.0 - 15.5"])
    r = client.post("/api/documents", data={"category": "laboratory_report"}, files={"file": ("lab.pdf", pdf)}, headers=p1)
    assert r.status_code == 200 and r.json()["user_category"] == "laboratory_report"
    assert client.post("/api/documents", data={"category": "bogus"}, files={"file": ("lab.pdf", pdf)}, headers=p1).status_code == 400
    d = client.get("/api/documents", headers=p1).json()[0]
    assert d["size_bytes"] == len(pdf) and d["page_count"] == 1
    did = r.json()["id"]
    th = client.get(f"/api/documents/{did}/thumbnail", headers=p1)
    assert th.status_code == 200 and th.content[:2] == b"\xff\xd8"
    assert client.get(f"/api/documents/{did}/thumbnail", headers=p2).status_code == 404
    fid = client.get(f"/api/documents/{did}", headers=p1).json()["fields"][0]["id"]
    assert client.post(f"/api/documents/{did}/fields/{fid}/flag", json={"reason": "wrong unit"}, headers=p2).status_code == 404
    assert client.post(f"/api/documents/{did}/fields/{fid}/flag", json={"reason": "wrong unit"}, headers=p1).status_code == 200


def test_care_plan_requires_doctor_and_hides_drafts(client):
    p1, p2, doc, adm = tok(client, "patient1"), tok(client, "patient2"), tok(client, "doctor1"), tok(client, "admin1")
    assert client.get("/api/care-plan", headers=p1).json()["plan"] is None
    body = {"assessment": "Findings reviewed with the patient.", "findings": ["Low ferritin on report"],
            "stages": [{"title": "Review results", "detail": "", "status": "planned"}, {"title": "Follow-up", "detail": "", "status": "planned"}],
            "notes": "Return in 6 weeks.", "recommended_item_ids": []}
    assert client.put("/api/clinic/care-plans/patient1", json=body, headers=p1).status_code == 403
    assert client.put("/api/clinic/care-plans/patient1", json=body, headers=adm).status_code == 403    # admins cannot author clinical plans
    plan = client.put("/api/clinic/care-plans/patient1", json=body, headers=doc).json()
    assert plan["status"] == "draft"
    assert client.get("/api/care-plan", headers=p1).json()["plan"] is None                              # draft is invisible to patient
    bad = dict(body, recommended_item_ids=["not-in-catalogue"])
    assert client.put("/api/clinic/care-plans/patient1", json=bad, headers=doc).status_code == 422
    assert client.post(f"/api/clinic/care-plans/{plan['id']}/approve", headers=p1).status_code == 403
    assert client.post(f"/api/clinic/care-plans/{plan['id']}/approve", headers=adm).status_code == 403
    appr = client.post(f"/api/clinic/care-plans/{plan['id']}/approve", headers=doc).json()
    assert appr["status"] == "approved" and appr["progress"]["percent"] == 0
    mine = client.get("/api/care-plan", headers=p1).json()
    assert mine["plan"]["assessment"].startswith("Findings") and mine["doctor"]["verified"] is False    # never claims verification
    assert client.get("/api/care-plan", headers=p2).json()["plan"] is None                              # isolation
    done = client.post(f"/api/clinic/care-plans/{plan['id']}/stages/0", json={"status": "done"}, headers=doc).json()
    assert done["progress"] == {"done": 1, "total": 2, "percent": 50}
    # verification needs an admin and credentials
    assert client.post("/api/admin/users/doctor1/verify-clinician", json={"credentials": "MD", "verified": True}, headers=doc).status_code == 403
    client.post("/api/admin/users/doctor1/verify-clinician", json={"credentials": "MD Dermatology", "verified": True}, headers=adm)
    assert client.get("/api/care-plan", headers=p1).json()["doctor"] == {"name": "Demo Doctor", "credentials": "MD Dermatology", "verified": True}


def test_history_filters_and_isolation(client):
    p1, p2 = tok(client, "patient1"), tok(client, "patient2")
    all_ = client.get("/api/history", headers=p1).json()
    kinds = {i["kind"] for i in all_}
    assert {"report", "consultation", "treatment"} <= kinds
    assert all(i["kind"] == "treatment" for i in client.get("/api/history?kind=treatments", headers=p1).json())
    assert client.get("/api/history?q=zzzzzz", headers=p1).json() == []
    assert [i["ts"] for i in all_] == sorted((i["ts"] for i in all_), reverse=True)
    assert not any(i["kind"] == "consultation" for i in client.get("/api/history", headers=p2).json())


def test_quote_config_tax_and_catalogue(client):
    doc, p1 = tok(client, "doctor1"), tok(client, "patient1")
    assert client.get("/api/quote-config", headers=p1).json() == {"tax_percent": 0.0, "payment_options": [], "inclusions": [], "terms": "", "validity_days": 30}
    assert client.put("/api/clinic/quote-config", json={"tax_percent": 18}, headers=p1).status_code == 403
    client.put("/api/clinic/quote-config", json={"tax_percent": 18, "payment_options": ["Pay in full"], "inclusions": ["Follow-up visit"]}, headers=doc)
    pid = client.post("/api/clinic/price-items", json={"name": "Peel", "unit_price": 1000, "category": "Skin", "sessions": 4, "duration_min": 30,
                                                      "compare_at_price": 1200}, headers=doc).json()["id"]
    item = next(i for i in client.get("/api/price-items", headers=p1).json() if i["id"] == pid)
    assert item["sessions"] == 4 and item["duration_min"] == 30 and item["category"] == "Skin"
    q = client.post("/api/quotes", json={"items": [{"item_id": pid, "qty": 4}]}, headers=p1).json()
    assert (q["subtotal"], q["tax_amount"], q["total"]) == (4000.0, 720.0, 4720.0)
    assert client.post(f"/api/clinic/quotes/{q['id']}/approve", headers=doc).status_code == 200
    pdf = client.get(f"/api/quotes/{q['id']}/pdf", headers=p1)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    client.put("/api/clinic/quote-config", json={"tax_percent": 0}, headers=doc)


def test_conversations_and_suggestions(client):
    p1 = tok(client, "patient1")
    cid = client.post("/api/conversations", headers=p1).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "What does my hemoglobin say?"}, headers=p1)
    assert any(c["id"] == cid and "hemoglobin" in c["preview"].lower() for c in client.get("/api/conversations", headers=p1).json())
    s = client.get("/api/assistant/suggestions", headers=p1).json()
    assert any("Hemoglobin" in x for x in s) and "What questions should I ask my doctor?" in s


def test_patient_data_deletion_and_backup_restore(client, tmp_path):
    import os
    from pathlib import Path
    from app import config, db as _db
    from scripts import backup as bk
    p2 = tok(client, "patient2")
    client.post("/api/documents", files={"file": ("l.pdf", lab_pdf(["Ferritin     8     ng/mL     15 - 150"]))}, headers=p2)
    cid = client.post("/api/conversations", headers=p2).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "What does my ferritin say?"}, headers=p2)
    stored = [Path(r["stored_path"]) for r in _db.q("SELECT stored_path FROM documents WHERE owner_id='patient2' AND deleted=0")]
    assert stored and all(p.exists() for p in stored)
    # backup -> restore into an empty directory, verified by integrity check and counts
    out = bk.backup(config.DATA_DIR, tmp_path / "bk")
    res = bk.restore(out, tmp_path / "restored")
    assert res["integrity_check"] == "ok" and res["counts"]["documents"] >= 1
    with pytest.raises(SystemExit):
        bk.restore(out, tmp_path / "restored")                       # refuses to overwrite
    (out / "cosmo.sqlite3").write_bytes(b"tampered")
    with pytest.raises(SystemExit):
        bk.restore(out, tmp_path / "other")                          # integrity failure is detected
    # deletion removes files and text, keeps the clinical/financial record and says so
    r = client.post("/api/me/delete-my-data", headers=p2).json()
    assert r["deleted"]["documents"] >= 1 and not any(p.exists() for p in stored)
    assert client.get("/api/documents", headers=p2).json() == []
    assert client.get("/api/conversations", headers=p2).json() == []
    assert _db.q1("SELECT COUNT(*) c FROM doc_pages WHERE document_id IN (SELECT id FROM documents WHERE owner_id='patient2')")["c"] == 0
    assert "retained_by_policy" in r
    assert client.post("/api/me/delete-my-data", headers=tok(client, "doctor1")).status_code == 403


def test_corrections_are_versioned_and_not_training_eligible_by_default(client):
    p1, doc, adm = tok(client, "patient1"), tok(client, "doctor1"), tok(client, "admin1")
    r = client.post("/api/documents", files={"file": ("c.pdf", lab_pdf(["Ferritin     8     ng/mL     15 - 150"]))}, headers=p1).json()
    fid = client.get(f"/api/documents/{r['id']}", headers=p1).json()["fields"][0]["id"]
    assert client.patch(f"/api/clinic/fields/{fid}", json={"value": "9", "review_status": "corrected"}, headers=p1).status_code == 403
    client.patch(f"/api/clinic/fields/{fid}", json={"value": "9", "review_status": "corrected"}, headers=doc)
    s = client.get("/api/admin/corrections/summary", headers=adm).json()
    assert s["by_status"]["corrected"] >= 1 and s["records_where_values_changed"] >= 1 and s["training_eligible"] == 0
    from app import db as _db
    row = _db.q1("SELECT * FROM field_corrections WHERE field_id=?", (fid,))
    assert '"8"' in row["before"] and '"9"' in row["after"] and row["reviewer"] == "doctor1" and row["training_eligible"] == 0
    assert client.post(f"/api/admin/corrections/{row['id']}/eligibility", json={"governance_ref": "GOV-1", "deidentified": False}, headers=adm).status_code == 422
    assert client.post(f"/api/admin/corrections/{row['id']}/eligibility", json={"governance_ref": "x", "deidentified": True}, headers=adm).status_code == 422
    assert client.post(f"/api/admin/corrections/{row['id']}/eligibility", json={"governance_ref": "GOV-2025-014", "deidentified": True}, headers=doc).status_code == 403
    assert client.post(f"/api/admin/corrections/{row['id']}/eligibility", json={"governance_ref": "GOV-2025-014", "deidentified": True}, headers=adm).status_code == 200


def test_followup_uses_previous_question_and_reindex_only_embeds_changes(client):
    p1 = tok(client, "patient1")
    client.post("/api/documents", files={"file": ("f.pdf", lab_pdf(["Ferritin     8     ng/mL     15 - 150  L"]))}, headers=p1)
    cid = client.post("/api/conversations", headers=p1).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "What does my ferritin result say?"}, headers=p1)
    out = client.post(f"/api/conversations/{cid}/messages", json={"text": "and its range?"}, headers=p1).json()
    assert not out["abstained"] and any("Ferritin" in (s.get("excerpt") or "") for s in out["sources"])
    from app.assistant.retrieval import HybridRetriever, SentenceTransformerEmbedder
    r = HybridRetriever(SentenceTransformerEmbedder(), 1.0)
    pool = [{"text": "Ferritin  8  ng/mL  15 - 150  L"}, {"text": "TSH  2.1  mIU/L  0.4 - 4.0"}]
    r.search("ferritin", pool)
    assert r.embedded_count == 2
    r.search("tsh", pool)
    assert r.embedded_count == 2                                         # nothing re-embedded
    r.search("ferritin", pool + [{"text": "Hemoglobin  10.2  g/dL  12.0 - 15.5"}])
    assert r.embedded_count == 3                                         # only the new passage
    r.search("ferritin", [{"text": "Ferritin  9  ng/mL  15 - 150  L"}, pool[1]])
    assert r.embedded_count == 4                                         # a changed passage is re-embedded


def test_clinician_can_see_photos_cases_and_review_them(client):
    p1, p2, doc, adm = tok(client, "patient1"), tok(client, "patient2"), tok(client, "doctor1"), tok(client, "admin1")
    img = np.random.default_rng(9).integers(60, 200, (500, 500, 3), dtype=np.uint8)
    r = client.post("/api/images", data={"region": "body"}, files={"file": ("b.jpg", cv2.imencode(".jpg", img)[1].tobytes())}, headers=p1).json()
    iid = r["id"]
    saved = r["report"]["verdict"] != "rejected"
    sub = client.get("/api/clinic/submissions", headers=doc).json()
    mine = next(i for i in sub["images"] if i["id"] == iid)
    assert mine["patient"] == "Demo Patient One" and "checks" in mine["report"] and mine["has_file"] == saved
    assert client.get("/api/clinic/submissions", headers=p1).status_code == 403
    if saved:
        f = client.get(f"/api/images/{iid}/file", headers=doc)
        assert f.status_code == 200 and f.content[:2] == b"\xff\xd8"
        assert client.get(f"/api/images/{iid}/file", headers=p2).status_code == 404
        assert client.post(f"/api/clinic/images/{iid}/review", headers=adm).status_code == 403        # only doctors review clinically
        assert client.post(f"/api/clinic/images/{iid}/review", headers=doc).status_code == 200
        h = [i for i in client.get("/api/history?kind=photos", headers=p1).json() if i["id"] == iid][0]
        assert h["status"] == "reviewed" and "reviewed by a clinician" in h["subtitle"]
    case = client.get("/api/clinic/cases/patient1", headers=doc).json()
    assert case["patient"]["id"] == "patient1" and case["photos"] and case["documents"] is not None
    assert client.get("/api/clinic/cases/patient1", headers=p1).status_code == 403
    assert client.get("/api/clinic/cases/doctor1", headers=doc).status_code == 404                    # only patients have cases
    from app import db as _db
    acts = {a["action"] for a in _db.q("SELECT action FROM audit")}
    assert {"case.opened", "image.staff_access"} <= acts or not saved

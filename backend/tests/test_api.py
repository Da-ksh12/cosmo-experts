from pathlib import Path
import io
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def tok(client, user):
    r = client.post("/api/auth/login", json={"user_id": user, "password": "cosmo-dev"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def lab_pdf(lines, date="Report Date: 12/03/2024", encrypt=False):
    buf = io.BytesIO()
    kw = {"encrypt": "pw"} if encrypt else {}
    c = canvas.Canvas(buf, pagesize=A4, **kw)
    y = 800
    c.drawString(50, y, "PATHOLOGY LABORATORY - Laboratory Report"); y -= 20
    c.drawString(50, y, date); y -= 20
    c.drawString(50, y, "Test            Result   Unit      Reference range"); y -= 20
    for ln in lines:
        c.drawString(50, y, ln); y -= 18
    c.save()
    return buf.getvalue()


def test_auth_required(client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer x.y"}).status_code == 401


def test_role_enforcement(client):
    assert client.get("/api/admin/audit", headers=tok(client, "patient1")).status_code == 403
    assert client.get("/api/clinic/submissions", headers=tok(client, "patient1")).status_code == 403
    assert client.post("/api/documents", files={"file": ("a.txt", b"hi")}, headers=tok(client, "doctor1")).status_code == 403


def test_document_pipeline_and_isolation(client):
    p1, p2, doc = tok(client, "patient1"), tok(client, "patient2"), tok(client, "doctor1")
    pdf = lab_pdf(["Hemoglobin     10.2     g/dL      12.0 - 15.5",
                   "TSH            2.1      mIU/L     0.4 - 4.0",
                   "Ferritin       8        ng/mL     15 - 150  L"])
    r = client.post("/api/documents", files={"file": ("lab.pdf", pdf, "application/pdf")}, headers=p1)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["doc_type"] == "laboratory_report" and j["fields"] == 3 and j["status"] == "clinician_review_required"
    did = j["id"]
    full = client.get(f"/api/documents/{did}", headers=p1).json()
    by = {f["name"]: f for f in full["fields"]}
    assert by["Hemoglobin"]["value"] == "10.2" and by["Hemoglobin"]["unit"] == "g/dL"
    assert by["Hemoglobin"]["flag"] == "outside_printed_range"
    assert by["TSH"]["flag"] == "within_printed_range"
    assert by["Ferritin"]["flag"] == "printed_flag_low"
    assert all(f["source_page"] == 1 and f["source_text"] for f in full["fields"])
    assert full["document"]["doc_date"] == "2024-03-12"
    # isolation
    assert client.get(f"/api/documents/{did}", headers=p2).status_code == 404
    assert client.get(f"/api/documents/{did}/file", headers=p2).status_code == 404
    assert client.get(f"/api/documents/{did}", headers=doc).status_code == 200
    assert client.get("/api/documents", headers=p2).json() == []
    # explanation: fixed phrasing, source cited, no diagnosis
    ex = client.get(f"/api/documents/{did}/explanation?lang=en", headers=p1).json()
    txt = ex["spoken_text"].lower()
    assert "diagnos" not in txt.replace("not a diagnosis", "")
    assert ex["status"] == "clinician_review_required"
    assert ex["sections"][0]["items"][0]["source"]["page"] == 1
    for lang in ("hi", "hinglish"):
        assert client.get(f"/api/documents/{did}/explanation?lang={lang}", headers=p1).json()["language"] == lang


def test_bad_documents(client):
    p1 = tok(client, "patient1")
    assert client.post("/api/documents", files={"file": ("x.pdf", b"%PDF-1.4 garbage")}, headers=p1).status_code == 422
    assert client.post("/api/documents", files={"file": ("x.exe", b"MZ....")}, headers=p1).status_code == 422
    assert client.post("/api/documents", files={"file": ("x.pdf", b"")}, headers=p1).status_code == 422
    r = client.post("/api/documents", files={"file": ("enc.pdf", lab_pdf(["A 1 mg 1 - 2"], encrypt=True))}, headers=p1)
    assert r.status_code == 422 and "password" in r.json()["detail"].lower()
    r = client.post("/api/documents", files={"file": ("fake.png", b"%PDF-1.4")}, headers=p1)
    assert r.status_code == 422


def test_unreadable_scan_is_reported_not_invented(client):
    p1 = tok(client, "patient1")
    img = np.full((400, 400, 3), 255, np.uint8)
    cv2.putText(img, "scan", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 3)
    png = cv2.imencode(".png", img)[1].tobytes()
    r = client.post("/api/documents", files={"file": ("scan.png", png)}, headers=p1)
    assert r.status_code == 200
    j = r.json()
    assert j["fields"] == 0 and j["doc_type"] == "other_uncertain"


def test_compare_requires_matching_units(client):
    p2 = tok(client, "patient2")
    for date, line in (("01/01/2023", "Vitamin D     20     ng/mL     30 - 100"), ("01/06/2024", "Vitamin D     60     nmol/L    75 - 250")):
        assert client.post("/api/documents", files={"file": ("v.pdf", lab_pdf([line], f"Report Date: {date}"))}, headers=p2).status_code == 200
    cmp_ = client.get("/api/documents/compare", headers=p2).json()
    vit = next(c for c in cmp_ if c["name"].lower().startswith("vitamin"))
    assert vit["comparable"] is False and vit["conversion_performed"] is False and "observed_change" not in vit
    # same unit -> comparable with observed change but no clinical claim
    p1 = tok(client, "patient1")
    for date, v in (("01/01/2023", "9.0"), ("01/06/2024", "11.0")):
        client.post("/api/documents", files={"file": ("h.pdf", lab_pdf([f"Hemoglobin     {v}     g/dL     12.0 - 15.5"], f"Report Date: {date}"))}, headers=p1)
    hb = next(c for c in client.get("/api/documents/compare", headers=p1).json() if c["name"].lower() == "hemoglobin")
    assert hb["comparable"] and hb["observed_change"]["direction"] == "higher" and "interpret" in hb["note"]


def test_assistant_grounding_refusal_abstention(client):
    p1, p2, doc = tok(client, "patient1"), tok(client, "patient2"), tok(client, "doctor1")
    cid = client.post("/api/conversations", headers=p1).json()["id"]
    a = client.post(f"/api/conversations/{cid}/messages", json={"text": "What does my ferritin result say?"}, headers=p1).json()
    assert not a["abstained"] and a["sources"][0]["kind"] == "document" and a["sources"][0]["page"] == 1
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "Which medicine should I take and what dose?"}, headers=p1).json()
    assert r["abstained"] and r["reason"] == "clinical_decision" and r["sources"] == []
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "Do I have anemia? Diagnose me"}, headers=p1).json()
    assert r["reason"] == "clinical_decision"
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "I have chest pain"}, headers=p1).json()
    assert r["reason"] == "urgent"
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "Who won the cricket world cup in nineteen eighty three"}, headers=p1).json()
    assert r["abstained"]
    # another patient cannot read this conversation or see this patient's data through the assistant
    assert client.get(f"/api/conversations/{cid}", headers=p2).status_code == 404
    cid2 = client.post("/api/conversations", headers=p2).json()["id"]
    leak = client.post(f"/api/conversations/{cid2}/messages", json={"text": "What is my ferritin value?"}, headers=p2).json()
    assert "Ferritin" not in leak["answer"]
    # unapproved KB is not used; approved KB is cited
    q = "What is a reference range?"
    before = client.post(f"/api/conversations/{cid2}/messages", json={"text": q}, headers=p2).json()
    assert not any(s["kind"] == "knowledge_base" for s in before["sources"])
    art = next(a for a in client.get("/api/clinic/kb", headers=doc).json() if a["title"] == q)
    assert client.post(f"/api/clinic/kb/{art['id']}/approve", headers=p2).status_code == 403
    assert client.post(f"/api/clinic/kb/{art['id']}/approve", headers=doc).status_code == 422          # no attestation, no approval
    assert client.post(f"/api/clinic/kb/{art['id']}/approve", json=ATTEST, headers=doc).status_code == 200
    after = client.post(f"/api/conversations/{cid2}/messages", json={"text": q}, headers=p2).json()
    assert any(s["kind"] == "knowledge_base" for s in after["sources"])
    # deletion
    assert client.delete(f"/api/conversations/{cid}", headers=p1).status_code == 200
    assert client.get(f"/api/conversations/{cid}", headers=p1).status_code == 404


def test_quotation_flow(client):
    p1, doc = tok(client, "patient1"), tok(client, "doctor1")
    assert client.post("/api/quotes", json={"items": [{"item_id": "invented", "qty": 1}]}, headers=p1).status_code == 422
    pid = client.post("/api/clinic/price-items", json={"name": "Consultation", "unit_price": 1000, "currency": "INR"}, headers=doc).json()["id"]
    assert client.post("/api/clinic/price-items", json={"name": "x", "unit_price": 1}, headers=p1).status_code == 403
    q = client.post("/api/quotes", json={"items": [{"item_id": pid, "qty": 2}]}, headers=p1).json()
    qid = q["id"]
    assert q["status"] == "draft"
    ask = client.post("/api/conversations", headers=p1).json()["id"]
    r = client.post(f"/api/conversations/{ask}/messages", json={"text": "How much is my quotation?"}, headers=p1).json()
    assert "approved quotation" in r["answer"].lower() and "yet" in r["answer"].lower()   # draft is not disclosed as final
    assert client.post(f"/api/clinic/quotes/{qid}/approve", headers=p1).status_code == 403
    assert client.post(f"/api/clinic/quotes/{qid}/approve", headers=doc).json()["status"] == "approved"
    r = client.post(f"/api/conversations/{ask}/messages", json={"text": "How much is my quotation?"}, headers=p1).json()
    assert "2000.00" in r["answer"]
    pdf = client.get(f"/api/quotes/{qid}/pdf", headers=p1)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get(f"/api/quotes/{qid}/pdf", headers=tok(client, "patient2")).status_code == 404


ATTEST = {"attest": True, "sources_checked": "Test fixture: wording checked against the laboratory report layout."}


def _img(h=500, w=500):
    rng = np.random.default_rng(3)
    img = rng.integers(60, 200, (h, w, 3), dtype=np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_image_upload_and_tasks(client):
    p1, doc = tok(client, "patient1"), tok(client, "doctor1")
    r = client.post("/api/images", data={"region": "face"}, files={"file": ("f.jpg", _img())}, headers=p1)
    assert r.status_code == 200 and r.json()["report"]["verdict"] == "rejected"
    assert "face" in r.json()["report"]["message"].lower()
    assert client.post("/api/images", data={"region": "leg"}, files={"file": ("f.jpg", _img())}, headers=p1).status_code == 400
    ts = {t["id"]: t for t in client.get("/api/tasks", headers=doc).json()}
    assert ts["image_suitability"]["status"]["status"] == "available"
    assert ts["acne_features"]["status"]["status"] == "unavailable"
    assert not any(t["status"]["status"] == "approved_for_defined_use" for t in ts.values())
    from app.training import tasks
    with pytest.raises(NotImplementedError):
        tasks.predict("acne_features", b"")


def test_status_cannot_be_faked():
    from app import status as st
    assert st.effective_status("approved_for_defined_use", None) == "research_only"
    ap = st.Approval("dr", "use", {r: True for r in st.RELEASE_REQUIREMENTS[:-1]})
    assert st.effective_status("approved_for_defined_use", ap) == "research_only"
    ap.requirements_met[st.RELEASE_REQUIREMENTS[-1]] = True
    assert st.effective_status("approved_for_defined_use", ap) == "approved_for_defined_use"


def test_dataset_license_gate_and_compat(client):
    a = tok(client, "admin1")
    ds = {d["id"]: d for d in client.get("/api/admin/datasets", headers=a).json()}
    assert not ds["ham10000"]["license_gate"]["allowed"]
    assert all(not d["license_gate"]["allowed"] for d in ds.values())     # nothing usable until verified
    r = client.post("/api/admin/datasets/ham10000/approve", json={"approved": True, "commercial": True}, headers=a)
    assert r.status_code == 409
    r = client.post("/api/admin/datasets/ham10000/verify", json={"verified_spdx": "CC-BY-NC-4.0"}, headers=a)
    assert r.status_code == 422                                           # no evidence snapshot recorded
    comp = client.get("/api/admin/datasets/ham10000/compatibility", headers=a).json()
    acne = next(c for c in comp if c["task"] == "acne_features")
    assert not acne["compatible"] and any("Dermoscopic" in x or "dermoscop" in x.lower() for x in acne["reasons_incompatible"])
    assert client.get("/api/admin/datasets", headers=tok(client, "doctor1")).status_code == 403


def test_import_split_train_approval_rollback(client, tmp_path):
    a = tok(client, "admin1")
    root = tmp_path / "imgs"
    root.mkdir()
    rng = np.random.default_rng(0)
    rows = ["filename,label,group_id,fitzpatrick,source"]
    for i in range(60):
        cls = "a" if i % 2 == 0 else "b"
        img = np.zeros((96, 96, 3), np.uint8)
        img[:] = (30, 30, 200) if cls == "a" else (200, 60, 30)
        img = np.clip(img + rng.integers(0, 40, img.shape), 0, 255).astype(np.uint8)
        cv2.circle(img, (int(rng.integers(20, 76)), int(rng.integers(20, 76))), int(rng.integers(5, 15)), (255, 255, 255), -1)
        cv2.imwrite(str(root / f"{i}.png"), img)
        rows.append(f"{i}.png,{cls},g{i // 2},{(i % 3) + 1},synthetic")
    cv2.imwrite(str(root / "dup.png"), cv2.imread(str(root / "0.png")))   # exact duplicate
    (root / "broken.jpg").write_bytes(b"not an image")
    (tmp_path / "labels.csv").write_text("\n".join(rows))

    # blocked until licence verified
    body = {"folder": str(root), "labels_csv": str(tmp_path / "labels.csv"), "commercial": False}
    assert client.post("/api/admin/datasets/pad-ufes-20/import-folder", json=body, headers=a).status_code == 409
    # admin records written permission (evidence) -> verify -> approve -> gate opens for evaluation use
    client.post("/api/admin/datasets/pad-ufes-20/verify", json={"verified_spdx": "CC-BY-4.0", "permission_document": "LICENSE-RECORD-001"}, headers=a)
    r = client.post("/api/admin/datasets/pad-ufes-20/approve", json={"approved": True, "commercial": True}, headers=a)
    assert r.status_code == 200, r.text
    imp = client.post("/api/admin/datasets/pad-ufes-20/import-folder", json=body, headers=a).json()
    assert imp["count"] == 60 and imp["duplicates_skipped"] == 1 and len(imp["errors"]) == 1

    from app.datasets.importer import make_splits
    from app import db
    import json
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (imp["version_id"],))["manifest"])
    sp = make_splits(mf["entries"])
    g = {k: {e["group_id"] for e in v} for k, v in sp.items()}
    assert not (g["train"] & g["test"]) and not (g["train"] & g["val"]) and not (g["val"] & g["test"])

    # incompatible task rejected; compatible research task trains
    r = client.post("/api/admin/train", json={"task_id": "acne_features", "dataset_id": "pad-ufes-20", "version_id": imp["version_id"]}, headers=a)
    assert r.status_code == 409
    trn = {"task_id": "lesion_classification_research", "dataset_id": "pad-ufes-20", "version_id": imp["version_id"], "epochs": 2}
    r = client.post("/api/admin/train", json=trn, headers=a)
    assert r.status_code == 200, r.text
    job = r.json()["job_id"]
    for _ in range(300):
        st = client.get(f"/api/admin/jobs/{job}", headers=a).json()
        if st["status"] != "running":
            break
        time.sleep(1)
    assert st["status"] == "done", st
    mid = st["model_id"]
    m = client.get("/api/admin/models", headers=a).json()[0]
    assert m["status"] == "research_only" and m["test_metrics"]["n"] > 0 and m["test_metrics"]["confusion_matrix"]
    assert m["pretrained_weights"].startswith("none") and m["code_revision"].startswith("src-") and m["baselines"]["majority_class"]
    # real inference through the registry/serving path (admin only), integrity-checked
    from app.training import serving
    test_img = (root / f"{1}.png")
    png = test_img.read_bytes()
    assert serving.predict(mid, png, "rid-small")["status"] == "rejected_input"                            # 96 px synthetic image is too small: refused, not guessed
    big = cv2.imencode(".jpg", np.random.default_rng(4).integers(40, 210, (420, 420, 3), dtype=np.uint8))[1].tobytes()
    out = serving.predict(mid, big, "rid-serve")
    assert out["status"] in ("predicted", "abstained"), out                                              # the REAL prediction path ran
    assert out["research_only"] is True and abs(sum(out["all_probabilities"].values()) - 1) < 1e-2 and set(out["all_probabilities"]) == {"a", "b"}
    assert out["model"]["code_revision"].startswith("src-") and len(out["top"]) == 2 and out["warnings"]
    assert serving.predict(mid, big)["all_probabilities"] == out["all_probabilities"]                    # deterministic
    assert serving.predict(mid, b"not an image")["status"] == "rejected_input"
    r_api = client.post("/api/v1/research/predict", data={"model_id": mid}, files={"file": ("x.png", png)}, headers=tok(client, "patient1"))
    assert r_api.status_code == 403                                                                           # never patient-facing
    r_api = client.post("/api/v1/research/predict", data={"model_id": mid}, files={"file": ("x.jpg", big)}, headers=a)
    assert r_api.status_code == 202
    for _ in range(120):
        jj = client.get(f"/api/v1/jobs/{r_api.json()['id']}", headers=a).json()
        if jj["status"] in ("completed", "uncertain", "rejected", "failed"):
            break
        time.sleep(0.5)
    assert jj["status"] in ("completed", "uncertain") and jj["result"]["research_only"] is True and jj["result"]["status"] != "rejected_input"
    # tamper with the artifact: loading must be refused
    from pathlib import Path as _P
    ap_ = _P(m["artifact_path"])
    good = ap_.read_bytes()
    serving._cache.clear()
    ap_.write_bytes(good + b"x")
    with pytest.raises(serving.ServingError):
        serving.predict(mid, png)
    ap_.write_bytes(good)
    serving._cache.clear()
    # linear-probe path (real DINOv2 weights if present): train on the same synthetic version, then SERVE it through the same route
    dino = Path(__file__).resolve().parent.parent / "data" / "models" / "pretrained" / "dinov2_small" / "model.safetensors"
    if dino.is_file():
        from app.training import probe
        pcard = probe.train_probe("lesion_classification_research", "pad-ufes-20", imp["version_id"], "admin1", str(dino), {"source": "test"})
        pid = pcard["model_id"]
        assert pcard["test_metrics"]["n"] > 0 and pcard["artifact_path"].endswith(".npz") and "majority_class" in pcard["baselines"]
        po = serving.predict(pid, big, "rid-probe")
        assert po["status"] in ("predicted", "abstained") and set(po["all_probabilities"]) == {"a", "b"} and abs(sum(po["all_probabilities"].values()) - 1) < 1e-2
        assert serving.predict(pid, big)["all_probabilities"] == po["all_probabilities"]
        from pathlib import Path as _P2
        ap2 = _P2(pcard["artifact_path"]); g2 = ap2.read_bytes(); serving._cache.clear(); ap2.write_bytes(g2 + b"x")
        with pytest.raises(serving.ServingError):
            serving.predict(pid, big)                                    # tampered probe head refused
        ap2.write_bytes(g2); serving._cache.clear()
    # cannot deploy without full approval; incomplete approval remains research_only
    assert client.post(f"/api/admin/models/{mid}/deploy", headers=a).status_code == 409
    ap = client.post(f"/api/admin/models/{mid}/approval", json={"intended_use": "x", "requirements": {"held_out_evaluation_passed": True}}, headers=a).json()
    assert ap["effective_status"] == "research_only" and "clinical_signoff_recorded" in ap["missing_requirements"]
    assert client.post(f"/api/admin/models/{mid}/deploy", headers=a).status_code == 409
    from app import status as st_
    full = {r_: True for r_ in st_.RELEASE_REQUIREMENTS}
    ap = client.post(f"/api/admin/models/{mid}/approval", json={"intended_use": "Research demo", "requirements": full}, headers=a).json()
    assert ap["effective_status"] == "research_only" and "min_test_images" in ap["failed_evaluation_gates"]   # tiny synthetic test set cannot pass the gates
    # a card that genuinely satisfies the evaluation gates can be approved, deployed and rolled back
    import json as _j
    from app import db as _db
    card = _j.loads(_db.q1("SELECT data FROM model_versions WHERE id=?", (mid,))["data"])
    card.update(model_id="gatepass", task="gate_test_task_without_critical_classes", near_duplicates_vs_train={"val": 0, "test": 0}, dataset_license={"spdx": "CC-BY-4.0", "terms_snapshot_hash": "x"})
    card["test_metrics"].update(n=500, macro_f1=0.9, macro_f1_ci95=[0.85, 0.93], ece=0.05)
    card["baselines"] = {"majority_class": {"macro_f1": 0.1}}
    _db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", ("gatepass", card["task"], _j.dumps(card), "research_only", _db.now()))
    # a self-declared "clinical sign-off" is not enough: a verified clinician has to be named
    ap = client.post("/api/admin/models/gatepass/approval", json={"intended_use": "Research demo", "requirements": full}, headers=a).json()
    assert ap["effective_status"] == "research_only" and "verified clinician" in " ".join(ap["refused_because"])
    was_verified = _db.q1("SELECT verified FROM users WHERE id='doctor1'")["verified"]
    _db.x("UPDATE users SET verified=1 WHERE id='doctor1'")
    ap = client.post("/api/admin/models/gatepass/approval", json={"intended_use": "Research demo", "requirements": full, "clinical_signoff_by": "doctor1"}, headers=a).json()
    _db.x("UPDATE users SET verified=? WHERE id='doctor1'", (was_verified,))
    assert ap["effective_status"] == "approved_for_defined_use"
    assert client.post("/api/admin/models/gatepass/deploy", headers=a).json()["active"] == "gatepass"
    assert client.post("/api/admin/tasks/gate_test_task_without_critical_classes/rollback", headers=a).json()["active"] is None
    actions = {x["action"] for x in client.get("/api/admin/audit", headers=a).json()}
    assert {"dataset.import", "model.train", "model.deploy", "model.rollback", "dataset.import_blocked"} <= actions


def test_speech_status_and_fallback(client):
    p1 = tok(client, "patient1")
    s = client.get("/api/speech/status", headers=p1).json()
    assert set(s["stt"]) == {"available"}            # config details hidden from patients
    # with a working local model, undecodable audio is a clear 422 (never a 500); without one, a clear 503
    r = client.post("/api/speech/transcribe", files={"file": ("a.webm", b"x")}, headers=p1)
    assert r.status_code in (503, 422) and "detail" in r.json()


def test_delete_document_removes_data(client):
    p2 = tok(client, "patient2")
    did = client.get("/api/documents", headers=p2).json()[0]["id"]
    assert client.delete(f"/api/documents/{did}", headers=p2).status_code == 200
    assert client.get(f"/api/documents/{did}", headers=p2).status_code == 404

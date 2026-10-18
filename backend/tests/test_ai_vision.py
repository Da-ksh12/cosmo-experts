"""Image quality v2 and category routing. Synthetic photographs test the CODE paths; the measured behaviour on real photographs is in docs/eval/quality_v2.json."""
import io
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db
from app.ai import registry, vision
from app.ai.engine import HandlerResult, get_engine, reset_engine
from app.imaging import quality, suitability
from app.main import app

SKIN_BGR = (120, 150, 205)               # a skin-like colour in BGR


def skin_photo(w=640, h=640, seed=1, noise=20) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), SKIN_BGR, np.float32)
    img += rng.normal(0, noise, (h, w, 3))
    yy, xx = np.mgrid[:h, :w]
    img += (np.sin(xx / 23.0) * np.cos(yy / 31.0) * 12)[..., None]
    cv2.circle(img, (w // 2, h // 2), h // 9, (80, 90, 150), -1)                # a darker "lesion" in the middle
    return np.clip(img, 0, 255).astype(np.uint8)


def png(img) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


def jpg(img, q=90) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()


@pytest.fixture(scope="module", autouse=True)
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    registry.invalidate()
    monkeypatch.setattr(quality, "_calibration", lambda: {})                       # pinned: tests do not depend on a local evaluation file
    yield
    reset_engine()
    with db.context.as_system():
        db.x("DELETE FROM model_versions WHERE id LIKE 'aivis-%'")
        db.x("DELETE FROM settings WHERE key LIKE 'active_model:%'")


def tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


def post(c, who, img_bytes, **data):
    return c.post("/api/v1/ai/images/analyze", data=data, files={"file": ("p.png", img_bytes)}, headers=tok(c, who))


# ---------------------------------------------------------------- quality measurements
def test_jpeg_quality_estimate_tracks_the_true_quality_and_png_has_none():
    img = skin_photo(seed=3)
    for q in (95, 85, 75, 60, 40, 20):
        est = quality.estimate_jpeg_quality(jpg(img, q))
        assert est is not None and abs(est - q) <= 4, (q, est)
    assert quality.estimate_jpeg_quality(png(img)) is None and quality.estimate_jpeg_quality(b"not an image") is None


def test_obstruction_fraction_rises_with_a_covered_frame_edge_and_stays_low_for_a_clean_photo():
    clean = skin_photo(seed=4)
    assert quality.obstruction_fraction(clean) < 0.05
    covered = clean.copy()
    covered[:300, :300] = 0                                                        # a lens cover in the corner (~22 % of the frame)
    assert quality.obstruction_fraction(covered) >= 0.15
    glare = clean.copy()
    cv2.ellipse(glare, (640, 320), (200, 300), 0, 0, 360, (255, 255, 255), -1)
    assert quality.obstruction_fraction(glare) >= 0.12


def test_centre_skin_measurement_exists_for_the_evaluation_but_is_not_a_shipped_check():
    img = skin_photo(seed=5)
    assert quality.center_skin_fraction(img) > 0.5
    assert "framing" not in {c["id"] for c in quality.assess(png(img), "skin_lesion").checks if c.get("source") is None}      # only the legacy framing entry exists for close-ups


def test_face_framing_flags_cut_off_and_off_centre_faces():
    img = np.zeros((400, 400, 3), np.uint8)
    assert quality.face_framing(img, [(100, 100, 200, 200)]) == {"cut_off": False, "offset": 0.0}
    assert quality.face_framing(img, [(0, 100, 200, 200)])["cut_off"] is True
    assert quality.face_framing(img, [(10, 10, 100, 100), (200, 200, 100, 100)]) == {"cut_off": False, "offset": None}      # more than one face: not this check's job


# ---------------------------------------------------------------- assessment rules
def test_v2_never_accepts_what_the_legacy_gate_rejects_and_keeps_its_reason():
    cases = {"blurry": cv2.GaussianBlur(skin_photo(seed=6), (0, 0), 9), "too_dark": (skin_photo(seed=7) * 0.25).astype(np.uint8), "too_small": skin_photo(200, 200, seed=8)}
    for reason, img in cases.items():
        legacy = suitability.assess(png(img), "body")
        v2 = quality.assess(png(img), "body")
        assert legacy.verdict == "rejected" and v2.verdict == "not_usable" and v2.reason == legacy.reason, (reason, legacy.reason, v2.reason)
    v2 = quality.assess(b"not an image", "body")
    assert v2.verdict == "not_usable" and v2.reason == "invalid_image"


def test_a_good_photo_is_usable_and_lists_every_check_with_its_basis():
    qa = quality.assess(png(skin_photo(seed=9)), "body")
    assert qa.verdict in ("usable", "usable_with_warnings") and qa.reason == "ok"
    ids = {c["id"] for c in qa.checks}
    assert {"resolution", "compression", "obstruction", "document"} <= ids and {"format", "quality", "lighting", "subject", "framing", "overall"} <= ids
    for c in qa.checks:
        assert set(c) >= {"id", "label", "status", "validated"} and c["status"] in ("pass", "warn", "fail", "skipped", "passed", "failed", "uncertain")
    assert qa.to_dict()["version"] == quality.VERSION


def test_an_unvalidated_check_can_only_warn_and_a_validated_one_can_reject(monkeypatch):
    covered = skin_photo(seed=10)
    covered[:340, :340] = 0                                                         # large dark corner
    qa = quality.assess(png(covered), "body")
    obs = next(c for c in qa.checks if c["id"] == "obstruction")
    assert obs["status"] == "warn" and obs["validated"] is False and qa.verdict != "not_usable" and any("covered" in w for w in qa.warnings)
    monkeypatch.setattr(quality, "_calibration", lambda: {"obstruction": {"threshold": 0.12, "validated": True, "basis": "fixture"}})
    qa = quality.assess(png(covered), "skin_lesion")                                # the validation covers lesion close-ups
    assert qa.verdict == "not_usable" and qa.reason == "obstruction" and next(c for c in qa.checks if c["id"] == "obstruction")["status"] == "fail"
    for other in ("body", "other", "face", "hair_scalp"):                           # ... and is never applied as a rejection to any other kind of photo
        qo = quality.assess(png(covered), other)
        assert next(c for c in qo.checks if c["id"] == "obstruction")["status"] != "fail", other


def test_categories_use_their_own_profiles_and_a_report_photo_has_its_own_assessment():
    assert quality.PROFILE_FOR["face"] == "default" and quality.PROFILE_FOR["hair_scalp"] == "hair_closeup" and quality.PROFILE_FOR["skin_lesion"] == "skin_closeup"
    page = np.full((900, 700, 3), 245, np.uint8)
    qa = quality.assess(png(page), "medical_report_image")
    assert qa.category == "medical_report_image" and qa.profile == "document" and {c["id"] for c in qa.checks} == {"legibility", "resolution", "focus"}
    with pytest.raises(ValueError):
        quality.assess(png(page), "selfie")


# ---------------------------------------------------------------- routing
def test_categories_endpoint_hides_research_from_patients_and_shows_unavailability_honestly(client):
    p = {c["id"]: c for c in client.get("/api/v1/ai/images/categories", headers=tok(client, "patient1")).json()}
    assert "skin_lesion" not in p and set(p) == {"face", "hair_scalp", "body", "other", "medical_report_image"}
    assert all(t["available"] is False for c in p.values() for t in c["tasks"]) and {t["task"] for t in p["face"]["tasks"]} >= {"acne_features", "pigmentation"}
    a = {c["id"]: c for c in client.get("/api/v1/ai/images/categories", headers=tok(client, "admin1")).json()}
    assert "skin_lesion" in a and a["skin_lesion"]["tasks"][0]["task"] == "lesion_classification_research"


def test_a_usable_photo_with_no_approved_model_routes_to_clinician_review_and_runs_no_model(client, monkeypatch):
    ran = []
    eng = get_engine()
    real = eng.run
    monkeypatch.setattr(eng, "run", lambda cid, *a, **k: (ran.append(cid), real(cid, *a, **k))[1])
    r = post(client, "patient1", png(skin_photo(seed=11)), category="body")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["route"] == "clinician_review" and j["outcomes"] == [] and j["clinician_review"] is True and "nothing is diagnosed" in j["message"]
    assert ran == ["image.quality"]                                                     # only the quality check ran: no model was invoked
    assert j["quality"]["verdict"] in ("usable", "usable_with_warnings") and j["candidates"][0]["task"] == "dryness_scaling" and not j["candidates"][0]["runnable_by_caller"]
    assert "not a diagnosis" in " ".join(j["disclaimers"]).lower()


def test_an_unrelated_task_is_refused_for_the_category(client):
    for cat, task in (("hair_scalp", "acne_features"), ("face", "hair_thinning"), ("body", "lesion_classification_research"), ("face", "lesion_classification_research")):
        r = post(client, "admin1", png(skin_photo(seed=12)), category=cat, task=task, research="true")
        assert r.status_code == 422 and "does not apply" in r.json()["detail"], (cat, task)
    assert post(client, "patient1", png(skin_photo(seed=12)), category="selfie").status_code == 422


def test_unusable_photos_are_rejected_before_any_model_is_considered(client):
    r = post(client, "patient1", png(skin_photo(120, 120, seed=13)), category="body").json()
    assert r["route"] == "rejected" and r["candidates"] == [] and r["outcomes"] == [] and "too small" in r["message"].lower()
    bad = post(client, "patient1", b"not an image", category="body").json()
    assert bad["route"] == "rejected" and "open this file" in bad["message"]


def test_research_models_run_only_for_an_administrator_who_names_the_task_and_the_model(client):
    mid = "aivis-research-1"
    card = {"model_id": mid, "task": "lesion_classification_research", "architecture": "fixture", "artifact_sha256": "cd" * 32, "test_metrics": {}, "baselines": {}, "dataset_license": {}}
    with db.context.as_system():
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (mid, "lesion_classification_research", json.dumps(card), "research_only", db.now()))
    eng = get_engine()
    calls = []

    def fake_handler(payload, ctx):
        calls.append(payload["model_id"])
        return HandlerResult("ok", {"top": [{"label": "NEV", "probability": 0.6}], "confidence": 0.6, "status": "predicted"}, model_derived=True)
    eng.register("image.lesion_classification_research", fake_handler)
    img = png(skin_photo(seed=14))
    # patient: category not even offered; doctor: not an audience; admin without the research flag: refused; wrong category: refused
    assert post(client, "patient1", img, category="skin_lesion").json()["outcomes"] == []
    assert post(client, "doctor1", img, category="skin_lesion", task="lesion_classification_research", model_id=mid).json()["outcomes"] == []
    assert post(client, "doctor1", img, category="skin_lesion", research="true").status_code == 403
    j = post(client, "admin1", img, category="skin_lesion", task="lesion_classification_research", model_id=mid).json()
    assert j["route"] == "clinician_review" and j["outcomes"] == [] and calls == []
    j = post(client, "admin1", img, category="skin_lesion", research="true").json()                                   # research without naming the task: nothing runs automatically
    assert j["outcomes"] == [] and calls == []
    j = post(client, "admin1", img, category="skin_lesion", task="lesion_classification_research", model_id=mid, research="true").json()
    assert j["route"] == "image_models" and calls == [mid]
    o = j["outcomes"][0]
    assert o["status"] == "ok" and "research_only" in o["labels"] and o["result"]["research_only"] is True and "Research output only" in " ".join(j["disclaimers"])
    assert "diagnos" not in json.dumps(o["result"]).replace("Not a diagnosis", "").lower().replace("not a diagnosis", "")


def test_an_approved_active_model_is_the_only_kind_that_runs_automatically(client):
    approved, research = "aivis-approved-1", "aivis-research-2"
    card = lambda mid: {"model_id": mid, "task": "dryness_scaling", "architecture": "fixture", "artifact_sha256": "ef" * 32, "test_metrics": {}, "baselines": {}, "dataset_license": {}}
    with db.context.as_system():
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (approved, "dryness_scaling", json.dumps(card(approved)), "approved_for_defined_use", db.now()))
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (research, "dryness_scaling", json.dumps(card(research)), "research_only", db.now() + 1))
    seen = []
    get_engine().register("image.dryness_scaling", lambda p, c: (seen.append(c.capability.model.model_id), HandlerResult("ok", {"note": "visible scaling: mild"}, model_derived=True))[1])
    img = png(skin_photo(seed=15))
    j = post(client, "patient1", img, category="body").json()                                    # approved but NOT active, and a research model exists: nothing runs
    assert j["route"] == "clinician_review" and seen == [] and j["outcomes"] == []
    with db.context.as_system():
        db.set_setting("active_model:dryness_scaling", approved)
    registry.invalidate()
    j = post(client, "patient1", img, category="body").json()
    assert j["route"] == "image_models" and seen == [approved] and j["outcomes"][0]["task"] == "dryness_scaling" and "research_only" not in j["outcomes"][0]["labels"]
    assert "not a diagnosis" in " ".join(j["disclaimers"]).lower() and "clinician reviews" in " ".join(j["disclaimers"]).lower()
    assert post(client, "doctor1", img, category="body").json()["route"] == "image_models"
    with db.context.as_system():
        db.x("DELETE FROM settings WHERE key='active_model:dryness_scaling'")
    registry.invalidate()
    seen.clear()
    assert post(client, "admin1", img, category="body").json()["route"] == "clinician_review" and seen == []      # no active model: the research one is NOT substituted


def test_a_photo_of_a_document_is_sent_to_the_document_pipeline_not_an_image_model(client):
    from app.documents import pipeline
    if not pipeline.ocr_available():
        pytest.skip("Tesseract OCR is not installed")
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i in range(30):
        page.insert_text((50, 60 + i * 24), f"Haemoglobin test number {i} result {10 + i} g/dL reference range 12 - 16", fontsize=11)
    pix = page.get_pixmap(dpi=130)
    img = cv2.cvtColor(np.array(__import__("PIL.Image", fromlist=["Image"]).open(io.BytesIO(pix.tobytes("png"))).convert("RGB")), cv2.COLOR_RGB2BGR)
    j = post(client, "patient1", png(img), category="body").json()
    assert j["route"] == "document_pipeline" and j["outcomes"] == [] and "Reports" in j["message"]
    j2 = post(client, "patient1", png(img), category="medical_report_image").json()
    assert j2["route"] == "document_pipeline" and j2["quality"]["category"] == "medical_report_image"


def test_access_rules_and_limits(client, monkeypatch):
    assert client.post("/api/v1/ai/images/analyze", data={"category": "body"}, files={"file": ("p.png", b"x")}).status_code == 401
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    from app import consent
    with db.context.as_system():
        db.x("DELETE FROM consent_events WHERE user_id='patient1'")
    r = post(client, "patient1", png(skin_photo(seed=16)), category="body")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "consent_required"
    assert post(client, "doctor1", png(skin_photo(seed=16)), category="body").status_code == 200                       # staff are not subject to the patient consent gate
    monkeypatch.delenv("COSMO_REQUIRE_CONSENT")
    huge = b"\x89PNG" + b"0" * (16 * 1024 * 1024)
    assert post(client, "patient1", huge, category="body").status_code == 413

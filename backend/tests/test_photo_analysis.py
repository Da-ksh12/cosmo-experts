"""Photo-analysis sessions (typed contract v1): real geometry, the whole flow through the API, ownership, idempotency, cancellation, contextual answers, and the rule that a skin finding can
only ever come from an APPROVED model. Synthetic photographs exercise the CODE paths; where a test needs a face it stubs the detector and says so. Nothing here measures a model."""
import io
import json
import threading
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app import db, limits
from app.ai import registry, vision
from app.ai.engine import get_engine, reset_engine
from app.ai.schemas import CapabilityState
from app.imaging import quality, suitability
from app.main import app
from app.photo_analysis import answers, builder, service, store
from app.photo_analysis import schema as S

SKIN_BGR = (120, 150, 205)


def skin_photo(w=640, h=640, seed=1, noise=20) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), SKIN_BGR, np.float32)
    img += rng.normal(0, noise, (h, w, 3))
    yy, xx = np.mgrid[:h, :w]
    img += (np.sin(xx / 23.0) * np.cos(yy / 31.0) * 12)[..., None]
    return np.clip(img, 0, 255).astype(np.uint8)


def face_photo(w=640, h=640, seed=1) -> np.ndarray:
    """A skin-toned photo with a smooth left-to-right lighting gradient: it lifts the contrast measure above the face profile's minimum (the plain skin_photo is too flat for that profile)."""
    img = skin_photo(w, h, seed).astype(np.float32)
    xx = np.mgrid[:h, :w][1]
    img += ((xx / w - 0.5) * 70)[..., None]
    return np.clip(img, 0, 255).astype(np.uint8)


def jpg(img, q=92) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()


def png(img) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    registry.invalidate()
    monkeypatch.setattr(quality, "_calibration", lambda: {})
    yield
    reset_engine()


def tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


def send(client, who, photos, region="body", data=None, headers=None):
    files = [("files", (f"p{i}.jpg", b, "image/jpeg")) for i, b in enumerate(photos)]
    return client.post("/api/v1/photo-analyses", data={"region": region, **(data or {})}, files=files, headers={**tok(client, who), **(headers or {})})


def finish(client, who, aid, timeout=60):
    h = tok(client, who)
    t = time.time()
    while time.time() - t < timeout:
        a = client.get(f"/api/v1/photo-analyses/{aid}", headers=h).json()
        if a["status"] not in ("queued", "processing"):
            return a
        time.sleep(0.05)
    raise AssertionError("the analysis did not finish")


# ---------------------------------------------------------------- real geometry
def test_the_face_box_the_detector_found_is_kept_and_is_in_pixels_of_the_displayed_photo(monkeypatch):
    img = face_photo(640, 480)
    monkeypatch.setattr(suitability, "detect_faces", lambda im: [(160, 100, 320, 300)])
    rep = suitability.assess(jpg(img), "face")
    assert rep.verdict == "accepted" and rep.regions == [{"id": "face-1", "kind": "face", "box_px": [160, 100, 320, 300]}]
    assert (rep.metrics["width"], rep.metrics["height"]) == (640, 480)


def test_the_covered_region_is_located_where_the_cover_actually_is():
    clean = skin_photo(seed=4)
    covered = clean.copy()
    covered[:300, :300] = 0                                                         # a lens cover in the top-left corner: ground truth x 0..300, y 0..300 of 640
    frac, box = quality.obstruction_region(covered)
    assert frac >= 0.15 and box is not None
    x, y, w, h = box
    assert x == 0 and y == 0 and abs(w - 300 / 640) < 0.06 and abs(h - 300 / 640) < 0.06        # within one 8-pixel block of the 192-pixel grid, as measured
    assert quality.obstruction_region(clean)[1] is None or quality.obstruction_fraction(clean) < 0.05


def test_geometry_is_validated_and_never_repaired():
    for bad in (dict(x=-0.1, y=0, w=0.1, h=0.1), dict(x=0.9, y=0, w=0.3, h=0.1), dict(x=0, y=0, w=0, h=0.1), dict(x=float("nan"), y=0, w=0.1, h=0.1), dict(x=0, y=0, w=0.1, h=1.2)):
        with pytest.raises(ValidationError):
            S.Box(**bad)
    with pytest.raises(ValidationError):
        S.Polygon(points=[(0, 0), (1, 1)])                                           # fewer than 3 points
    with pytest.raises(ValidationError):
        S.Polygon(points=[(0, 0), (0.5, 2), (1, 1)])                                  # outside the photo
    assert S.box_from_pixels([300, 20, 200, 200], 400, 800) is None                   # runs past the right edge: dropped, not clamped into something plausible
    assert S.box_from_pixels([10, 20, 100, 200], 400, 800).model_dump() == {"type": "box", "x": 0.025, "y": 0.025, "w": 0.25, "h": 0.25}


def test_the_model_refuses_unknown_fields_and_a_skin_finding_without_an_approved_model():
    base = dict(id="a", status="completed", created_at=1.0)
    with pytest.raises(ValidationError):
        S.PhotoAnalysis(**base, surprise=1)
    prov = [S.Provenance(capability="image.quality", version="1", kind="rule_based", approval="not_required", validated=False, note="")]
    obs = S.Observation(id="skin-1", kind="skin_feature", group="observe", label="Spots", tone="info", summary="", provenance=0)
    with pytest.raises(ValidationError):
        S.PhotoAnalysis(**base, provenance=prov, observations=[obs])
    research = [S.Provenance(capability="image.x", version="1", kind="model", approval="research_only", validated=False, note="")]
    with pytest.raises(ValidationError):
        S.PhotoAnalysis(**base, provenance=research, observations=[obs])


# ---------------------------------------------------------------- the whole flow
def test_a_photo_becomes_a_session_in_a_conversation_with_the_real_result(client):
    raw = jpg(skin_photo(seed=11))
    r = send(client, "patient1", [raw])
    assert r.status_code == 202, r.text
    body = r.json()
    a0 = body["analyses"][0]
    assert a0["api_version"] == "photo-analysis/1" and a0["status"] in ("queued", "processing") and a0["conversation_id"] == body["conversation_id"]
    a = finish(client, "patient1", a0["id"])
    assert a["status"] == "completed" and a["outcome"]["verdict"] == "accepted" and a["outcome"]["route"] == "clinician_review"
    # the photo is the exact file that was sent, and is described without a storage path
    assert a["image"]["kept"] and a["image"]["url"].startswith("/api/images/") and a["image"]["url"].endswith("/file") and "stored_path" not in json.dumps(a) and "private_uploads" not in json.dumps(a)
    file = client.get(a["image"]["url"], headers=tok(client, "patient1"))
    assert file.status_code == 200 and file.content == raw and file.headers["content-type"] == "image/jpeg"
    assert (a["transform"]["width"], a["transform"]["height"]) == (640, 640) and a["coordinate_system"]["reference"] == "image_as_displayed" and a["transform"]["server_modified"] is False
    # what it shows comes from measurements
    by = {o["id"]: o for o in a["observations"]}
    assert {"quality", "lighting", "subject", "framing", "overall"} <= set(by) and by["lighting"]["evidence"] and by["lighting"]["provenance"] in (0, 1, 2)
    assert all(o["kind"] != "skin_feature" for o in a["observations"])                       # no approved model exists: nothing about the skin is produced
    assert [c["task"] for c in a["capabilities"]] == ["dryness_scaling"] and a["capabilities"][0]["available"] is False
    assert any("not your skin" in x for x in a["sections"]["cannot"]) and a["sections"]["next_steps"] and a["sections"]["questions"][0]["intent"] == "measurements"
    # the assessment is in the conversation: the patient's message and the assistant's card, each pointing at the session
    msgs = client.get(f"/api/conversations/{body['conversation_id']}", headers=tok(client, "patient1")).json()
    assert [m["role"] for m in msgs] == ["patient", "assistant"] and all(m["sources"][0] == {"kind": "photo_analysis", "analysis_id": a0["id"], "index": 0} for m in msgs)


def test_a_face_photo_exposes_the_face_area_as_real_geometry(client, monkeypatch):
    monkeypatch.setattr(suitability, "detect_faces", lambda im: [(160, 120, 320, 320)])
    a = finish(client, "patient1", send(client, "patient1", [jpg(face_photo(640, 640, seed=12))], region="face").json()["analyses"][0]["id"])
    assert a["status"] == "completed"
    face = next(o for o in a["observations"] if o["id"] == "face-1")
    assert face["kind"] == "face_region" and face["group"] == "area" and face["geometry"] == {"type": "box", "x": 0.25, "y": 0.1875, "w": 0.5, "h": 0.5}
    assert face["confidence"] is None and "face-1" in a["sections"]["areas"] and "validated" in face and face["provenance"] == 2
    assert a["provenance"][2]["capability"] == "face.detect" and a["provenance"][2]["validated"] is False
    assert any(q["observation_id"] == "face-1" for q in a["sections"]["questions"])


def test_exif_orientation_is_applied_to_size_and_to_coordinates(client, monkeypatch):
    im = Image.fromarray(cv2.cvtColor(face_photo(600, 400, seed=13), cv2.COLOR_BGR2RGB))
    exif = Image.Exif()
    exif[0x0112] = 6                                                                      # the file says: rotate 90 degrees clockwise to display
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    monkeypatch.setattr(suitability, "detect_faces", lambda i: [(100, 150, 200, 300)])  # in pixels of the DISPLAYED (rotated) photo: 400 wide, 600 high
    a = finish(client, "patient1", send(client, "patient1", [buf.getvalue()], region="face").json()["analyses"][0]["id"])
    assert (a["transform"]["width"], a["transform"]["height"], a["transform"]["exif_orientation"]) == (400, 600, 6)
    assert a["image"]["width"] == 400 and a["image"]["height"] == 600
    assert next(o for o in a["observations"] if o["id"] == "face-1")["geometry"] == {"type": "box", "x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}


def test_an_unusable_photo_is_not_kept_and_says_how_to_fix_it(client):
    dark = (skin_photo(seed=14) * 0.2).astype(np.uint8)
    a = finish(client, "patient1", send(client, "patient1", [jpg(dark)]).json()["analyses"][0]["id"])
    assert a["status"] == "rejected" and a["outcome"]["verdict"] == "rejected" and a["outcome"]["reason"] == "too_dark"
    assert a["image"]["kept"] is False and a["image"]["url"] is None and a["clinician_review"]["status"] == "not_applicable"
    retake = next(s for s in a["sections"]["next_steps"] if s["kind"] == "retake")
    assert "light" in retake["detail"].lower() and retake["route"] == "/photo?region=body"
    assert next(o for o in a["observations"] if o["id"] == "lighting")["tone"] == "attention"
    assert any(q["intent"] == "better_photo" for q in a["sections"]["questions"])


def test_png_photos_are_served_as_png(client):
    raw = png(skin_photo(seed=15))
    a = finish(client, "patient1", send(client, "patient1", [raw]).json()["analyses"][0]["id"])
    f = client.get(a["image"]["url"], headers=tok(client, "patient1"))
    assert f.content == raw and f.headers["content-type"] == "image/png"


def test_several_photos_share_one_conversation_and_one_card(client):
    r = send(client, "patient1", [jpg(skin_photo(seed=20)), jpg(skin_photo(seed=21))])
    body = r.json()
    ids = [x["id"] for x in body["analyses"]]
    assert r.status_code == 202 and len(ids) == 2 and ids[0] != ids[1] and [x["index"] for x in body["analyses"]] == [0, 1]
    for i in ids:
        assert finish(client, "patient1", i)["status"] in ("completed", "uncertain", "rejected")
    msgs = client.get(f"/api/conversations/{body['conversation_id']}", headers=tok(client, "patient1")).json()
    assert len(msgs) == 2 and [s["analysis_id"] for s in msgs[1]["sources"]] == ids
    assert send(client, "patient1", [jpg(skin_photo(seed=30 + i)) for i in range(5)]).status_code == 422          # at most four at a time
    assert client.post("/api/v1/photo-analyses", data={"region": "bogus"}, files=[("files", ("p.jpg", jpg(skin_photo()), "image/jpeg"))], headers=tok(client, "patient1")).status_code == 400


def test_the_same_upload_sent_twice_returns_the_same_sessions(client):
    raw = jpg(skin_photo(seed=40))
    h = {"Idempotency-Key": "retry-photo-1"}
    first = send(client, "patient1", [raw], headers=h).json()
    finish(client, "patient1", first["analyses"][0]["id"])
    second = send(client, "patient1", [raw], headers=h)
    assert second.status_code == 200 and second.json()["idempotent_replay"] is True
    assert second.json()["analyses"][0]["id"] == first["analyses"][0]["id"] and second.json()["conversation_id"] == first["conversation_id"]
    assert len(client.get(f"/api/conversations/{first['conversation_id']}", headers=tok(client, "patient1")).json()) == 2        # not two more messages


def test_deleting_the_photo_removes_it_from_the_assessment(client):
    a = finish(client, "patient1", send(client, "patient1", [jpg(skin_photo(seed=41))]).json()["analyses"][0]["id"])
    assert a["image"]["kept"]
    assert client.delete(f"/api/images/{a['image']['id']}", headers=tok(client, "patient1")).status_code == 200
    again = client.get(f"/api/v1/photo-analyses/{a['id']}", headers=tok(client, "patient1")).json()
    assert again["image"]["kept"] is False and again["image"]["url"] is None and again["clinician_review"]["status"] == "not_applicable"


# ---------------------------------------------------------------- ownership
def test_only_the_owner_can_read_cancel_ask_about_or_add_to_a_session(client):
    a = finish(client, "patient1", send(client, "patient1", [jpg(skin_photo(seed=50))]).json()["analyses"][0]["id"])
    aid, p2 = a["id"], tok(client, "patient2")
    assert client.get(f"/api/v1/photo-analyses/{aid}", headers=p2).status_code == 404                  # exactly like an id that does not exist
    assert client.get("/api/v1/photo-analyses/does-not-exist", headers=p2).status_code == 404
    assert client.post(f"/api/v1/photo-analyses/{aid}/cancel", headers=p2).status_code == 404
    assert client.get(f"/api/v1/photo-analyses/{aid}").status_code == 401
    assert client.get(f"/api/v1/photo-analyses/{aid}", headers=tok(client, "doctor1")).status_code == 403
    assert client.get(a["image"]["url"], headers=p2).status_code == 404
    c2 = client.post("/api/conversations", headers=p2).json()["id"]
    r = client.post(f"/api/conversations/{c2}/messages", json={"text": "Explain what was measured.", "context": {"analysis_id": aid}}, headers=p2)
    assert r.status_code == 404                                                                          # another person's session cannot be used as a question's context
    assert client.get(f"/api/conversations/{c2}", headers=p2).json() == []                              # ... and the refused question left no message behind
    other = client.post("/api/v1/photo-analyses", data={"region": "body", "conversation_id": client.post("/api/conversations", headers=tok(client, "patient1")).json()["id"]},
                        files=[("files", ("p.jpg", jpg(skin_photo(seed=51)), "image/jpeg"))], headers=p2)
    assert other.status_code == 404                                                                      # nor can a photo be added to someone else's conversation


# ---------------------------------------------------------------- progress and cancellation
def test_the_real_stage_is_reported_while_processing_and_a_cancel_discards_everything(client, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    real = service.core.check_and_store_image

    def slow(owner, region, data, request_id=""):
        entered.set()
        assert gate.wait(30)
        return real(owner, region, data, request_id)
    monkeypatch.setattr(service.core, "check_and_store_image", slow)
    h = tok(client, "patient1")
    kept_before = {r["id"] for r in db.q("SELECT id FROM images WHERE owner_id='patient1' AND deleted=0 AND stored_path!=''")}
    a0 = send(client, "patient1", [jpg(skin_photo(seed=60))]).json()["analyses"][0]
    assert entered.wait(30)
    now = client.get(f"/api/v1/photo-analyses/{a0['id']}", headers=h).json()
    assert now["status"] == "processing" and now["stage"] == "quality" and now["observations"] == []             # the real stage, and nothing invented before there is a result
    cancelled = client.post(f"/api/v1/photo-analyses/{a0['id']}/cancel", headers=h)
    assert cancelled.status_code == 200
    gate.set()
    final = finish(client, "patient1", a0["id"], timeout=30)
    assert final["status"] == "cancelled" and final["image"]["kept"] is False and final["outcome"]["headline"] == "The check was cancelled"
    time.sleep(0.3)
    kept_after = {r["id"] for r in db.q("SELECT id FROM images WHERE owner_id='patient1' AND deleted=0 AND stored_path!=''")}
    assert kept_after == kept_before                                                                          # the photo that had already been stored was taken back out


# ---------------------------------------------------------------- the photo as the context of a question
def _chat(client, who, text, **ctx):
    cid = client.post("/api/conversations", headers=tok(client, who)).json()["id"]
    return client.post(f"/api/conversations/{cid}/messages", json={"text": text, **({"context": ctx} if ctx else {})}, headers=tok(client, who))


def test_questions_about_the_photo_are_answered_from_its_own_measurements(client):
    a = finish(client, "patient1", send(client, "patient1", [jpg(skin_photo(seed=70))]).json()["analyses"][0]["id"])
    r = _chat(client, "patient1", "Explain what was measured.", analysis_id=a["id"], intent="measurements").json()
    assert r["mode"] == "analysis" and r["answer"].startswith("Here is what the photo check measured") and "not your skin" in r["answer"] and r["abstained"] is False
    assert r["sources"] and all(s["kind"] == "photo_analysis" and s["analysis_id"] == a["id"] and s["observation_id"] for s in r["sources"])
    lighting = next(o for o in a["observations"] if o["id"] == "lighting")
    r = _chat(client, "patient1", "Explain: Lighting.", analysis_id=a["id"], observation_id="lighting", intent="observation").json()
    assert lighting["evidence"][0]["text"] in r["answer"] and "no confidence score" in r["answer"] and r["sources"][0]["observation_id"] == "lighting"
    r = _chat(client, "patient1", "Please prepare a summary for my dermatologist", analysis_id=a["id"]).json()                 # recognised in the person's own words
    assert r["answer"].startswith("Photo summary for your dermatologist") and "Not assessed: skin features" in r["answer"] and "not a diagnosis" in r["answer"]
    assert _chat(client, "patient1", "Explain: Lighting.", analysis_id=a["id"], observation_id="no-such-area").status_code == 404


def test_a_question_about_skin_features_is_answered_honestly_and_a_medicine_question_is_still_refused(client):
    a = finish(client, "patient1", send(client, "patient1", [jpg(skin_photo(seed=71))]).json()["analyses"][0]["id"])
    r = _chat(client, "patient1", "Can you find the acne and redness on my face?", analysis_id=a["id"]).json()
    assert "can't identify skin features" in r["answer"] and "won't guess" in r["answer"] and r["mode"] == "analysis"
    assert all(w not in r["answer"].lower() for w in ("you have", "likely", "diagnos"))
    refused = _chat(client, "patient1", "Which medicine should I take for this?", analysis_id=a["id"]).json()
    assert refused["abstained"] is True and refused["reason"] == "clinical_decision"
    urgent = _chat(client, "patient1", "I have severe chest pain right now", analysis_id=a["id"]).json()
    assert urgent["abstained"] is True and urgent["reason"] == "urgent"


def test_a_question_that_is_not_about_the_photo_is_not_forced_into_it(client):
    a = finish(client, "patient1", send(client, "patient1", [jpg(skin_photo(seed=72))]).json()["analyses"][0]["id"])
    r = _chat(client, "patient1", "What is a reference range?", analysis_id=a["id"]).json()
    assert r.get("mode") != "analysis" and not r["answer"].startswith("Here is what the photo check measured")
    plain = _chat(client, "patient1", "What is a reference range?").json()                               # no context: the normal assistant, unchanged
    assert plain.get("mode") != "analysis"


def test_compare_is_offered_only_when_an_earlier_photo_exists(client):
    first = finish(client, "patient2", send(client, "patient2", [jpg(skin_photo(seed=80))]).json()["analyses"][0]["id"])
    assert all(q["intent"] != "compare" for q in first["sections"]["questions"])
    second = finish(client, "patient2", send(client, "patient2", [jpg(skin_photo(seed=81))]).json()["analyses"][0]["id"])
    assert any(q["intent"] == "compare" for q in second["sections"]["questions"])
    r = _chat(client, "patient2", "Compare this with my previous photo.", analysis_id=second["id"], intent="compare").json()
    assert "don't measure differences" in r["answer"] and r["actions"][0]["route"] == "/history?kind=photos"


# ---------------------------------------------------------------- skin findings only from an approved model
def _state(cap, approval, patient_facing=True):
    return CapabilityState(id=cap, kind="image", name="Acne-like visible features", version="1", description="", availability="available", approval=approval,
                           status="approved_for_defined_use" if approval == "approved_for_defined_use" else "research_only", patient_facing=patient_facing,
                           audiences=["patient", "clinician", "admin"] if approval == "approved_for_defined_use" else ["admin"], limitations=["Fixture limitation."])


def _fake_analysis(capability, findings):
    return vision.ImageAnalysis(request_id="r", category="face", category_label="Face", route="image_models", message="", clinician_review=True,
                                outcomes=[vision.TaskOutcome(task="acne_features", capability=capability, status="ok", result={"findings": findings})])


FINDINGS = [{"id": "x", "label": "Acne-like spot", "summary": "A fixture finding.", "score": 0.8, "calibration": {"calibrated": True, "basis": "test fixture"}, "evidence": ["fixture evidence"],
             "geometry": {"type": "point", "x": 0.4, "y": 0.5}},
            {"id": "bad", "label": "Out of range", "summary": "invalid geometry", "geometry": {"type": "box", "x": 0.9, "y": 0.1, "w": 0.5, "h": 0.2}},
            {"id": "u", "label": "Uncalibrated", "summary": "no calibration", "score": 0.99, "geometry": {"type": "polygon", "points": [[0.1, 0.1], [0.3, 0.1], [0.2, 0.3]]}}]


def test_an_approved_models_findings_are_shown_validated_and_a_research_model_never_is(monkeypatch):
    """CONTROLLED TEST DOUBLE (not a model): proves the adapter, the validation and the approval rule. No such model exists in the product."""
    out = {"id": "img-x", "report": {"verdict": "accepted", "reason": "ok", "message": "ok", "metrics": {"width": 640, "height": 640}, "checks": [], "warnings": [], "regions": []}, "next": "n"}
    params = {"region": "face"}
    eng = get_engine()
    monkeypatch.setattr(eng, "describe", lambda cid: _state("image.acne_features", "approved_for_defined_use") if cid == "image.acne_features" else CapabilityState(
        id=cid, kind="image", name=cid, version="1", description="", availability="unavailable", approval="none", status="unavailable", patient_facing=False, audiences=["admin"]))
    a = builder.build(analysis_id="s1", owner="patient1", out=out, data=None, params=params, created_at=1.0, finished_at=2.0, ia=_fake_analysis("image.acne_features", FINDINGS))
    skin = [o for o in a["observations"] if o["kind"] == "skin_feature"]
    assert [o["label"] for o in skin] == ["Acne-like spot", "Uncalibrated"]                         # the finding with an impossible box was dropped, not repaired
    assert skin[0]["geometry"] == {"type": "point", "x": 0.4, "y": 0.5} and skin[0]["confidence"] == {"value": 0.8, "calibrated": True, "basis": "test fixture"}
    assert skin[1]["confidence"] is None and skin[1]["geometry"]["type"] == "polygon"             # a score without calibration is not shown as confidence
    assert a["provenance"][skin[0]["provenance"]]["kind"] == "model" and a["provenance"][skin[0]["provenance"]]["approval"] == "approved_for_defined_use"
    monkeypatch.setattr(eng, "describe", lambda cid: _state("image.acne_features", "research_only", patient_facing=False))
    b = builder.build(analysis_id="s2", owner="patient1", out=out, data=None, params=params, created_at=1.0, finished_at=2.0, ia=_fake_analysis("image.acne_features", FINDINGS))
    assert not [o for o in b["observations"] if o["kind"] == "skin_feature"]                        # research output never reaches a patient


def test_every_skin_analysis_is_reported_as_not_available_today(client):
    a = finish(client, "patient1", send(client, "patient1", [jpg(skin_photo(seed=90))], region="face").json()["analyses"][0]["id"])
    assert {c["task"] for c in a["capabilities"]} == {"acne_features", "pigmentation", "redness_irritation", "dryness_scaling", "texture_scarring"}
    assert all(c["available"] is False and "No approved analysis" in c["note"] for c in a["capabilities"])
    assert not any(o["kind"] == "skin_feature" for o in a["observations"])


def test_the_upload_route_has_the_upload_size_and_rate_limit_rules():
    assert limits.UPLOAD_ROUTES.match("POST /api/v1/photo-analyses") and not limits.UPLOAD_ROUTES.match("GET /api/v1/photo-analyses/x")
    from app.middleware import LIMITS
    assert any(name == "submit" and rx.match("POST /api/v1/photo-analyses") for name, rx, *_ in LIMITS)

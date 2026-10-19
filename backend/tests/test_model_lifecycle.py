"""Model lifecycle and release control (ai/lifecycle.py). Every model here is a SYNTHETIC FIXTURE: a row with a hand-built card and a small file. Nothing in this module says anything about a real
model; it tests the rules that decide which model may answer."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, status as st_
from app.ai import lifecycle as lc, registry
from app.ai.engine import HandlerResult, get_engine, reset_engine
from app.ai.errors import RejectedInput
from app.config import MODEL_DIR
from app.main import app

TASK = "acne_features"                       # an image task without critical-class rules: the generic evaluation gates apply
FULL = {r: True for r in st_.RELEASE_REQUIREMENTS}
MADE: list[str] = []


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean(client):
    yield
    with db.context.as_system():
        for t in (TASK, "lesion_classification_research"):
            db.x("DELETE FROM settings WHERE key IN (?,?)", (f"active_model:{t}", f"model_history:{t}"))
        db.x("DELETE FROM settings WHERE key LIKE 'approval:%'")
    lc._outcomes.clear()
    registry.invalidate()
    reset_engine()


def tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def passing(mid: str, task: str = TASK, **over) -> dict:
    f = MODEL_DIR / task
    f.mkdir(parents=True, exist_ok=True)
    art = f / f"{mid}.bin"
    art.write_bytes(os.urandom(64) + mid.encode())
    card = {"model_id": mid, "task": task, "architecture": "resnet18", "classes": ["a", "b"], "dataset_id": "synthetic-fixture", "dataset_version_id": "dv-fixture", "dataset_version": 1,
            "artifact_path": str(art), "artifact_sha256": sha(art), "intended_use": "FIXTURE ONLY. Not a real model.", "limitations": ["synthetic fixture"],
            "dataset_license": {"spdx": "CC-BY-4.0", "terms_snapshot_hash": "fixture"}, "near_duplicates_vs_train": {"val": 0, "test": 0}, "baselines": {"majority_class": {"macro_f1": 0.1}},
            "test_metrics": {"n": 500, "macro_f1": 0.9, "macro_f1_ci95": [0.85, 0.93], "ece": 0.05, "robustness": {"blur": 0.8}, "subgroups_by_fitzpatrick": {"1-3": {"n": 300}, "4-6": {"n": 200}}},
            "approval": None}
    card.update(over)
    with db.context.as_system():
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (mid, task, json.dumps(card), "research_only", db.now()))
    MADE.append(mid)
    return card


def failing(mid: str, task: str = TASK) -> dict:
    return passing(mid, task, test_metrics={"n": 10, "macro_f1": 0.3, "ece": 0.4}, baselines={"majority_class": {"macro_f1": 0.5}})


def verified_doctor(client, yes=True):
    with db.context.as_system():
        db.x("UPDATE users SET verified=? WHERE id='doctor1'", (1 if yes else 0,))


def state(mid):
    with db.context.as_system():
        row = db.q1("SELECT * FROM model_versions WHERE id=?", (mid,))
        return lc.state_of(row)


def release(mid, client=None):
    """evaluate -> approve with everything in order (a verified clinician named) -> activate."""
    with db.context.as_system():
        db.x("UPDATE users SET verified=1 WHERE id='doctor1'")
    lc.evaluate(mid, "admin1")
    ap = lc.approve(mid, "admin1", "FIXTURE ONLY", FULL, "", "doctor1")
    assert ap["effective_status"] == "approved_for_defined_use", ap
    return lc.activate(mid, "admin1")


def kinds(mid):
    return [e["event"] for e in lc.events(mid)]


# ---------------------------------------------------------------- evaluation and approval
def test_a_model_that_fails_the_gates_cannot_be_approved_or_activated(client):
    failing("lc-f1")
    assert state("lc-f1") == "experimental"
    with pytest.raises(PermissionError, match="complete approval record"):
        lc.activate("lc-f1", "admin1")
    r = lc.evaluate("lc-f1", "admin1")
    assert r["state"] == "evaluation_failed" and {"min_test_images", "beat_baselines_macro_f1"} <= set(r["failed_gates"])
    ap = lc.approve("lc-f1", "admin1", "x", FULL, "", "doctor1")
    assert ap["effective_status"] == "research_only" and "min_test_images" in ap["failed_evaluation_gates"] and state("lc-f1") == "evaluation_failed"
    # every refusal is on the record, in order (an approval request re-runs the evaluation first, so it appears twice)
    assert kinds("lc-f1") == ["activation_refused", "evaluation_started", "evaluation_failed", "evaluation_started", "evaluation_failed", "approval_refused"]


def test_passing_the_gates_is_necessary_and_not_sufficient(client):
    passing("lc-p1")
    verified_doctor(client)
    r = lc.evaluate("lc-p1", "admin1")
    assert r["state"] == "review_pending" and r["failed_gates"] == []
    part = lc.approve("lc-p1", "admin1", "x", {"held_out_evaluation_passed": True}, "", "doctor1")
    assert part["effective_status"] == "research_only" and "clinical_signoff_recorded" in part["missing_requirements"] and state("lc-p1") == "review_pending"
    none = lc.approve("lc-p1", "admin1", "x", FULL, "", None)                                    # claiming a clinical sign-off without naming anyone
    assert none["effective_status"] == "research_only" and any("verified clinician" in x for x in none["refused_because"]) and state("lc-p1") == "review_pending"
    verified_doctor(client, False)
    unverified = lc.approve("lc-p1", "admin1", "x", FULL, "", "doctor1")                          # a clinician whose credentials nobody verified
    assert unverified["effective_status"] == "research_only" and state("lc-p1") == "review_pending"
    for who in ("patient1", "admin1", "nobody"):
        assert lc.approve("lc-p1", "admin1", "x", FULL, "", who)["effective_status"] == "research_only"      # only a verified DOCTOR counts
    verified_doctor(client)
    ok = lc.approve("lc-p1", "admin1", "Fixture use", FULL, "note", "doctor1")
    assert ok["effective_status"] == "approved_for_defined_use" and ok["clinical_signoff_by"] == "doctor1" and state("lc-p1") == "approved"
    with db.context.as_system():
        assert db.q1("SELECT status FROM model_versions WHERE id='lc-p1'")["status"] == "approved_for_defined_use"
    verified_doctor(client, False)


def test_an_approved_model_is_not_serving_until_it_is_activated(client):
    passing("lc-a1")
    verified_doctor(client)
    lc.evaluate("lc-a1", "admin1")
    lc.approve("lc-a1", "admin1", "x", FULL, "", "doctor1")
    s = registry.resolve(registry.get(f"image.{TASK}"))
    assert not s.patient_facing and s.approval != "approved_for_defined_use" and (s.model is None or s.model.lifecycle != "active")          # approved is not the same as active
    out = lc.activate("lc-a1", "admin1")
    registry.invalidate()
    s = registry.resolve(registry.get(f"image.{TASK}"))
    assert out["active"] == "lc-a1" and s.availability == "available" and s.approval == "approved_for_defined_use" and s.patient_facing and s.model.lifecycle == "active"
    assert kinds("lc-a1")[-1] == "activated"
    verified_doctor(client, False)


def test_activation_refuses_a_damaged_missing_or_incomplete_model(client):
    verified_doctor(client)
    c = passing("lc-d1")
    lc.evaluate("lc-d1", "admin1"); lc.approve("lc-d1", "admin1", "x", FULL, "", "doctor1")
    art = Path(c["artifact_path"])
    good = art.read_bytes()
    art.write_bytes(good + b"x")
    with pytest.raises(PermissionError, match="does not match its recorded checksum"):
        lc.activate("lc-d1", "admin1")
    art.unlink()
    with pytest.raises(PermissionError, match="file is missing"):
        lc.activate("lc-d1", "admin1")
    art.write_bytes(good)
    lc.flag_clinical_issue("lc-d1", "admin1", "a reviewer saw a wrong output")
    with pytest.raises(PermissionError, match="clinical issue is open"):
        lc.activate("lc-d1", "admin1")
    lc.close_clinical_issue("lc-d1", "admin1", "reviewed; not caused by the model")
    assert lc.activate("lc-d1", "admin1")["active"] == "lc-d1"
    assert [e["detail"].get("problems") for e in lc.events("lc-d1") if e["event"] == "activation_refused"][0]
    # a record without its limitations, intended use or evaluation is not activatable either
    for name, over in (("lc-d2", {"limitations": []}), ("lc-d3", {"dataset_version_id": None, "dataset_id": None, "source": None}), ("lc-d4", {"test_metrics": {}})):
        c2 = passing(name, **over)
        with db.context.as_system():
            row = db.q1("SELECT * FROM model_versions WHERE id=?", (name,))
            card = json.loads(row["data"]); card["lifecycle"] = {"state": "approved"}; card["approval"] = {"x": 1}
            db.x("UPDATE model_versions SET data=?, status='approved_for_defined_use' WHERE id=?", (json.dumps(card), name))
        with pytest.raises(PermissionError):
            lc.activate(name, "admin1")
    verified_doctor(client, False)


# ---------------------------------------------------------------- known-good fallback and rollback
def test_the_previous_model_is_kept_as_the_known_good_and_a_rollback_returns_to_it(client):
    a, b = passing("lc-k1"), passing("lc-k2")
    release("lc-k1"); release("lc-k2")
    assert state("lc-k1") == "approved" and state("lc-k2") == "active"
    with db.context.as_system():
        assert json.loads(db.q1("SELECT value FROM settings WHERE key=?", (f"model_history:{TASK}",))["value"]) == ["lc-k1"]
    out = lc.rollback(TASK, "admin1", "technical", "latency")
    assert out["active"] == "lc-k1" and out["rolled_back"] == "lc-k2" and out["kind"] == "technical" and "does not change or correct any result already issued" in out["note"]
    assert state("lc-k2") == "rolled_back" and state("lc-k1") == "active"
    # the model rolled back from keeps its record and its file; results it produced can still be traced to it
    v = lc.view("lc-k2")
    assert v["state"] == "rolled_back" and v["artifact"]["verifies"] is True and Path(b["artifact_path"]).is_file() and v["approval"]["clinical_signoff_by"] == "doctor1"
    assert [e["event"] for e in v["events"]][-2:] == ["activated", "rolled_back"] and v["events"][-1]["detail"]["kind"] == "technical"
    w = lc.exposure("lc-k2")
    assert len(w) == 1 and w[0]["from"] <= w[0]["to"] and w[0]["ended_by"] == "rolled_back"
    with pytest.raises(PermissionError):
        lc.activate("lc-k2", "admin1")                                                            # a rolled-back model is not simply switched back on
    verified_doctor(client, False)


def test_a_rollback_skips_a_known_good_model_whose_file_no_longer_verifies(client):
    cards = {n: passing(n) for n in ("lc-s1", "lc-s2", "lc-s3")}
    for n in cards:
        release(n)
    Path(cards["lc-s2"]["artifact_path"]).write_bytes(b"corrupted")
    out = lc.rollback(TASK, "admin1")
    assert out["active"] == "lc-s1" and out["skipped"][0]["model"] == "lc-s2" and "checksum" in out["skipped"][0]["why"]
    verified_doctor(client, False)


def test_with_nothing_good_to_return_to_the_capability_is_unavailable_and_nothing_is_substituted(client):
    passing("lc-n1"); release("lc-n1")
    passing("lc-n2", task=TASK)                                                                    # another model exists, but is neither approved nor active
    out = lc.rollback(TASK, "admin1")
    registry.invalidate()
    s = registry.resolve(registry.get(f"image.{TASK}"))
    assert out["active"] is None and s.patient_facing is False and s.approval != "approved_for_defined_use"        # research models stay research-only (administrators, explicit request); patients get nothing
    verified_doctor(client, False)


def test_deactivating_without_a_replacement_makes_the_capability_unavailable(client):
    passing("lc-x1"); release("lc-x1")
    out = lc.deactivate(TASK, "admin1", "maintenance")
    registry.invalidate()
    s = registry.resolve(registry.get(f"image.{TASK}"))
    assert out["active"] is None and state("lc-x1") == "approved" and not s.patient_facing and s.approval != "approved_for_defined_use"       # nothing answers for patients
    with pytest.raises(lc.LifecycleError):
        lc.deactivate(TASK, "admin1")
    verified_doctor(client, False)


def test_an_active_model_that_cannot_be_used_is_reported_and_never_replaced(client):
    c1, c2 = passing("lc-u1"), passing("lc-u2")
    release("lc-u1"); release("lc-u2")
    lc.deactivate(TASK, "admin1")
    lc.activate("lc-u1", "admin1")                                                                 # u1 active, u2 approved and ready
    Path(c1["artifact_path"]).write_bytes(b"damaged")
    registry.invalidate()
    s = registry.resolve(registry.get(f"image.{TASK}"))
    assert s.model is None or s.model.model_id != "lc-u2"                                          # u2 is NOT quietly put in its place
    assert not s.patient_facing and any("cannot be used" in r and "checksum" in r for r in s.reasons)
    assert lc.active_approved(TASK) is None and lc.active_problem(TASK)
    verified_doctor(client, False)


# ---------------------------------------------------------------- a technical rollback is not a clinical correction
def test_a_clinical_issue_is_separate_from_a_rollback(client):
    passing("lc-c1"); release("lc-c1")
    out = lc.flag_clinical_issue("lc-c1", "admin1", "Output looked wrong to a reviewer on 3 reports")
    assert out["clinical_issue_open"] is True and out["periods_to_review"][0]["to"] is None and "does not take the model out of service" in out["note"]
    assert state("lc-c1") == "active" and lc.active_approved(TASK).model_id == "lc-c1"              # reporting an issue changes nothing that is serving
    assert "clinical_issue_reported" in kinds("lc-c1")
    lc.rollback(TASK, "admin1")
    assert lc.view("lc-c1")["clinical_issue_open"] and lc.view("lc-c1")["state"] == "rolled_back"      # the issue stays open after a rollback: it concerns what was already shown
    lc.close_clinical_issue("lc-c1", "admin1", "periods reviewed")
    assert not lc.view("lc-c1")["clinical_issue_open"] and "clinical_issue_closed" in kinds("lc-c1")
    with pytest.raises(lc.LifecycleError, match="No clinical issue"):
        lc.close_clinical_issue("lc-c1", "admin1", "again")
    verified_doctor(client, False)


# ---------------------------------------------------------------- the state machine and the history
def test_impossible_moves_are_refused(client):
    passing("lc-m1")
    with db.context.as_system():
        row = db.q1("SELECT * FROM model_versions WHERE id='lc-m1'")
        card = json.loads(row["data"])
        with pytest.raises(lc.LifecycleError, match="cannot become"):
            lc._move(row, card, "approved", "t", "x")                                            # experimental -> approved skips evaluation and review
        with pytest.raises(lc.LifecycleError, match="cannot become"):
            lc._move(row, card, "active", "t", "x")
    lc.deprecate("lc-m1", "admin1", "obsolete")
    with pytest.raises(lc.LifecycleError):
        lc.evaluate("lc-m1", "admin1")
    with pytest.raises(PermissionError):
        lc.activate("lc-m1", "admin1")
    passing("lc-m2"); release("lc-m2")
    with pytest.raises(lc.LifecycleError, match="must be rolled back or deactivated"):
        lc.deprecate("lc-m2", "admin1")
    assert set(lc.TRANSITIONS["deprecated"]) == set() and set(lc.TRANSITIONS) == set(lc.STATES)
    verified_doctor(client, False)


def test_the_history_is_append_only(client):
    passing("lc-h1"); lc.evaluate("lc-h1", "admin1")
    with db.context.as_system():
        n = db.q1("SELECT COUNT(*) c FROM model_events WHERE model_id='lc-h1'")["c"]
        assert n >= 2
        for sql in ("UPDATE model_events SET event='rewritten' WHERE model_id='lc-h1'", "DELETE FROM model_events WHERE model_id='lc-h1'"):
            with pytest.raises(Exception, match="append-only|permission|denied"):
                db.x(sql)
        assert db.q1("SELECT COUNT(*) c FROM model_events WHERE model_id='lc-h1'")["c"] == n
    assert [e["event"] for e in lc.events("lc-h1")][:2] == ["evaluation_started", "review_requested"]


def test_an_imported_model_is_unevaluated_and_stays_out_of_service(client):
    outside = Path(os.environ["COSMO_DATA_DIR"]) / "outside.bin"
    outside.write_bytes(b"x")
    with pytest.raises(lc.LifecycleError, match="inside the application's model directory"):
        lc.register_import(TASK, str(outside), "admin1", architecture="resnet18", source="somewhere")
    f = MODEL_DIR / "imports"; f.mkdir(parents=True, exist_ok=True); art = f / "imp.bin"; art.write_bytes(b"weights")
    card = lc.register_import(TASK, str(art), "admin1", architecture="resnet18", source="a published checkpoint", licence="unverified")
    mid = card["model_id"]; MADE.append(mid)
    assert state(mid) == "imported" and kinds(mid) == ["imported"]
    with pytest.raises(PermissionError):
        lc.activate(mid, "admin1")
    r = lc.evaluate(mid, "admin1")
    assert r["state"] == "evaluation_failed" and "min_test_images" in r["failed_gates"]            # no evaluation has been run on it


# ---------------------------------------------------------------- monitoring: only reliable signals
def test_too_few_runs_or_unreliable_signals_never_trigger_anything(client):
    passing("lc-o1"); release("lc-o1")
    for _ in range(10):
        lc.record_outcome(TASK, False)
    d = lc.evaluate_signals(TASK)
    assert d["action"] == "none" and "too few" in d["reason"]                                       # ten failures in ten runs is still only ten runs
    for _ in range(100):
        lc.record_outcome(TASK, True)
    assert lc.evaluate_signals(TASK)["action"] == "none"
    verified_doctor(client, False)


def test_a_high_failure_rate_over_enough_runs_is_a_reliable_signal_and_acts_only_when_switched_on(client, monkeypatch):
    passing("lc-r1"); passing("lc-r2"); release("lc-r1"); release("lc-r2")
    for i in range(60):
        lc.record_outcome(TASK, i % 3 == 0)                                                        # 40 failures in 60 runs
    d = lc.evaluate_signals(TASK)
    assert d["action"] == "rollback" and d["evidence"]["runs"] == 60 and d["evidence"]["failure_rate_lower_bound"] >= lc.FAILURE_LOWER_BOUND
    monkeypatch.delenv("COSMO_MODEL_AUTO_ROLLBACK", raising=False)
    r = lc.maybe_auto_rollback(TASK)
    assert r["executed"] is False and state("lc-r2") == "active"                                    # off by default: a recommendation only
    monkeypatch.setenv("COSMO_MODEL_AUTO_ROLLBACK", "1")
    r = lc.maybe_auto_rollback(TASK)
    assert r["executed"] is True and r["result"]["active"] == "lc-r1" and r["result"]["kind"] == "automatic"
    assert state("lc-r2") == "rolled_back" and lc.events("lc-r2")[-1]["actor"] == "system:monitor"
    verified_doctor(client, False)


def test_the_model_file_failing_its_checksum_is_an_immediate_deterministic_signal(client, monkeypatch):
    c = passing("lc-i1"); release("lc-i1")
    Path(c["artifact_path"]).write_bytes(b"flipped")
    d = lc.evaluate_signals(TASK)
    assert d["action"] == "rollback" and d["deterministic"] is True and "checksum" in d["reason"]
    verified_doctor(client, False)


def test_only_the_models_own_errors_and_timeouts_count_as_failures(client):
    passing("lc-e1"); release("lc-e1")
    registry.invalidate(); reset_engine()
    eng = get_engine()
    calls = {"n": 0}

    def handler(payload, ctx):
        calls["n"] += 1
        if payload.get("mode") == "reject":
            raise RejectedInput("bad input")
        if payload.get("mode") == "boom":
            raise RuntimeError("model failed")
        return HandlerResult("abstained" if payload.get("mode") == "abstain" else "ok", {"x": 1}, "")
    eng.register(f"image.{TASK}", handler, None)
    user = {"id": "doctor1", "role": "doctor", "name": "D"}
    for mode in ("ok", "abstain", "reject", "ok"):
        eng.run(f"image.{TASK}", {"mode": mode}, user=user)
    assert [o for _, o in lc._outcomes[TASK]] == [True, True, True]                                  # an abstention is a normal run; rejected input is not a run of the model at all
    eng.run(f"image.{TASK}", {"mode": "boom"}, user=user)
    assert [o for _, o in lc._outcomes[TASK]][-1] is False
    verified_doctor(client, False)


# ---------------------------------------------------------------- the administrator API
def test_the_administrator_api_walks_a_model_through_its_life_and_refuses_everyone_else(client):
    a, p, d = tok(client, "admin1"), tok(client, "patient1"), tok(client, "doctor1")
    passing("lc-api")
    verified_doctor(client)
    for path in ("/api/admin/models/lc-api/lifecycle", "/api/admin/models/lc-api/exposure", f"/api/admin/tasks/{TASK}/monitor"):
        assert client.get(path, headers=a).status_code == 200
        assert client.get(path, headers=p).status_code == 403 and client.get(path, headers=d).status_code == 403 and client.get(path).status_code == 401
    assert client.get("/api/admin/models/nope/lifecycle", headers=a).status_code == 404
    v = client.get("/api/admin/models/lc-api/lifecycle", headers=a).json()
    assert v["state"] == "experimental" and v["allowed_next_states"] == ["deprecated", "evaluation_pending"] and v["artifact"]["verifies"] is True
    assert client.post("/api/admin/models/lc-api/evaluate", headers=a).json()["state"] == "review_pending"
    refused = client.post("/api/admin/models/lc-api/approval", json={"intended_use": "x", "requirements": FULL}, headers=a).json()
    assert refused["effective_status"] == "research_only"
    ok = client.post("/api/admin/models/lc-api/approval", json={"intended_use": "Fixture", "requirements": FULL, "clinical_signoff_by": "doctor1"}, headers=a).json()
    assert ok["effective_status"] == "approved_for_defined_use"
    assert client.post("/api/admin/models/lc-api/deploy", headers=p).status_code == 403
    assert client.post("/api/admin/models/lc-api/deploy", headers=a).json()["active"] == "lc-api"
    issue = client.post("/api/admin/models/lc-api/clinical-issue", json={"description": "a reviewer saw a wrong output"}, headers=a).json()
    assert issue["clinical_issue_open"] is True
    assert client.post("/api/admin/models/lc-api/clinical-issue", json={"description": "x"}, headers=a).status_code == 422
    rb = client.post(f"/api/admin/tasks/{TASK}/rollback", json={"reason": "latency"}, headers=a).json()
    assert rb["active"] is None and rb["kind"] == "technical" and rb["rolled_back"] == "lc-api" and "does not change or correct" in rb["note"]
    assert client.post(f"/api/admin/tasks/{TASK}/rollback", headers=a).status_code == 200                       # nothing active: harmless
    assert client.post("/api/admin/models/lc-api/clinical-issue/close", json={"reason": "reviewed"}, headers=a).status_code == 200
    assert client.post("/api/admin/models/lc-api/deprecate", json={"reason": "obsolete"}, headers=a).json()["state"] == "deprecated"
    ev = client.get("/api/admin/models/lc-api/lifecycle", headers=a).json()["events"]
    assert [e["event"] for e in ev][:5] == ["evaluation_started", "review_requested", "approval_refused", "approved", "activated"]
    verified_doctor(client, False)


def test_importing_through_the_api_is_confined_to_the_model_directory_and_known_tasks(client):
    a = tok(client, "admin1")
    body = {"task": TASK, "artifact_path": "../../etc/passwd", "architecture": "resnet18", "source": "a published checkpoint"}
    outside = Path(os.environ["COSMO_DATA_DIR"]) / "outside.bin"
    outside.write_bytes(b"x")
    for path in ("../../etc/passwd", str(outside)):
        r = client.post("/api/admin/model-imports", json={**body, "artifact_path": path}, headers=a)
        assert r.status_code == 422 and "model directory" in r.json()["detail"], r.text
    r = client.post("/api/admin/model-imports", json={**body, "task": "not_a_task", "artifact_path": "x"}, headers=a)
    assert r.status_code == 422 and r.json()["detail"] == "Unknown task."
    f = MODEL_DIR / "imports"; f.mkdir(parents=True, exist_ok=True); art = f / "api.bin"; art.write_bytes(b"w")
    r = client.post("/api/admin/model-imports", json={**body, "artifact_path": str(art)}, headers=a)
    assert r.status_code == 200 and r.json()["state"] == "imported"
    MADE.append(r.json()["model_id"])
    assert client.post("/api/admin/model-imports", json={**body, "artifact_path": str(art)}, headers=tok(client, "doctor1")).status_code == 403


def test_events_keep_their_order_when_the_clock_does_not_advance(client, monkeypatch):
    """Two events of one operation can fall into the same tick of the system clock; the history is read back ordered by time and must still show them in the order they happened."""
    frozen = db.now()
    monkeypatch.setattr(db, "now", lambda: frozen)
    monkeypatch.setattr(lc, "_last_event_at", 0.0)
    for i in range(8):
        lc._event("lc-same-tick", TASK, "admin1", f"step{i}", None, None)
    assert [e["event"] for e in lc.events("lc-same-tick")] == [f"step{i}" for i in range(8)]

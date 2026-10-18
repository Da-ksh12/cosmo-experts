"""Privacy notice and consent: draft never offered in production, approved notice hash-bound, acceptance recorded per notice version, enforcement on data-collecting routes."""
import json

import pytest
from fastapi.testclient import TestClient

from app import clinical_content as cc, consent, db, prod_checks
from app.main import app
from tests.test_api import lab_pdf

TEXT = "# Privacy notice (test fixture)\n\nThis is a synthetic notice used only by the tests."


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def content_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CONTENT_DIR", tmp_path)
    yield tmp_path


def H(client, u="patient2"):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


def approved(dirpath, text=TEXT, **over):
    ap = {"reviewer_name": "TEST FIXTURE (not a lawyer)", "reviewer_role": "test", "registration_number": "T-0", "approved_on": "2000-01-01",
          "statement": "Synthetic file for unit tests.", "content_sha256": consent._sha(text)}
    ap.update(over)
    (dirpath / "consent_notice.approved.json").write_text(json.dumps({"text": text, "approval": ap}), encoding="utf-8")


# ---------------------------------------------------------------- which notice is offered
def test_the_shipped_draft_is_marked_draft_and_not_usable_in_production(monkeypatch):
    n = consent.notice()
    assert n["status"] == "draft" and "DRAFT" in n["text"].splitlines()[0] and len(n["sha256"]) == 64
    monkeypatch.setenv("COSMO_MODE", "production")
    assert consent.usable() is False
    monkeypatch.setenv("COSMO_MODE", "local")
    assert consent.usable() is True


def test_an_approved_notice_replaces_the_draft_and_a_tampered_one_is_ignored(content_dir):
    (content_dir / "consent_notice.draft.md").write_text("# DRAFT\n\ndraft text")
    assert consent.notice()["status"] == "draft"
    approved(content_dir)
    n = consent.notice()
    assert n["status"] == "approved" and n["text"] == TEXT and "TEST FIXTURE" in n["approved_by"]
    for bad in ({"content_sha256": "0" * 64}, {"reviewer_name": ""}, {"statement": " "}):
        approved(content_dir, **bad)
        assert consent.notice()["status"] == "draft", bad
    (content_dir / "consent_notice.approved.json").write_text("{broken")
    assert consent.notice()["status"] == "draft"
    (content_dir / "consent_notice.draft.md").unlink()
    assert consent.notice()["status"] == "none"


def test_production_startup_requires_an_approved_notice(content_dir, monkeypatch):
    (content_dir / "consent_notice.draft.md").write_text("# DRAFT\n\nx")
    assert any("privacy notice" in p for p in prod_checks.consent_problems({}))
    assert prod_checks.consent_problems({"COSMO_ALLOW_NO_CONSENT": "1"}) == []
    approved(content_dir)
    assert prod_checks.consent_problems({}) == []


# ---------------------------------------------------------------- enforcement
def test_enforcement_is_off_by_default_and_on_in_production_or_when_requested(monkeypatch):
    monkeypatch.delenv("COSMO_MODE", raising=False); monkeypatch.delenv("COSMO_REQUIRE_CONSENT", raising=False)
    assert consent.enforced() is False
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    assert consent.enforced() is True
    monkeypatch.delenv("COSMO_REQUIRE_CONSENT"); monkeypatch.setenv("COSMO_MODE", "production")
    assert consent.enforced() is True


def test_patients_cannot_collect_or_submit_data_until_they_accept_and_acceptance_is_per_notice_version(client, content_dir, monkeypatch):
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    db.x("DELETE FROM consents")
    approved(content_dir)
    h = H(client)
    st = client.get("/api/consent", headers=h).json()
    assert st["required"] is True and st["notice"]["status"] == "approved" and st["notice"]["text"] == TEXT
    pdf = lab_pdf(["Ferritin     8     ng/mL     15 - 150  L"])
    for call in (lambda: client.post("/api/documents", files={"file": ("a.pdf", pdf)}, headers=h),
                 lambda: client.post("/api/conversations", headers=h),
                 lambda: client.post("/api/images", data={"region": "face"}, files={"file": ("p.jpg", b"x")}, headers=h),
                 lambda: client.post("/api/quotes", json={"items": []}, headers=h),
                 lambda: client.post("/api/v1/documents", files={"file": ("a.pdf", pdf)}, headers=h)):
        r = call()
        assert r.status_code == 403 and r.json()["detail"]["code"] == "consent_required", r.text
    assert client.get("/api/me", headers=h).status_code == 200                              # reading one's own profile/consent screen is never blocked
    assert client.get("/api/documents", headers=h).status_code == 200

    assert client.post("/api/consent/accept", json={"notice_sha256": "wrong"}, headers=h).status_code == 409
    ok = client.post("/api/consent/accept", json={"notice_sha256": st["notice"]["sha256"]}, headers=h)
    assert ok.status_code == 200 and ok.json()["required"] is False and ok.json()["accepted"] is True
    assert client.post("/api/conversations", headers=h).status_code == 200
    assert db.q1("SELECT 1 x FROM audit WHERE action='consent.accept' AND actor='patient2'")
    assert client.post("/api/consent/accept", json={"notice_sha256": st["notice"]["sha256"]}, headers=h).status_code == 200      # idempotent
    assert db.q1("SELECT COUNT(*) c FROM consents WHERE user_id='patient2'")["c"] == 1

    approved(content_dir, text=TEXT + "\n\nA new paragraph.")                                 # the text changed: everyone is asked again
    assert client.get("/api/consent", headers=h).json()["required"] is True
    assert client.post("/api/conversations", headers=h).status_code == 403
    assert client.get("/api/consent", headers=H(client, "patient1")).json()["required"] is True    # another patient never inherits acceptance


def test_staff_are_not_subject_to_the_patient_consent_gate_and_cannot_accept_for_patients(client, content_dir, monkeypatch):
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    approved(content_dir)
    d = H(client, "doctor1")
    assert client.get("/api/consent", headers=d).json()["required"] is False
    assert client.get("/api/clinic/kb", headers=d).status_code == 200
    assert client.post("/api/consent/accept", json={"notice_sha256": consent.notice()["sha256"]}, headers=d).status_code == 403
    assert client.post("/api/conversations", headers=d).status_code == 403                  # still a patient-only route


def test_a_draft_cannot_be_accepted_in_production(client, content_dir, monkeypatch):
    monkeypatch.setenv("COSMO_MODE", "production")
    (content_dir / "consent_notice.draft.md").write_text("# DRAFT\n\nx")
    h = H(client)
    r = client.post("/api/consent/accept", json={"notice_sha256": consent.notice()["sha256"]}, headers=h)
    assert r.status_code == 409


def test_notice_packaging_tool_refuses_drafts_and_unconfirmed_runs(tmp_path):
    from scripts import make_approved_notice as mk
    draft = tmp_path / "n.md"; draft.write_text("# DRAFT: x\n\ntext")
    out = tmp_path / "o.json"
    base = [str(draft), "--name", "N", "--role", "R", "--registration", "1", "--date", "2000-01-01", "--out", str(out)]
    assert mk.main(base) == 2 and not out.exists()
    assert mk.main(base + ["--confirm-reviewer-approved"]) == 2 and not out.exists()        # still marked DRAFT
    final = tmp_path / "f.md"; final.write_text(TEXT)
    base[0] = str(final)
    assert mk.main(base + ["--confirm-reviewer-approved"]) == 0
    doc = json.loads(out.read_text())
    assert doc["approval"]["content_sha256"] == consent._sha(TEXT) and doc["text"] == TEXT


# ---------------------------------------------------------------- history, withdrawal, visible state
def _accept(client, h):
    st = client.get("/api/consent", headers=h).json()
    return client.post("/api/consent/accept", json={"notice_sha256": st["notice"]["sha256"]}, headers=h)


def test_every_decision_is_kept_as_an_immutable_event_bound_to_the_notice_hash_and_its_approval_status(client, content_dir, monkeypatch):
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    approved(content_dir)
    h, uid = H(client, "patient1"), "patient1"
    assert _accept(client, h).json()["accepted"] is True
    assert _accept(client, h).status_code == 200                                              # idempotent: no duplicate event
    ev = client.get("/api/consent/history", headers=h).json()["events"]
    assert [e["action"] for e in ev][:1] == ["accepted"] and ev[0]["notice_sha256"] == consent.notice()["sha256"] and ev[0]["notice_status"] == "approved"
    assert sum(1 for e in ev if e["action"] == "accepted" and e["notice_sha256"] == consent.notice()["sha256"]) == 1
    assert "text" not in ev[0]
    with pytest.raises(Exception):
        db.x("UPDATE consent_events SET action='accepted' WHERE user_id=?", (uid,))            # history cannot be rewritten
    assert client.get("/api/consent/history", headers=H(client, "doctor1")).status_code == 403    # only the patient reads their own history
    assert client.get("/api/consent/history").status_code == 401


def test_withdrawal_closes_the_gate_immediately_is_recorded_and_can_be_followed_by_a_fresh_acceptance(client, content_dir, monkeypatch):
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    approved(content_dir, text=TEXT + "\n\nwithdrawal fixture")
    h = H(client, "patient2")
    _accept(client, h)
    assert client.post("/api/conversations", headers=h).status_code == 200
    w = client.post("/api/consent/withdraw", headers=h)
    assert w.status_code == 200 and w.json()["accepted"] is False and w.json()["required"] is True and w.json()["withdrawn"] is True
    assert client.post("/api/conversations", headers=h).status_code == 403                       # new data collection stops at once
    assert client.get("/api/documents", headers=h).status_code == 200 and client.get("/api/me", headers=h).status_code == 200    # the person can still see and export what exists
    assert client.post("/api/consent/withdraw", headers=h).status_code == 200                    # idempotent
    acts = [e["action"] for e in client.get("/api/consent/history", headers=h).json()["events"]]
    assert acts[:2] == ["withdrawn", "accepted"] and acts.count("withdrawn") == 1
    assert db.q1("SELECT 1 x FROM audit WHERE action='consent.withdraw' AND actor='patient2'")
    assert _accept(client, h).json()["accepted"] is True and client.post("/api/conversations", headers=h).status_code == 200
    assert client.post("/api/consent/withdraw", headers=H(client, "doctor1")).status_code == 403    # staff cannot withdraw for a patient


def test_a_patient_cannot_withdraw_or_accept_on_anothers_behalf_and_the_state_is_per_person(client, content_dir, monkeypatch):
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    approved(content_dir, text=TEXT + "\n\nper-person fixture")
    a, b = H(client, "patient1"), H(client, "patient2")
    _accept(client, a); _accept(client, b)
    client.post("/api/consent/withdraw", headers=a)
    assert client.get("/api/consent", headers=b).json()["accepted"] is True


def test_an_unapproved_draft_is_recorded_as_a_draft_and_never_as_approved(client, content_dir, monkeypatch):
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    (content_dir / "consent_notice.draft.md").write_text("# DRAFT\n\nunapproved fixture text")
    h = H(client, "patient1")
    r = client.post("/api/consent/accept", json={"notice_sha256": consent.notice()["sha256"], "notice_status": "approved"}, headers=h)    # a client-supplied status is ignored
    assert r.status_code == 200
    last = db.q1("SELECT notice_status FROM consent_events WHERE user_id='patient1' ORDER BY at DESC LIMIT 1")
    assert last["notice_status"] == "draft"
    with pytest.raises(Exception):
        db.x("INSERT INTO consent_events(id,user_id,notice_sha256,notice_status,action,at) VALUES('x','patient1','h','definitive','accepted',1)")   # no other status is representable


def test_consent_never_covers_model_training(client, content_dir, monkeypatch):
    monkeypatch.setenv("COSMO_REQUIRE_CONSENT", "1")
    approved(content_dir)
    assert client.get("/api/consent", headers=H(client, "patient1")).json()["not_covered"] == ["model_training"]

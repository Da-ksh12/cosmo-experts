"""Administrator user management: creation, role changes, disabling, password reset, lock-out protection, audit, and the rule that an administrator gains no clinical authority."""
import json

import pytest
from fastapi.testclient import TestClient

from app import db, security
from app.main import app

PW = "correct-horse-battery"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(client, user, pw="cosmo-dev"):
    r = client.post("/api/auth/login", json={"user_id": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture(autouse=True)
def clean_users(client):
    yield
    db.x("DELETE FROM users WHERE id LIKE 'ut-%'")
    db.x("UPDATE users SET disabled=0 WHERE id IN ('patient1','patient2','doctor1','admin1')")


def create(client, h, uid="ut-nurse", role="doctor", pw=PW):
    return client.post("/api/admin/users", json={"id": uid, "name": "Test User", "role": role, "password": pw}, headers=h)


# ---------------------------------------------------------------- hashing
def test_new_passwords_are_salted_and_legacy_hashes_still_verify():
    a, b = security.new_hash("same-password-123"), security.new_hash("same-password-123")
    assert a != b and a.startswith("pbkdf2$") and security.verify_pw("same-password-123", a) and not security.verify_pw("other", a)
    assert security.verify_pw("cosmo-dev", security.hash_pw("cosmo-dev")) and not security.verify_pw("x", security.hash_pw("cosmo-dev"))
    assert not security.verify_pw("anything", "") and not security.verify_pw("x", "pbkdf2$zz$zz")


# ---------------------------------------------------------------- permissions
def test_only_administrators_can_manage_users(client):
    for who in ("patient1", "doctor1"):
        h = H(client, who)
        assert client.get("/api/admin/users", headers=h).status_code == 403
        assert create(client, h).status_code == 403
        assert client.post("/api/admin/users/patient2/role", json={"role": "admin"}, headers=h).status_code == 403
        assert client.post("/api/admin/users/patient2/disabled", json={"disabled": True}, headers=h).status_code == 403
        assert client.post("/api/admin/users/patient2/reset-password", json={"password": PW}, headers=h).status_code == 403
    assert client.get("/api/admin/users").status_code == 401


def test_list_never_exposes_password_hashes(client):
    rows = client.get("/api/admin/users", headers=H(client, "admin1")).json()
    assert {"patient1", "doctor1", "admin1"} <= {r["id"] for r in rows}
    assert not any("password" in k for r in rows for k in r) and "pbkdf2" not in json.dumps(rows)


# ---------------------------------------------------------------- create
def test_create_user_validates_input_and_the_new_user_can_sign_in(client):
    a = H(client, "admin1")
    assert create(client, a, "ut-nurse", "doctor").status_code == 200
    assert create(client, a, "ut-nurse", "doctor").status_code == 409                       # duplicate id
    for bad in ({"uid": "AB", "role": "doctor", "pw": PW}, {"uid": "ut bad id", "role": "doctor", "pw": PW}, {"uid": "ut-x", "role": "superuser", "pw": PW},
                {"uid": "ut-y", "role": "patient", "pw": "short"}, {"uid": "../etc", "role": "patient", "pw": PW}):
        assert create(client, a, bad["uid"], bad["role"], bad["pw"]).status_code == 422, bad
    h = H(client, "ut-nurse", PW)
    assert client.get("/api/me", headers=h).json()["role"] == "doctor"
    assert client.post("/api/auth/login", json={"user_id": "ut-nurse", "password": "cosmo-dev"}).status_code == 401
    assert db.q1("SELECT verified FROM users WHERE id='ut-nurse'")["verified"] == 0          # created clinicians are unverified until an administrator records credentials
    assert db.q1("SELECT 1 x FROM audit WHERE action='user.create' AND target='ut-nurse'")
    assert PW not in json.dumps(db.q("SELECT * FROM audit"))


# ---------------------------------------------------------------- roles
def test_role_change_resets_clinical_verification_and_takes_effect_immediately(client):
    a = H(client, "admin1")
    create(client, a, "ut-doc", "doctor")
    assert client.post("/api/admin/users/ut-doc/verify-clinician", json={"credentials": "MD (test fixture)", "verified": True}, headers=a).status_code == 200
    old = H(client, "ut-doc", PW)
    assert client.get("/api/clinic/kb", headers=old).status_code == 200
    assert client.post("/api/admin/users/ut-doc/role", json={"role": "patient"}, headers=a).status_code == 200
    assert client.get("/api/clinic/kb", headers=old).status_code == 403                    # the existing token loses clinical access at once
    row = db.q1("SELECT role,verified,credentials FROM users WHERE id='ut-doc'")
    assert row == {"role": "patient", "verified": 0, "credentials": ""} or dict(row) == {"role": "patient", "verified": 0, "credentials": ""}
    assert client.post("/api/admin/users/ut-doc/role", json={"role": "doctor"}, headers=a).status_code == 200
    assert db.q1("SELECT verified FROM users WHERE id='ut-doc'")["verified"] == 0          # promotion does not resurrect the old verification
    assert client.post("/api/admin/users/nobody/role", json={"role": "doctor"}, headers=a).status_code == 404
    assert client.post("/api/admin/users/ut-doc/role", json={"role": "root"}, headers=a).status_code == 422


def test_an_administrator_gains_no_clinical_authority(client):
    a = H(client, "admin1")
    kb = client.get("/api/clinic/kb", headers=a).json()[0]["id"]
    assert client.post(f"/api/clinic/kb/{kb}/approve", json={"attest": True, "sources_checked": "an administrator tries"}, headers=a).status_code == 403
    assert client.post("/api/clinic/care-plans/patient1", json={}, headers=a).status_code in (403, 405, 422)


def test_cannot_change_or_disable_your_own_account(client):
    a = H(client, "admin1")
    assert client.post("/api/admin/users/admin1/role", json={"role": "patient"}, headers=a).status_code == 409
    assert client.post("/api/admin/users/admin1/disabled", json={"disabled": True}, headers=a).status_code == 409
    assert db.q1("SELECT role,disabled FROM users WHERE id='admin1'")["role"] == "admin"


def test_a_second_administrator_can_act_but_the_last_active_one_is_protected(client):
    from app.routers import care
    a = H(client, "admin1")
    assert care._active_admins("admin1") == 0                                               # with only admin1 active, nobody else could keep the system administrable
    create(client, a, "ut-admin2", "admin")
    assert care._active_admins("admin1") == 1
    b = H(client, "ut-admin2", PW)
    assert client.post("/api/admin/users/admin1/disabled", json={"disabled": True}, headers=b).status_code == 200      # allowed: ut-admin2 remains active
    assert client.post("/api/admin/users/ut-admin2/disabled", json={"disabled": True}, headers=b).status_code == 409   # cannot disable yourself
    assert client.post("/api/admin/users/ut-admin2/role", json={"role": "patient"}, headers=b).status_code == 409      # nor demote yourself
    assert H(client, "ut-admin2", PW) and client.get("/api/admin/users", headers=b).status_code == 200


# ---------------------------------------------------------------- disabling
def test_disabling_blocks_existing_tokens_and_new_sign_ins_and_is_reversible(client):
    a = H(client, "admin1")
    create(client, a, "ut-pat", "patient")
    tok = H(client, "ut-pat", PW)
    assert client.get("/api/me", headers=tok).status_code == 200
    assert client.post("/api/admin/users/ut-pat/disabled", json={"disabled": True}, headers=a).status_code == 200
    assert client.get("/api/me", headers=tok).status_code == 403                            # already-issued token stops working
    r = client.post("/api/auth/login", json={"user_id": "ut-pat", "password": PW})
    assert r.status_code == 403 and "disabled" in r.json()["detail"].lower()
    assert db.q1("SELECT 1 x FROM audit WHERE action='auth.refused_disabled' AND actor='ut-pat'")
    assert client.post("/api/admin/users/ut-pat/disabled", json={"disabled": False}, headers=a).status_code == 200
    assert client.get("/api/me", headers=tok).status_code == 200
    assert client.post("/api/admin/users/nobody/disabled", json={"disabled": True}, headers=a).status_code == 404


def test_password_reset_replaces_the_password_and_is_audited_without_it(client):
    a = H(client, "admin1")
    create(client, a, "ut-reset", "patient")
    assert client.post("/api/admin/users/ut-reset/reset-password", json={"password": "short"}, headers=a).status_code == 422
    assert client.post("/api/admin/users/ut-reset/reset-password", json={"password": "a-brand-new-password"}, headers=a).status_code == 200
    assert client.post("/api/auth/login", json={"user_id": "ut-reset", "password": PW}).status_code == 401
    assert client.post("/api/auth/login", json={"user_id": "ut-reset", "password": "a-brand-new-password"}).status_code == 200
    assert "a-brand-new-password" not in json.dumps(db.q("SELECT * FROM audit"))
    assert client.post("/api/admin/users/nobody/reset-password", json={"password": PW}, headers=a).status_code == 404


def test_management_is_refused_when_accounts_live_in_supabase(monkeypatch):
    from fastapi import HTTPException
    from app.routers import care
    monkeypatch.setenv("COSMO_AUTH", "supabase")
    with pytest.raises(HTTPException) as e:
        care._local_only()
    assert e.value.status_code == 409 and "Supabase" in e.value.detail

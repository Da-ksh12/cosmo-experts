"""Supabase Auth account lifecycle against a protocol-level fake (tests/fake_supabase.py) that signs real ES256 tokens and serves a real JWKS.
No e-mail is sent and no real project is touched."""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import db, ratelimit, supabase_auth
from app.main import app
from tests.fake_supabase import ANON, SERVICE, FakeSupabase

PW = "correct-horse-battery-staple"
CSRF = {"X-Requested-With": "cosmo-web"}


@pytest.fixture(scope="module")
def fake():
    f = FakeSupabase()
    yield f
    f.close()


@pytest.fixture
def sb(fake, monkeypatch):
    for k, v in {"COSMO_AUTH": "supabase", "SUPABASE_URL": fake.url, "SUPABASE_ANON_KEY": ANON, "SUPABASE_SERVICE_ROLE_KEY": SERVICE, "COSMO_COOKIE_SECURE": "0",
                 "COSMO_ALLOW_REGISTRATION": "1", "COSMO_CORS": "http://localhost:3000"}.items():
        monkeypatch.setenv(k, v)
    supabase_auth._verifier = None
    supabase_auth._profiles.clear()
    ratelimit.get().reset()
    fake.users.clear(); fake.refresh.clear(); fake.call_log.clear()
    fake.down = fake.rate_limited = fake.confirm_required = False
    with TestClient(app) as c:
        yield c, fake
    db.x("DELETE FROM users WHERE password_hash='' AND id NOT IN ('patient1','patient2','doctor1','admin1')")


def login(c, email, pw=PW):
    return c.post("/api/auth/login", json={"user_id": email, "password": pw})


def bearer(r):
    return {"Authorization": "Bearer " + r.json()["token"]}


# ---------------------------------------------------------------- configuration
def test_config_tells_the_sign_in_screen_which_form_to_show(sb, monkeypatch):
    c, _ = sb
    assert c.get("/api/auth/config").json() == {"mode": "supabase", "registration": True, "password_min": 12}
    monkeypatch.delenv("COSMO_ALLOW_REGISTRATION")
    assert c.get("/api/auth/config").json()["registration"] is False                             # registration is closed unless explicitly opened
    monkeypatch.setenv("COSMO_AUTH", "local")
    assert c.get("/api/auth/config").json() == {"mode": "local", "registration": False, "password_min": 12}
    assert c.post("/api/auth/refresh", headers=CSRF).status_code == 404                           # the Supabase endpoints do not exist in local mode
    assert c.post("/api/auth/register", json={"email": "a@b.co", "password": PW, "full_name": "A"}).status_code == 404


# ---------------------------------------------------------------- sign-in
def test_signin_returns_an_access_token_keeps_the_refresh_token_in_an_httponly_cookie_and_takes_the_role_from_the_profile(sb):
    c, f = sb
    uid = f.add_user("doc@example.org", PW, role="doctor", full_name="Dr Rao")
    r = login(c, "  Doc@Example.org ")                                                          # normalised
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"] == {"id": uid, "name": "Dr Rao", "role": "doctor"} and body["expires_in"] == 3600
    assert "refresh" not in json.dumps(body).lower() and not any(k.startswith("rt-") for k in body.values() if isinstance(k, str))
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("cosmo_rt=rt-") and "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api/auth" in cookie and "Secure" not in cookie
    assert c.get("/api/clinic/kb", headers=bearer(r)).status_code == 200                           # profile role doctor, even though the token claims admin
    assert c.get("/api/admin/users", headers=bearer(r)).status_code == 403                          # hostile role claims inside the JWT are ignored
    assert db.q1("SELECT role, password_hash FROM users WHERE id=?", (uid,)) == {"role": "doctor", "password_hash": ""}
    pw_call = f.calls("/auth/v1/token")[0]
    assert pw_call["apikey"] == ANON and pw_call["query"] == {"grant_type": "password"}               # the public key, never the service key


def test_the_secure_cookie_flag_follows_the_setting(sb, monkeypatch):
    c, f = sb
    f.add_user("s@example.org", PW)
    monkeypatch.setenv("COSMO_COOKIE_SECURE", "1")
    assert "Secure" in login(c, "s@example.org").headers["set-cookie"]


def test_failures_are_indistinguishable_and_never_reveal_whether_an_account_exists(sb):
    c, f = sb
    f.add_user("real@example.org", PW)
    a, b = login(c, "real@example.org", "wrong-password-123"), login(c, "nobody@example.org", "wrong-password-123")
    assert a.status_code == b.status_code == 401 and a.json() == b.json() == {"detail": "Incorrect email or password."}
    assert login(c, "not-an-email", PW).json() == a.json() and login(c, "", "").json() == a.json() and login(c, "x@y.zz", "p" * 300).status_code == 401
    assert "set-cookie" not in a.headers


def test_an_account_is_locked_after_five_failures_even_for_the_right_password_and_unlocks_on_success_after_the_window(sb):
    c, f = sb
    f.add_user("lock@example.org", PW)
    for _ in range(5):
        assert login(c, "lock@example.org", "bad-password-000").status_code == 401
    r = login(c, "lock@example.org", PW)
    assert r.status_code == 429 and r.headers["retry-after"] == "900"
    assert len(f.calls("/auth/v1/token")) == 5                                                       # the locked attempt never reached the provider
    ratelimit.get().reset()                                                                           # (the window elapsing)
    assert login(c, "lock@example.org", PW).status_code == 200
    for _ in range(4):
        login(c, "lock@example.org", "bad-password-000")
    assert login(c, "lock@example.org", PW).status_code == 200                                        # a success clears the counter


def test_lockout_is_per_account_not_global(sb):
    c, f = sb
    f.add_user("a@example.org", PW); f.add_user("b@example.org", PW)
    for _ in range(5):
        login(c, "a@example.org", "bad-password-000")
    assert login(c, "b@example.org", PW).status_code == 200


def test_unconfirmed_accounts_are_told_to_confirm_and_get_no_session(sb):
    c, f = sb
    f.add_user("new@example.org", PW, confirmed=False)
    r = login(c, "new@example.org")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "email_not_confirmed" and "set-cookie" not in r.headers


def test_provider_outage_and_rate_limits_give_safe_generic_answers(sb):
    c, f = sb
    f.add_user("o@example.org", PW)
    f.down = True
    r = login(c, "o@example.org")
    assert r.status_code == 503 and r.json() == {"detail": "Sign-in is temporarily unavailable."}
    f.down = False; f.rate_limited = True
    assert login(c, "o@example.org").status_code == 429
    f.rate_limited = False
    assert c.get("/api/me").status_code == 401 and c.get("/api/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_an_account_without_a_provisioned_profile_gets_no_access(sb, monkeypatch):
    c, f = sb
    uid = f.add_user("orphan@example.org", PW)
    f.users[uid]["role"] = "superuser"                                                                # a profile with an unknown role is not a valid account
    assert login(c, "orphan@example.org").status_code == 401


# ---------------------------------------------------------------- tokens: expiry, tampering, claims the client controls
def test_expired_tampered_and_foreign_tokens_are_refused(sb):
    c, f = sb
    uid = f.add_user("t@example.org", PW)
    good = f.access_token(uid)
    assert c.get("/api/me", headers={"Authorization": "Bearer " + good}).status_code == 200
    assert c.get("/api/me", headers={"Authorization": "Bearer " + f.access_token(uid, ttl=-60)}).status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer " + good[:-4] + "AAAA"}).status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer " + f.access_token(uid, aud="other")}).status_code == 401
    assert c.get("/api/me", headers={"Authorization": "Bearer " + f.access_token(uid, iss="https://evil.example/auth/v1")}).status_code == 401
    other = FakeSupabase()                                                                              # a token signed by someone else's key
    try:
        ouid = other.add_user("t@example.org", PW)
        assert c.get("/api/me", headers={"Authorization": "Bearer " + other.access_token(ouid)}).status_code == 401
    finally:
        other.close()


# ---------------------------------------------------------------- refresh and sign-out
def test_refresh_rotates_the_cookie_and_requires_the_custom_header_a_cookie_and_a_trusted_origin(sb):
    c, f = sb
    f.add_user("r@example.org", PW)
    r = login(c, "r@example.org")
    first = c.cookies.get("cosmo_rt")
    assert c.post("/api/auth/refresh").status_code == 403                                             # no X-Requested-With: a cross-site form post cannot do this
    assert c.post("/api/auth/refresh", headers={**CSRF, "Origin": "https://evil.example"}).status_code == 403
    ok = c.post("/api/auth/refresh", headers={**CSRF, "Origin": "http://localhost:3000"})
    assert ok.status_code == 200 and ok.json()["token"] != r.json()["token"] and c.cookies.get("cosmo_rt") != first
    assert c.get("/api/me", headers=bearer(ok)).status_code == 200
    old = TestClient(app)                                                                                # replaying the ROTATED (used) refresh token fails and clears the cookie
    old.cookies.set("cosmo_rt", first, path="/api/auth")
    gone = old.post("/api/auth/refresh", headers=CSRF)
    assert gone.status_code == 401 and "cosmo_rt=" in gone.headers["set-cookie"] and "Max-Age=0" in gone.headers["set-cookie"]
    assert TestClient(app).post("/api/auth/refresh", headers=CSRF).status_code == 401                 # no cookie at all


def test_refresh_is_revoked_for_a_disabled_account(sb):
    c, f = sb
    uid = f.add_user("d@example.org", PW)
    login(c, "d@example.org")
    db.x("UPDATE users SET disabled=1 WHERE id=?", (uid,))
    r = c.post("/api/auth/refresh", headers=CSRF)
    assert r.status_code == 403 and "Max-Age=0" in r.headers["set-cookie"]
    assert any(x["query"].get("scope") == "global" for x in f.calls("/auth/v1/logout"))                # the new session was revoked at the provider


def test_signout_revokes_at_the_provider_clears_the_cookie_and_needs_the_header(sb):
    c, f = sb
    f.add_user("o@example.org", PW)
    r = login(c, "o@example.org")
    assert c.post("/api/auth/logout", headers=bearer(r)).status_code == 403
    out = c.post("/api/auth/logout", headers={**bearer(r), **CSRF})
    assert out.status_code == 200 and "Max-Age=0" in out.headers["set-cookie"]
    call = f.calls("/auth/v1/logout")[-1]
    assert call["bearer"] == r.json()["token"] and call["apikey"] == ANON
    assert c.post("/api/auth/logout", headers={"Authorization": "Bearer expired.token.value", **CSRF}).status_code == 200      # signing out always succeeds locally
    f.down = True
    assert c.post("/api/auth/logout", headers={**bearer(r), **CSRF}).status_code == 200


# ---------------------------------------------------------------- account disablement
def test_a_disabled_account_is_refused_everywhere_at_once_and_can_be_restored(sb):
    c, f = sb
    uid = f.add_user("x@example.org", PW)
    r = login(c, "x@example.org")
    h = bearer(r)
    assert c.get("/api/me", headers=h).status_code == 200
    db.x("UPDATE users SET disabled=1 WHERE id=?", (uid,))
    assert c.get("/api/me", headers=h).status_code == 403                                             # the already-issued access token stops working immediately
    again = login(c, "x@example.org")
    assert again.status_code == 403 and "set-cookie" not in again.headers
    db.x("UPDATE users SET disabled=0 WHERE id=?", (uid,))
    assert c.get("/api/me", headers=h).status_code == 200


# ---------------------------------------------------------------- registration
def test_registration_responds_identically_for_new_existing_and_failing_addresses(sb):
    c, f = sb
    f.add_user("taken@example.org", PW)
    body = lambda e: {"email": e, "password": PW, "full_name": "Asha Rao"}  # noqa: E731
    new, dup = c.post("/api/auth/register", json=body("fresh@example.org")), c.post("/api/auth/register", json=body("taken@example.org"))
    f.down = True
    down = c.post("/api/auth/register", json=body("third@example.org"))
    assert new.status_code == dup.status_code == down.status_code == 202 and new.json() == dup.json() == down.json()
    assert f.by_email("fresh@example.org")[1]["role"] == "patient" and f.by_email("fresh@example.org")[1]["confirmed"] is False


def test_registration_can_never_choose_a_role_and_sends_only_the_name_as_metadata(sb):
    c, f = sb
    r = c.post("/api/auth/register", json={"email": "evil@example.org", "password": PW, "full_name": "Eve", "role": "admin", "data": {"role": "admin"}, "app_metadata": {"role": "admin"}})
    assert r.status_code == 202
    call = f.calls("/auth/v1/signup")[0]
    assert call["body"]["data"] == {"full_name": "Eve"} and set(call["body"]) == {"email", "password", "data"}
    assert f.by_email("evil@example.org")[1]["role"] == "patient"


@pytest.mark.parametrize("payload,needle", [
    ({"email": "nope", "password": PW, "full_name": "A"}, "valid email"), ({"email": "a@b.example", "password": "short", "full_name": "A"}, "at least 12"),
    ({"email": "a@b.example", "password": "password1234", "full_name": "A"}, "too easy"), ({"email": "alice@b.example", "password": "xxalicexxyyzz", "full_name": "A"}, "too easy"),
    ({"email": "a@b.example", "password": "p" * 129, "full_name": "A"}, "at most 128"), ({"email": "a@b.example", "password": PW, "full_name": "  "}, "your name"),
    ({"email": "a@b.example", "password": PW, "full_name": "<script>alert(1)</script>"}, "your name"),
])
def test_registration_input_is_validated_before_anything_is_sent(sb, payload, needle):
    c, f = sb
    r = c.post("/api/auth/register", json=payload)
    assert r.status_code == 422 and needle in r.json()["detail"] and f.calls("/auth/v1/signup") == []


def test_registration_is_rate_limited_per_address_and_per_ip(sb):
    c, f = sb
    for _ in range(3):
        assert c.post("/api/auth/register", json={"email": "same@example.org", "password": PW, "full_name": "A"}).status_code == 202
    assert c.post("/api/auth/register", json={"email": "same@example.org", "password": PW, "full_name": "A"}).status_code == 429
    ratelimit.get().reset()
    codes = [c.post("/api/auth/register", json={"email": f"u{i}@example.org", "password": PW, "full_name": "A"}).status_code for i in range(7)]
    assert codes[:5] == [202] * 5 and codes[5:] == [429, 429]


def test_registration_is_closed_unless_explicitly_opened(sb, monkeypatch):
    c, f = sb
    monkeypatch.delenv("COSMO_ALLOW_REGISTRATION")
    assert c.post("/api/auth/register", json={"email": "a@b.example", "password": PW, "full_name": "A"}).status_code == 404 and f.calls("/auth/v1/signup") == []


def test_an_unverified_registrant_cannot_sign_in_until_confirmed(sb):
    c, f = sb
    c.post("/api/auth/register", json={"email": "unv@example.org", "password": PW, "full_name": "U"})
    assert login(c, "unv@example.org").status_code == 403
    uid, u = f.by_email("unv@example.org")
    u["confirmed"] = True
    assert login(c, "unv@example.org").status_code == 200 and db.q1("SELECT role FROM users WHERE id=?", (uid,))["role"] == "patient"


# ---------------------------------------------------------------- password reset
def test_reset_request_answers_identically_whatever_the_address_and_limits_redirects(sb):
    c, f = sb
    f.add_user("known@example.org", PW)
    answers = [c.post("/api/auth/password-reset/request", json={"email": e}) for e in ("known@example.org", "unknown@example.org", "garbage", "")]
    assert all(a.status_code == 202 for a in answers) and len({json.dumps(a.json()) for a in answers}) == 1
    assert len(f.calls("/auth/v1/recover")) == 2                                                       # only well-formed addresses reach the provider (known or not)
    c.post("/api/auth/password-reset/request", json={"email": "known@example.org", "redirect_to": "https://evil.example/steal"})
    c.post("/api/auth/password-reset/request", json={"email": "known@example.org", "redirect_to": "http://localhost:3000/login"})
    redirects = [x["query"].get("redirect_to") for x in f.calls("/auth/v1/recover")][2:]
    assert redirects == [None, "http://localhost:3000/login"]                                         # an off-site redirect target is dropped


def test_reset_request_is_rate_limited_without_changing_the_answer(sb):
    c, f = sb
    rs = [c.post("/api/auth/password-reset/request", json={"email": "known@example.org"}) for _ in range(6)]
    assert all(r.status_code == 202 for r in rs) and len(f.calls("/auth/v1/recover")) == 3


def test_reset_confirm_applies_the_new_password_revokes_other_sessions_and_rejects_bad_links(sb):
    c, f = sb
    uid = f.add_user("rec@example.org", PW)
    token = f.access_token(uid)
    ok = c.post("/api/auth/password-reset/confirm", json={"recovery_token": token, "new_password": "a-brand-new-passphrase"})
    assert ok.status_code == 200 and f.users[uid]["password"] == "a-brand-new-passphrase"
    assert f.calls("/auth/v1/logout")[-1]["query"]["scope"] == "global"
    assert login(c, "rec@example.org", "a-brand-new-passphrase").status_code == 200 and login(c, "rec@example.org", PW).status_code == 401
    for bad in ({"recovery_token": f.access_token(uid, ttl=-5), "new_password": "another-long-passphrase"}, {"recovery_token": "junk", "new_password": "another-long-passphrase"}):
        assert c.post("/api/auth/password-reset/confirm", json=bad).status_code == 400
    weak = c.post("/api/auth/password-reset/confirm", json={"recovery_token": f.access_token(uid), "new_password": "short"})
    assert weak.status_code == 422 and f.users[uid]["password"] == "a-brand-new-passphrase"


# ---------------------------------------------------------------- administrator actions in Supabase mode
def admin_headers(c, f):
    uid = f.add_user("root@example.org", PW, role="admin", full_name="Root")
    return uid, bearer(login(c, "root@example.org"))


def test_administrators_create_change_disable_and_reset_accounts_through_the_provider(sb):
    c, f = sb
    aid, h = admin_headers(c, f)
    made = c.post("/api/admin/users", json={"id": "Nurse@Example.org", "name": "N", "role": "doctor", "password": "a-fine-long-password"}, headers=h)
    assert made.status_code == 200, made.text
    uid = made.json()["id"]
    assert f.users[uid]["role"] == "doctor" and f.users[uid]["confirmed"] is True and f.calls("/auth/v1/admin/users")[0]["apikey"] == SERVICE
    assert c.post("/api/admin/users", json={"id": "nurse@example.org", "name": "N", "role": "doctor", "password": "a-fine-long-password"}, headers=h).status_code == 409
    assert c.post("/api/admin/users", json={"id": "bad", "name": "N", "role": "patient", "password": "a-fine-long-password"}, headers=h).status_code == 422
    assert c.post("/api/admin/users", json={"id": "w@example.org", "name": "N", "role": "patient", "password": "weakweakweak"}, headers=h).status_code == 422
    assert c.post(f"/api/admin/users/{uid}/role", json={"role": "patient"}, headers=h).status_code == 200 and f.users[uid]["role"] == "patient"
    assert c.post(f"/api/admin/users/{uid}/disabled", json={"disabled": True}, headers=h).status_code == 200
    assert f.users[uid]["banned"] is True and db.q1("SELECT disabled FROM users WHERE id=?", (uid,))["disabled"] == 1
    assert login(c, "nurse@example.org", "a-fine-long-password").status_code in (401, 403)
    assert c.post(f"/api/admin/users/{uid}/disabled", json={"disabled": False}, headers=h).status_code == 200 and f.users[uid]["banned"] is False
    assert c.post(f"/api/admin/users/{uid}/reset-password", json={"password": "yet-another-long-pass"}, headers=h).status_code == 200 and f.users[uid]["password"] == "yet-another-long-pass"
    assert c.post(f"/api/admin/users/{aid}/role", json={"role": "patient"}, headers=h).status_code == 409                         # lock-out guards still apply
    assert c.post(f"/api/admin/users/{aid}/disabled", json={"disabled": True}, headers=h).status_code == 409
    f.down = True
    r = c.post(f"/api/admin/users/{uid}/disabled", json={"disabled": True}, headers=h)
    assert r.status_code in (401, 502)                                                                                                  # provider failure never pretends success


def test_a_role_change_takes_effect_immediately_despite_the_profile_cache(sb):
    c, f = sb
    aid, h = admin_headers(c, f)
    uid = f.add_user("c@example.org", PW, role="doctor")
    t = bearer(login(c, "c@example.org"))
    assert c.get("/api/clinic/kb", headers=t).status_code == 200
    assert c.post(f"/api/admin/users/{uid}/role", json={"role": "patient"}, headers=h).status_code == 200
    assert c.get("/api/clinic/kb", headers=t).status_code == 403


def test_only_administrators_can_use_the_account_endpoints_in_supabase_mode(sb):
    c, f = sb
    f.add_user("p@example.org", PW, role="patient")
    t = bearer(login(c, "p@example.org"))
    assert c.post("/api/admin/users", json={"id": "x@example.org", "name": "X", "role": "admin", "password": "a-fine-long-password"}, headers=t).status_code == 403
    assert c.get("/api/admin/users").status_code == 401


# ---------------------------------------------------------------- nothing sensitive is logged or audited
def test_no_password_token_or_address_reaches_the_logs_or_the_audit_trail(sb, caplog):
    c, f = sb
    f.add_user("private.person@example.org", PW)
    caplog.set_level(logging.DEBUG)
    login(c, "private.person@example.org", "wrong-password-xyz")
    r = login(c, "private.person@example.org")
    c.post("/api/auth/register", json={"email": "another.person@example.org", "password": PW, "full_name": "Another"})
    c.post("/api/auth/password-reset/request", json={"email": "private.person@example.org"})
    blob = caplog.text + json.dumps(db.q("SELECT actor, action, target, detail FROM audit ORDER BY ts DESC LIMIT 40"))
    for secret in ("private.person@example.org", "another.person@example.org", PW, "wrong-password-xyz", r.json()["token"], c.cookies.get("cosmo_rt") or "never"):
        assert secret not in blob, secret[:12]
    assert db.q1("SELECT COUNT(*) c FROM audit WHERE action='auth.failed'")["c"] >= 1 and db.q1("SELECT COUNT(*) c FROM audit WHERE action='auth.login'")["c"] >= 1

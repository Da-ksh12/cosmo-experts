"""Supabase token verification and role resolution.
Part 1/2 run offline with a locally generated EC key pair. Part 3 uses REAL Supabase Auth tokens and is skipped unless
SUPABASE_ACCESS_TOKEN and SUPABASE_PROJECT_REF are set (a development project)."""
import base64
import json
import os
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import supabase_auth
from app.supabase_auth import AuthError, Verifier

URL = "https://example-project.supabase.co"
ISS = URL + "/auth/v1"
SUB = str(uuid.uuid4())

PRIV = ec.generate_private_key(ec.SECP256R1())
OTHER = ec.generate_private_key(ec.SECP256R1())


def jwk_of(priv, kid):
    d = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(priv.public_key()))
    d.update(kid=kid, alg="ES256", use="sig")
    return d


JWKS = {"keys": [jwk_of(PRIV, "k1")]}


def token(claims=None, key=PRIV, alg="ES256", kid="k1", **over):
    base = {"sub": SUB, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 600, "role": "authenticated"}
    base.update(claims or {}); base.update(over)
    base = {k: v for k, v in base.items() if v is not None}
    return jwt.encode(base, key, algorithm=alg, headers={"kid": kid} if kid else {})


@pytest.fixture()
def v():
    return Verifier(URL, jwks=JWKS)


# ---------------------------------------------------------------- Part 1: the verifier
def test_valid_token_is_accepted(v):
    c = v.verify(token())
    assert c["sub"] == SUB and c["aud"] == "authenticated"


@pytest.mark.parametrize("name,kw", [
    ("expired", {"exp": int(time.time()) - 3600}),
    ("wrong audience", {"aud": "anon"}),
    ("wrong issuer", {"iss": "https://evil.supabase.co/auth/v1"}),
    ("missing exp", {"exp": None}),
    ("missing sub", {"sub": None}),
    ("missing audience", {"aud": None}),
    ("not yet valid", {"nbf": int(time.time()) + 3600}),
    ("short sub", {"sub": "x"}),
])
def test_invalid_claims_are_rejected(v, name, kw):
    with pytest.raises(AuthError):
        v.verify(token(**kw))


def test_expired_message_is_specific(v):
    with pytest.raises(AuthError, match="expired"):
        v.verify(token(exp=int(time.time()) - 3600))


def test_signature_and_payload_tampering_rejected(v):
    t = token()
    h, p, s = t.split(".")
    flipped = s[:-4] + ("AAAA" if not s.endswith("AAAA") else "BBBB")
    with pytest.raises(AuthError):
        v.verify(f"{h}.{p}.{flipped}")
    payload = json.loads(base64.urlsafe_b64decode(p + "=="))
    payload["sub"] = str(uuid.uuid4())                                          # swap the user, keep the old signature
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthError):
        v.verify(f"{h}.{forged}.{s}")


def test_token_signed_by_a_different_key_is_rejected(v):
    with pytest.raises(AuthError):
        v.verify(token(key=OTHER, kid="k1"))


def test_unknown_kid_and_missing_kid_rejected(v):
    with pytest.raises(AuthError):
        v.verify(token(kid="nope"))
    with pytest.raises(AuthError):
        v.verify(token(kid=None))


def test_alg_none_is_rejected(v):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps({"sub": SUB, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 600}).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthError):
        v.verify(f"{header}.{body}.")


def test_algorithm_confusion_with_the_public_key_as_hmac_secret_is_rejected():
    """Classic attack: claim HS256 and 'sign' with the (public) key bytes. Must fail whether or not an HS256 secret is configured."""
    import hashlib, hmac
    pem = PRIV.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    signing_input = b64(b'{"alg":"HS256","kid":"k1"}') + "." + b64(json.dumps({"sub": SUB, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 600}).encode())
    forged = signing_input + "." + b64(hmac.new(pem, signing_input.encode(), hashlib.sha256).digest())
    for verifier in (Verifier(URL, jwks=JWKS), Verifier(URL, hs_secret="a-different-configured-secret", jwks=JWKS)):
        with pytest.raises(AuthError):
            verifier.verify(forged)


def test_hs256_only_when_a_secret_is_configured():
    t = jwt.encode({"sub": SUB, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 600}, "s" * 40, algorithm="HS256")
    with pytest.raises(AuthError):
        Verifier(URL, jwks=JWKS).verify(t)                                       # not configured: refused
    assert Verifier(URL, hs_secret="s" * 40, jwks=JWKS).verify(t)["sub"] == SUB
    with pytest.raises(AuthError):
        Verifier(URL, hs_secret="x" * 40, jwks=JWKS).verify(t)                   # wrong secret


def test_header_algorithm_must_match_the_key_type(v):
    rsa_header = base64.urlsafe_b64encode(b'{"alg":"RS256","kid":"k1"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps({"sub": SUB, "aud": "authenticated", "iss": ISS, "exp": int(time.time()) + 600}).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthError):
        v.verify(f"{rsa_header}.{body}.AAAA")


@pytest.mark.parametrize("junk", ["", "abc", "a.b.c", "....", "x" * 5000, "Bearer abc", "eyJ.eyJ.eyJ"])
def test_garbage_tokens_are_rejected_not_crashing(v, junk):
    with pytest.raises(AuthError):
        v.verify(junk)


def test_key_rotation_refreshes_once_and_is_rate_limited(monkeypatch):
    calls = []
    new_jwks = {"keys": [jwk_of(OTHER, "k2")]}

    def fake_get(url, headers=None, timeout=10):
        calls.append(url)
        return JWKS if len(calls) == 1 else {"keys": JWKS["keys"] + new_jwks["keys"]}
    monkeypatch.setattr(supabase_auth, "_get", fake_get)
    ver = Verifier(URL)
    assert ver.verify(token())["sub"] == SUB and len(calls) == 1                  # first use fetches the key set
    t2 = token(key=OTHER, kid="k2")
    with pytest.raises(AuthError):
        ver.verify(t2)                                                            # unknown kid -> one forced refresh, but within 30 s of the last fetch: not allowed
    assert len(calls) == 1
    ver._fetched -= 60                                                            # time passes
    assert ver.verify(t2)["sub"] == SUB and len(calls) == 2                       # the rotated key is now picked up


def test_jwks_outage_without_cached_keys_fails_closed(monkeypatch):
    import urllib.error
    monkeypatch.setattr(supabase_auth, "_get", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")))
    with pytest.raises(AuthError, match="temporarily"):
        Verifier(URL).verify(token())


def test_jwks_outage_keeps_serving_with_cached_keys(monkeypatch):
    import urllib.error
    ver = Verifier(URL)
    monkeypatch.setattr(supabase_auth, "_get", lambda *a, **k: JWKS)
    ver.verify(token())
    ver._fetched = 0                                                              # cache expired, and now the network is down
    monkeypatch.setattr(supabase_auth, "_get", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")))
    assert ver.verify(token())["sub"] == SUB


# ---------------------------------------------------------------- Part 2: the application using offline tokens
@pytest.fixture(scope="module")
def client():
    saved = {k: os.environ.get(k) for k in ("COSMO_AUTH", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET")}
    os.environ.update(COSMO_AUTH="supabase", SUPABASE_URL=URL, SUPABASE_SERVICE_ROLE_KEY="service-key-for-tests")
    os.environ.pop("SUPABASE_JWT_SECRET", None)
    supabase_auth._verifier = Verifier(URL, jwks=JWKS)
    from app.main import app
    with TestClient(app) as c:
        yield c
    for k, val in saved.items():
        os.environ.pop(k, None) if val is None else os.environ.__setitem__(k, val)
    supabase_auth._verifier = None
    supabase_auth.forget()


@pytest.fixture()
def profiles(monkeypatch):
    table = {}

    def fake_get(url, headers=None, timeout=10):
        assert headers["apikey"] == "service-key-for-tests"                         # the service key is used server-side only
        sub = url.split("id=eq.")[1].split("&")[0]
        return [table[sub]] if sub in table else []
    monkeypatch.setattr(supabase_auth, "_get", fake_get)
    supabase_auth.forget()
    return table


def auth(sub=SUB, **kw):
    return {"Authorization": f"Bearer {token(sub=sub, **kw)}"}


def test_app_resolves_identity_and_role_from_profiles(client, profiles):
    profiles[SUB] = {"id": SUB, "role": "patient", "full_name": "Pat Ient"}
    r = client.get("/api/me", headers=auth())
    assert r.status_code == 200 and r.json() == {"id": SUB, "name": "Pat Ient", "role": "patient"}
    assert client.get("/api/clinic/submissions", headers=auth()).status_code == 403


def test_role_claims_inside_the_token_are_ignored(client, profiles):
    profiles[SUB] = {"id": SUB, "role": "patient", "full_name": "P"}
    h = auth(app_metadata={"role": "admin"}, user_metadata={"role": "doctor"}, role="service_role")
    assert client.get("/api/me", headers=h).json()["role"] == "patient"
    assert client.get("/api/admin/audit", headers=h).status_code == 403


def test_role_changes_take_effect_after_the_cache_window(client, profiles, monkeypatch):
    profiles[SUB] = {"id": SUB, "role": "patient", "full_name": "P"}
    assert client.get("/api/me", headers=auth()).json()["role"] == "patient"
    profiles[SUB] = {"id": SUB, "role": "doctor", "full_name": "P"}
    assert client.get("/api/me", headers=auth()).json()["role"] == "patient"      # cached for a short window
    monkeypatch.setattr(supabase_auth, "PROFILE_TTL", 0.0)
    assert client.get("/api/me", headers=auth()).json()["role"] == "doctor"
    assert client.get("/api/clinic/submissions", headers=auth()).status_code == 200


def test_account_without_a_profile_or_with_unknown_role_is_refused(client, profiles, monkeypatch):
    monkeypatch.setattr(supabase_auth, "PROFILE_TTL", 0.0)
    assert client.get("/api/me", headers=auth()).status_code == 401
    profiles[SUB] = {"id": SUB, "role": "superuser", "full_name": "x"}
    assert client.get("/api/me", headers=auth()).status_code == 401
    del profiles[SUB]
    profiles[SUB] = {"id": SUB, "role": "patient", "full_name": "x"}
    assert client.get("/api/me", headers=auth()).status_code == 200
    del profiles[SUB]
    assert client.get("/api/me", headers=auth()).status_code == 401              # profile deleted -> access ends within the cache window


def test_bad_tokens_and_local_tokens_are_rejected(client, profiles):
    profiles[SUB] = {"id": SUB, "role": "patient", "full_name": "P"}
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert client.get("/api/me", headers=auth(exp=int(time.time()) - 5000)).status_code == 401
    from app import security
    assert client.get("/api/me", headers={"Authorization": "Bearer " + security.make_token("patient1")}).status_code == 401      # local HMAC tokens do not work in this mode
    assert client.get("/api/me", headers={"Authorization": "Bearer " + token(key=OTHER)}).status_code == 401                     # signed by an unknown key


def test_local_login_and_demo_accounts_are_disabled(client, monkeypatch):
    assert client.post("/api/auth/login", json={"user_id": "patient1", "password": "cosmo-dev"}).status_code == 401
    from app import security
    from app.main import app
    calls = []
    monkeypatch.setattr(security, "seed_users", lambda: calls.append(1))
    with TestClient(app):                                                          # a fresh startup must not create demo accounts when Supabase auth is on
        pass
    assert calls == []


# ---------------------------------------------------------------- Part 3: REAL Supabase tokens
REAL = bool(os.environ.get("SUPABASE_ACCESS_TOKEN") and os.environ.get("SUPABASE_PROJECT_REF"))


@pytest.fixture(scope="module")
def real_env():
    import urllib.request
    ref, tok = os.environ["SUPABASE_PROJECT_REF"], os.environ["SUPABASE_ACCESS_TOKEN"]
    url = f"https://{ref}.supabase.co"

    def call(method, u, headers, body=None):
        req = urllib.request.Request(u, method=method, headers={"Content-Type": "application/json", **headers}, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, (json.loads(r.read() or b"null"))
        except urllib.error.HTTPError as e:
            return e.code, None
    import urllib.error
    s, keys = call("GET", f"https://api.supabase.com/v1/projects/{ref}/api-keys?reveal=true", {"Authorization": f"Bearer {tok}"})
    anon = next(k["api_key"] for k in keys if k["name"] == "anon" and k["type"] == "legacy")
    svc = next(k["api_key"] for k in keys if k["name"] == "service_role" and k["type"] == "legacy")
    pw, made = "Tst-" + uuid.uuid4().hex + "!", {}
    for role in ("patient", "patient2", "doctor", "admin"):
        email = f"cosmo-auth-{role}-{uuid.uuid4().hex[:8]}@example.com"
        s, b = call("POST", f"{url}/auth/v1/admin/users", {"apikey": svc, "Authorization": f"Bearer {svc}"},
                    {"email": email, "password": pw, "email_confirm": True, "user_metadata": {"full_name": role, "role": "admin"}})
        assert s in (200, 201)
        if role in ("doctor", "admin"):
            call("POST", f"https://api.supabase.com/v1/projects/{ref}/database/query", {"Authorization": f"Bearer {tok}"}, {"query": f"update public.profiles set role='{role}' where id='{b['id']}'"})
        s, t = call("POST", f"{url}/auth/v1/token?grant_type=password", {"apikey": anon}, {"email": email, "password": pw})
        made[role] = {"id": b["id"], "jwt": t["access_token"], "email": email}
    saved = {k: os.environ.get(k) for k in ("COSMO_AUTH", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
    os.environ.update(COSMO_AUTH="supabase", SUPABASE_URL=url, SUPABASE_SERVICE_ROLE_KEY=svc)
    supabase_auth._verifier = None
    supabase_auth.forget()
    yield {"url": url, "users": made, "svc": svc, "call": call}
    for u in made.values():
        call("DELETE", f"{url}/auth/v1/admin/users/{u['id']}", {"apikey": svc, "Authorization": f"Bearer {svc}"})
    for k, val in saved.items():
        os.environ.pop(k, None) if val is None else os.environ.__setitem__(k, val)
    supabase_auth._verifier = None
    supabase_auth.forget()


@pytest.mark.skipif(not REAL, reason="no Supabase development project credentials in the environment")
def test_real_tokens_verify_and_tampering_is_detected(real_env):
    ver = Verifier(real_env["url"])
    u = real_env["users"]["patient"]
    claims = ver.verify(u["jwt"])
    assert claims["sub"] == u["id"] and claims["aud"] == "authenticated" and claims["iss"] == real_env["url"] + "/auth/v1"
    h = json.loads(base64.urlsafe_b64decode(u["jwt"].split(".")[0] + "=="))
    assert h["alg"] == "ES256" and h.get("kid")                                    # the project really signs with an asymmetric key
    a, b, c = u["jwt"].split(".")
    with pytest.raises(AuthError):
        ver.verify(f"{a}.{b}.{c[:-3]}AAA")
    forged_b = json.loads(base64.urlsafe_b64decode(b + "=="))
    forged_b["sub"] = real_env["users"]["admin"]["id"]
    with pytest.raises(AuthError):
        ver.verify(f"{a}." + base64.urlsafe_b64encode(json.dumps(forged_b).encode()).rstrip(b"=").decode() + f".{c}")
    with pytest.raises(AuthError):
        Verifier(real_env["url"], jwks=JWKS).verify(u["jwt"])                      # a different key set must not validate it
    real_jwks = real_env["call"]("GET", real_env["url"] + "/auth/v1/.well-known/jwks.json", {})[1]
    with pytest.raises(AuthError):
        Verifier("https://someone-else.supabase.co", jwks=real_jwks).verify(u["jwt"])             # right signature, wrong project: issuer mismatch


@pytest.mark.skipif(not REAL, reason="no Supabase development project credentials in the environment")
def test_real_users_get_the_right_roles_in_the_app_and_deleted_users_lose_access(real_env, monkeypatch):
    from app.main import app
    users = real_env["users"]
    with TestClient(app) as c:
        h = {r: {"Authorization": f"Bearer {u['jwt']}"} for r, u in users.items()}
        assert c.get("/api/me", headers=h["patient"]).json()["role"] == "patient"      # even though sign-up metadata said role=admin
        assert c.get("/api/me", headers=h["doctor"]).json()["role"] == "doctor"
        assert c.get("/api/me", headers=h["admin"]).json()["role"] == "admin"
        assert c.get("/api/clinic/submissions", headers=h["patient"]).status_code == 403
        assert c.get("/api/clinic/submissions", headers=h["doctor"]).status_code == 200
        assert c.get("/api/admin/audit", headers=h["doctor"]).status_code == 403
        assert c.get("/api/admin/audit", headers=h["admin"]).status_code == 200
        assert c.post("/api/auth/login", json={"user_id": "x", "password": "y"}).status_code == 401
        # delete the doctor in Supabase: the still-valid JWT must stop working once the profile cache lapses
        real_env["call"]("DELETE", f"{real_env['url']}/auth/v1/admin/users/{users['doctor']['id']}", {"apikey": real_env["svc"], "Authorization": f"Bearer {real_env['svc']}"})
        monkeypatch.setattr(supabase_auth, "PROFILE_TTL", 0.0)
        assert c.get("/api/clinic/submissions", headers=h["doctor"]).status_code == 401

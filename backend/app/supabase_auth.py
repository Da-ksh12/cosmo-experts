"""Supabase Auth integration: verify access tokens and resolve the caller's role.

Verification rules (all enforced here, none delegated to the frontend):
* the algorithm is taken from the SERVER's key set, never trusted from the token header; `none` and symmetric algorithms are refused unless an
  HS256 secret is explicitly configured; an asymmetric public key is never usable as an HMAC secret (algorithm-confusion attack);
* asymmetric keys come from the project's JWKS (`/auth/v1/.well-known/jwks.json`), selected by `kid`, cached, refreshed at most every 30 s on a miss;
* `exp`, `sub`, `iss` (= <project url>/auth/v1) and `aud` (= authenticated) are required and checked;
* the ROLE is read from the `profiles` table (service-role REST call, short cache), never from the token or user metadata.
Configuration: COSMO_AUTH=supabase, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (server only), optional SUPABASE_JWT_SECRET (legacy HS256)."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import jwt

ASYMMETRIC = {"ES256", "RS256"}


class AuthError(Exception):
    """Message is safe to show to the client."""


def configured() -> bool:
    return os.environ.get("COSMO_AUTH") == "supabase"


def _get(url: str, headers: dict | None = None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - operator-configured project URL
        return json.loads(r.read())


class Verifier:
    def __init__(self, url: str, hs_secret: str | None = None, audience: str = "authenticated", jwks: dict | None = None, jwks_ttl: int = 600):
        self.url = url.rstrip("/")
        self.issuer = f"{self.url}/auth/v1"
        self.audience, self.hs_secret, self.ttl = audience, hs_secret, jwks_ttl
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched = 0.0
        self._lock = threading.Lock()
        if jwks is not None:                       # injected key set (tests / air-gapped use)
            self._load(jwks)
            self._fetched = float("inf")

    def _load(self, jwks: dict):
        self._keys = {k["kid"]: jwt.PyJWK.from_dict(k) for k in jwks.get("keys", []) if k.get("kid") and k.get("alg") in ASYMMETRIC}

    def _refresh(self, force=False):
        with self._lock:
            now = time.time()
            if self._fetched == float("inf"):
                return
            if (force and now - self._fetched < 30) or (not force and now - self._fetched < self.ttl and self._keys):
                return
            try:
                self._load(_get(f"{self.url}/auth/v1/.well-known/jwks.json"))
                self._fetched = now
            except (urllib.error.URLError, TimeoutError, ValueError):
                if not self._keys:
                    raise AuthError("Sign-in is temporarily unavailable.")

    def verify(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError("Please sign in again.")
        alg, kid = header.get("alg"), header.get("kid")
        if alg in ASYMMETRIC:
            self._refresh()
            if kid not in self._keys:
                self._refresh(force=True)           # key rotation: look once more, rate-limited
            key = self._keys.get(kid)
            if key is None or key.algorithm_name != alg:
                raise AuthError("Please sign in again.")
            key_material = key.key
        elif alg == "HS256" and self.hs_secret:
            key_material = self.hs_secret
        else:
            raise AuthError("Please sign in again.")  # includes alg=none and any algorithm we did not configure
        try:
            claims = jwt.decode(token, key_material, algorithms=[alg], audience=self.audience, issuer=self.issuer, leeway=5,
                                options={"require": ["exp", "sub", "iss", "aud"]})
        except jwt.ExpiredSignatureError:
            raise AuthError("Your session has expired. Please sign in again.")
        except jwt.PyJWTError:
            raise AuthError("Please sign in again.")
        if not isinstance(claims.get("sub"), str) or len(claims["sub"]) < 8:
            raise AuthError("Please sign in again.")
        return claims


_verifier: Verifier | None = None
_profiles: dict[str, tuple[float, dict]] = {}
PROFILE_TTL = 30.0


def verifier() -> Verifier:
    global _verifier
    url = os.environ.get("SUPABASE_URL", "")
    if _verifier is None or _verifier.url != url.rstrip("/"):
        if not url:
            raise AuthError("Sign-in is not configured.")
        _verifier = Verifier(url, os.environ.get("SUPABASE_JWT_SECRET"))
    return _verifier


def profile(sub: str) -> dict:
    """Role and display name from public.profiles via the service role. A missing profile means the account is not provisioned: no access."""
    hit = _profiles.get(sub)
    if hit and time.time() - hit[0] < PROFILE_TTL:
        return hit[1]
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise AuthError("Sign-in is not configured.")
    try:
        rows = _get(f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/profiles?id=eq.{sub}&select=id,role,full_name", {"apikey": key, "Authorization": f"Bearer {key}"})
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise AuthError("Sign-in is temporarily unavailable.")
    if not rows or rows[0].get("role") not in ("patient", "doctor", "admin"):
        _profiles.pop(sub, None)
        raise AuthError("This account is not set up for access. Please contact the clinic.")
    _profiles[sub] = (time.time(), rows[0])
    return rows[0]


def set_profile_role(user_id: str, role: str) -> None:
    """Server-side role change on public.profiles with the service-role key. The role never comes from a client."""
    if role not in ("patient", "doctor", "admin"):
        raise AuthError("Unknown role.")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise AuthError("Account administration is not configured.")
    req = urllib.request.Request(f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/profiles?id=eq.{urllib.parse.quote(user_id, safe='')}", method="PATCH",
                                 data=json.dumps({"role": role}).encode(), headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:                 # noqa: S310 - operator-configured project URL
            rows = json.loads(r.read() or b"[]")
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise AuthError("Account administration is temporarily unavailable.") from None
    if not rows:
        raise AuthError("User not found.")
    forget(user_id)


def forget(sub: str | None = None):
    _profiles.pop(sub, None) if sub else _profiles.clear()

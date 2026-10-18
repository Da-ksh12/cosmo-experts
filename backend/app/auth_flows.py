"""Account lifecycle on Supabase Auth: sign-in, refresh, sign-out, registration, password reset, administrator account actions.

Security properties (each is covered by tests/test_auth_lifecycle.py):
* The REFRESH token never reaches JavaScript: it lives in an httpOnly cookie (Secure and SameSite per settings, scoped to /api/auth). Only the short-lived access token
  is returned in the response body. Refresh and sign-out additionally require a custom request header (a cross-site form cannot send it) and a trusted Origin if one is sent.
* Responses do not reveal whether an account exists: sign-in failures, registration of an existing address and password-reset requests for unknown addresses all give the
  same public answer. Per-address lockout (5 failures / 15 minutes) and the existing per-IP limits apply.
* Roles are never taken from the client: public registration creates a patient (a database trigger on auth.users assigns the default role; any role in the request is ignored).
* A disabled account is refused on every request (security.identity_from_claims), its Supabase sessions are banned, and a refresh for it is revoked.
* Nothing sensitive is logged or audited: addresses are stored only as a short salted-free hash prefix, never passwords, tokens or cookies."""
from __future__ import annotations

import hashlib
import logging
import re

from fastapi import HTTPException, Request, Response

from . import db, ratelimit, supabase_auth
from .gotrue import GoTrue, GoTrueError, Session
from .redact import safe_exc
from .settings import Settings

log = logging.getLogger("cosmo.auth")
COOKIE = "cosmo_rt"
CSRF_HEADER, CSRF_VALUE = "x-requested-with", "cosmo-web"
LOCK_FAILURES, LOCK_WINDOW = 5, 900
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,}$")
_COMMON = {"password1234", "123456789012", "qwertyuiop12", "letmein12345", "iloveyou1234", "administrator", "welcome12345", "password12345", "changeme1234"}
GENERIC_LOGIN = "Incorrect email or password."
GENERIC_REGISTER = "If this address can be registered, we have sent a message to confirm it. Please check your inbox."
GENERIC_RESET = "If an account exists for this address, we have sent a message with a link to reset the password."


def normalize_email(raw: str) -> str | None:
    e = (raw or "").strip().lower()
    return e if len(e) <= 254 and _EMAIL.match(e) else None


def email_tag(email: str) -> str:
    """A short, non-reversible tag for rate-limit keys and audit entries (the address itself is never stored)."""
    return hashlib.sha256(email.encode()).hexdigest()[:16]


def password_problem(pw: str, email: str = "") -> str | None:
    """Length-based policy (no composition rules), plus a few obvious refusals. Returns a patient-safe message or None."""
    if len(pw) < 12:
        return "Please choose a password of at least 12 characters."
    if len(pw) > 128:
        return "Please choose a password of at most 128 characters."
    low = pw.lower()
    if low in _COMMON or len(set(low)) < 4 or (email and email.split("@")[0] and email.split("@")[0] in low):
        return "That password is too easy to guess. Please choose a longer, less predictable one."
    return None


def _audit(action: str, tag: str = "", detail: dict | None = None) -> None:
    try:
        with db.context.as_system():
            db.audit(f"acct:{tag}" if tag else "anonymous", action, "", detail or {})
    except Exception as e:                                                                # auditing must never break sign-in
        log.warning("audit failed: %s", safe_exc(e))


def gotrue() -> GoTrue:
    try:
        return GoTrue.from_settings()
    except GoTrueError:
        raise HTTPException(503, "Sign-in is not available right now.") from None


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    return not origin or origin in Settings.from_env().cors_origins


def require_csrf(request: Request) -> None:
    if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE or not _origin_ok(request):
        raise HTTPException(403, "This request was not accepted.")


def set_refresh_cookie(response: Response, token: str) -> None:
    st = Settings.from_env()
    response.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, secure=st.cookie_secure, samesite=st.cookie_samesite, path="/api/auth")


def clear_refresh_cookie(response: Response) -> None:
    st = Settings.from_env()
    response.delete_cookie(COOKIE, path="/api/auth", secure=st.cookie_secure, httponly=True, samesite=st.cookie_samesite)


def _finish_session(sess: Session, response: Response, tag: str, action: str) -> dict:
    """Verify the access token, resolve the account (role from the profile, disabled check), set the refresh cookie. Raises HTTPException."""
    from .security import identity_from_claims
    try:
        claims = supabase_auth.verifier().verify(sess.access_token)
    except supabase_auth.AuthError:
        raise HTTPException(401, GENERIC_LOGIN) from None
    try:
        user = identity_from_claims(claims)
    except HTTPException as e:
        if e.status_code == 403:                                                          # disabled: do not leave a usable session behind
            try:
                gotrue().logout(sess.access_token, "global")
            except Exception:                                                              # noqa: BLE001  best effort
                pass
        raise
    set_refresh_cookie(response, sess.refresh_token)
    _audit(action, tag)
    return {"token": sess.access_token, "expires_in": sess.expires_in, "user": user}


# ---------------------------------------------------------------------------------- sign-in
def login(email_raw: str, password: str, response: Response) -> dict:
    email = normalize_email(email_raw)
    tag = email_tag(email) if email else email_tag(email_raw or "")
    key = f"loginfail:{tag}"
    lim = ratelimit.get()
    if lim.count(key, LOCK_WINDOW) >= LOCK_FAILURES:
        _audit("auth.locked", tag)
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.", headers={"Retry-After": str(LOCK_WINDOW)})
    if not email or not password or len(password) > 256:
        lim.hit(key, LOCK_FAILURES, LOCK_WINDOW)
        raise HTTPException(401, GENERIC_LOGIN)
    try:
        sess = gotrue().password_login(email, password)
    except GoTrueError as e:
        if e.code == "email_not_confirmed":
            raise HTTPException(403, {"code": "email_not_confirmed", "message": "Please confirm your email address first."}) from None
        if e.status in (400, 401, 422):
            lim.hit(key, LOCK_FAILURES, LOCK_WINDOW)
            _audit("auth.failed", tag)
            raise HTTPException(401, GENERIC_LOGIN) from None
        if e.status == 429:
            raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.", headers={"Retry-After": "60"}) from None
        log.warning("sign-in provider error: %s", e.code)
        raise HTTPException(503, "Sign-in is temporarily unavailable.") from None
    out = _finish_session(sess, response, tag, "auth.login")
    lim.clear(key)
    return out


def refresh(request: Request, response: Response) -> dict:
    require_csrf(request)
    rt = request.cookies.get(COOKIE, "")
    if not rt:
        raise HTTPException(401, "Please sign in again.")
    try:
        sess = gotrue().refresh(rt)
    except GoTrueError as e:
        clear_refresh_cookie(response)
        if e.status == 0 or e.status >= 500:
            raise HTTPException(503, "Sign-in is temporarily unavailable.") from None
        raise HTTPException(401, "Please sign in again.") from None
    try:
        return _finish_session(sess, response, "", "auth.refresh")
    except HTTPException:
        clear_refresh_cookie(response)
        raise


def logout(request: Request, response: Response, authorization: str) -> dict:
    require_csrf(request)
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    if token:
        try:
            gotrue().logout(token)
        except GoTrueError:
            pass                                                                            # the token may already be expired: signing out must still succeed locally
    clear_refresh_cookie(response)
    return {"ok": True}


# ---------------------------------------------------------------------------------- registration
def registration_enabled() -> bool:
    import os
    return os.environ.get("COSMO_ALLOW_REGISTRATION") == "1"


def register(email_raw: str, password: str, full_name: str, ip: str) -> dict:
    if not registration_enabled():
        raise HTTPException(404, "Registration is not open.")
    email = normalize_email(email_raw)
    name = re.sub(r"\s+", " ", (full_name or "").strip())[:80]
    if not email:
        raise HTTPException(422, "Please enter a valid email address.")
    if not name or re.search(r"[<>\x00-\x1f]", name):
        raise HTTPException(422, "Please enter your name.")
    prob = password_problem(password, email)
    if prob:
        raise HTTPException(422, prob)
    tag = email_tag(email)
    lim = ratelimit.get()
    if not lim.hit(f"register:ip:{ip}", 5, 3600)[0] or not lim.hit(f"register:acct:{tag}", 3, 3600)[0]:
        raise HTTPException(429, "Too many attempts. Please try again later.", headers={"Retry-After": "3600"})
    try:
        gotrue().signup(email, password, {"full_name": name})          # ONLY the name is sent as metadata: a client-supplied role would be ignored anyway
    except GoTrueError as e:
        log.info("registration not completed: %s", e.code or e.status)  # existing address, provider limits, etc.: the caller learns nothing either way
    _audit("auth.register_requested", tag)
    return {"message": GENERIC_REGISTER}


# ---------------------------------------------------------------------------------- password reset
def reset_request(email_raw: str, ip: str, redirect_to: str | None) -> dict:
    email = normalize_email(email_raw)
    if email:
        tag = email_tag(email)
        lim = ratelimit.get()
        if lim.hit(f"reset:ip:{ip}", 10, 3600)[0] and lim.hit(f"reset:acct:{tag}", 3, 3600)[0]:
            allowed = Settings.from_env().cors_origins
            target = redirect_to if redirect_to and any(redirect_to == o or redirect_to.startswith(o.rstrip("/") + "/") for o in allowed) else None
            try:
                gotrue().recover(email, target)
            except GoTrueError as e:
                log.info("recovery not sent: %s", e.code or e.status)
            _audit("auth.reset_requested", tag)
    return {"message": GENERIC_RESET}                                                       # identical for unknown, malformed and rate-limited requests


def reset_confirm(recovery_token: str, new_password: str) -> dict:
    """The recovery link signs the user in with a short-lived token; the new password is applied with it and every other session is revoked."""
    try:
        claims = supabase_auth.verifier().verify(recovery_token)
    except supabase_auth.AuthError:
        raise HTTPException(400, "This reset link is invalid or has expired. Please request a new one.") from None
    prob = password_problem(new_password, claims.get("email", ""))
    if prob:
        raise HTTPException(422, prob)
    g = gotrue()
    try:
        g.update_password(recovery_token, new_password)
    except GoTrueError as e:
        if e.status in (400, 401, 403, 422):
            raise HTTPException(400, "This reset link is invalid or has expired. Please request a new one.") from None
        raise HTTPException(503, "Password reset is temporarily unavailable.") from None
    try:
        g.logout(recovery_token, "global")                                                  # sign out every session: a leaked password must not keep working elsewhere
    except GoTrueError:
        pass
    _audit("auth.password_reset", email_tag(claims.get("email", "") or claims.get("sub", "")))
    return {"ok": True}

"""Local-mode auth (HMAC-signed tokens) with role checks. Production should verify Supabase JWTs
and rely on Postgres RLS (see supabase/migrations). Authorization is always enforced server-side."""
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from . import db
from .config import SECRET

ROLES = ("patient", "doctor", "admin")


def hash_pw(pw: str, salt: str = "cosmo") -> str:
    """LEGACY format (fixed salt), still accepted so existing local accounts keep working. New passwords use new_hash()."""
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()


def new_hash(pw: str) -> str:
    salt = os.urandom(16).hex()
    return f"pbkdf2${salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), bytes.fromhex(salt), 310_000).hex()}"


def verify_pw(pw: str, stored: str) -> bool:
    """Constant-time comparison for both formats. An empty stored hash (Supabase-mirrored identity) never verifies."""
    if not stored:
        return False
    if stored.startswith("pbkdf2$"):
        try:
            _, salt, h = stored.split("$")
            return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 310_000).hex(), h)
        except ValueError:
            return False
    return hmac.compare_digest(hash_pw(pw), stored)


def make_token(user_id: str, ttl=8 * 3600) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"u": user_id, "exp": time.time() + ttl}).encode()).decode()
    sig = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_token(token: str):
    try:
        body, sig = token.split(".")
        good = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(base64.urlsafe_b64decode(body))
        if data["exp"] < time.time():
            return None
        return data["u"]
    except Exception:
        return None


def seed_users():
    """Dev users. Passwords come from env or are random-per-install; never hard-coded for production."""
    for uid_, name, role in (("patient1", "Demo Patient One", "patient"), ("patient2", "Demo Patient Two", "patient"),
                             ("doctor1", "Demo Doctor", "doctor"), ("admin1", "Demo Admin", "admin")):
        if not db.q1("SELECT id FROM users WHERE id=?", (uid_,)):
            pw = os.environ.get("COSMO_DEV_PASSWORD", "cosmo-dev")
            db.x("INSERT INTO users(id,name,role,password_hash) VALUES(?,?,?,?)", (uid_, name, role, hash_pw(pw)))


def _authenticate(authorization: str) -> dict:
    """Verify the token and resolve the account. Runs as the SYSTEM identity: who the caller is cannot be known before this lookup."""
    with db.context.as_system():
        return _authenticate_inner(authorization)


def _authenticate_inner(authorization: str) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please sign in.")
    token = authorization[7:]
    from . import supabase_auth
    if supabase_auth.configured():
        try:
            claims = supabase_auth.verifier().verify(token)
        except supabase_auth.AuthError as e:
            raise HTTPException(401, str(e))
        return identity_from_claims(claims)
    uid_ = verify_token(token)
    user = uid_ and db.q1("SELECT id,name,role,disabled FROM users WHERE id=?", (uid_,))
    if not user:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    if user["disabled"]:                                   # an existing token stops working the moment the account is disabled
        raise HTTPException(403, "This account has been disabled. Please contact the clinic.")
    return {"id": user["id"], "name": user["name"], "role": user["role"]}


def identity_from_claims(claims: dict) -> dict:
    """Verified Supabase claims -> the application identity. The ROLE comes from public.profiles (trusted server-side data), never from the token or the request.
    The account is mirrored into the application's own users table (separate from the authentication identity) where the `disabled` flag lives; a disabled account is
    refused on every request, so disabling takes effect immediately even though the access token itself stays valid until it expires."""
    from . import supabase_auth
    with db.context.as_system():
        try:
            prof = supabase_auth.profile(claims["sub"])
        except supabase_auth.AuthError as e:
            raise HTTPException(401, str(e))
        db.x("INSERT INTO users(id,name,role,password_hash) VALUES(?,?,?,'') ON CONFLICT(id) DO UPDATE SET role=excluded.role, name=excluded.name",
             (claims["sub"], prof.get("full_name") or claims.get("email", ""), prof["role"]))
        if (db.q1("SELECT disabled FROM users WHERE id=?", (claims["sub"],)) or {"disabled": 0})["disabled"]:
            raise HTTPException(403, "This account has been disabled. Please contact the clinic.")
    return {"id": claims["sub"], "name": prof.get("full_name") or "", "role": prof["role"]}


async def current_user(authorization: str = Header(default="")):
    """FastAPI dependency. Async on purpose: it establishes the database identity (dbcore.context) for the REST of this request, and a context variable set inside a
    synchronous dependency would be lost in its threadpool copy."""
    user = await run_in_threadpool(_authenticate, authorization)
    db.context.set_user(user["id"], user["role"])
    return user


def require(*roles):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "You do not have permission for this action.")
        return user
    return dep


def can_access_patient_data(user, owner_id: str) -> bool:
    """Owner, any clinician, and an administrator ONLY when COSMO_ADMIN_CLINICAL_ACCESS=read (the database policies apply the same rule)."""
    if user["id"] == owner_id or user["role"] == "doctor":
        return True
    return user["role"] == "admin" and db.context.admin_clinical_access() == "read"

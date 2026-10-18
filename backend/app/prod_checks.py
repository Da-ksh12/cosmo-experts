"""Refuse to start in production with an unsafe configuration. Active only when COSMO_MODE=production.

Each rule exists because the default that would otherwise be used is wrong for production: the dev secret, demo users, local plaintext HTTP,
disabled rate limits, wildcard CORS, an unapproved language model, no backup key."""
from __future__ import annotations

import base64
import os


def problems(env=os.environ) -> list[str]:
    out = []
    secret = env.get("COSMO_SECRET", "")
    if len(secret) < 32 or secret == "dev-only-secret-change-me":
        out.append("COSMO_SECRET must be set to a random value of at least 32 characters")
    if env.get("COSMO_AUTH") != "supabase" or not env.get("SUPABASE_URL") or not env.get("SUPABASE_SERVICE_ROLE_KEY"):
        out.append("COSMO_AUTH=supabase with SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY is required (local demo accounts must not exist in production)")
    if env.get("COSMO_STORAGE") != "supabase" and env.get("COSMO_ALLOW_LOCAL_STORAGE") != "1":
        out.append("COSMO_STORAGE=supabase is required, or set COSMO_ALLOW_LOCAL_STORAGE=1 to accept patient files on this host's disk (then they must be on an encrypted volume)")
    if env.get("COSMO_RATE_LIMIT") == "off":
        out.append("COSMO_RATE_LIMIT=off is not allowed")
    origins = [o.strip() for o in env.get("COSMO_CORS", "http://localhost:3000").split(",") if o.strip()]
    if not origins or any(o == "*" or not o.startswith("https://") for o in origins):
        out.append("COSMO_CORS must list explicit https:// origins (no '*', no http, no localhost)")
    if env.get("COSMO_ALLOW_LOCAL_HTTP") == "1":
        out.append("COSMO_ALLOW_LOCAL_HTTP=1 is for tests only")
    if env.get("COSMO_ASSISTANT_MODE") == "llm" and env.get("COSMO_LLM_APPROVED") != "1":
        out.append("COSMO_ASSISTANT_MODE=llm is not allowed: the evaluated model failed its acceptance criteria. Set COSMO_LLM_APPROVED=1 only after a documented re-evaluation")
    try:
        if len(base64.b64decode(env.get("COSMO_BACKUP_KEY", ""), validate=True)) != 32:
            raise ValueError
    except ValueError:
        out.append("COSMO_BACKUP_KEY must be a base64-encoded 32-byte key (python -m scripts.backup genkey)")
    return out


def enforce(env=os.environ) -> None:
    if env.get("COSMO_MODE") != "production":
        return
    bad = problems(env)
    if bad:
        raise SystemExit("Refusing to start in production:\n  - " + "\n  - ".join(bad))

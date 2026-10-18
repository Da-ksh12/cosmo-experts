"""Typed, validated configuration for the data, identity and session layers.

Every value comes from the environment (secrets may come from NAME_FILE, see secrets_env.py). `Settings.from_env()` parses and range-checks; `Settings.problems()` is the
production fail-closed rule set used by prod_checks.enforce(). Nothing here logs or returns a secret: `describe()` shows only whether a secret is set and, for the database
URL, the host and database name. Messages in ConfigError never contain the offending VALUE of a secret."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from .redact import redact


class ConfigError(Exception):
    """Invalid configuration. The message is already redacted."""


def _int(env, name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name, "")
    if raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if not lo <= v <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return v


def _choice(env, name: str, default: str, allowed: tuple) -> str:
    v = (env.get(name) or default).strip().lower()
    if v not in allowed:
        raise ConfigError(f"{name} must be one of: {', '.join(allowed)}")
    return v


def _flag(env, name: str, default: bool = False) -> bool:
    raw = env.get(name)
    return default if raw is None or raw == "" else raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    mode: str                       # local | test | production
    db_backend: str                 # sqlite | postgres | "" (unset: allowed outside production only)
    database_url: str               # SECRET
    pool_min: int
    pool_max: int
    connect_timeout_s: int
    statement_timeout_ms: int
    connect_retries: int
    migration_mode: str             # off | check | apply
    auth_mode: str                  # local | supabase
    supabase_url: str
    supabase_anon_key: str          # public by design, still kept out of logs
    supabase_service_key: str       # SECRET, server only
    cookie_secure: bool
    cookie_samesite: str            # strict | lax
    cors_origins: tuple
    admin_clinical_access: str      # read | none | "" (unset)
    allow_sqlite_in_production: bool
    allow_startup_migrations: bool

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        mode = _choice(env, "COSMO_MODE", "local", ("local", "test", "production"))
        backend = (env.get("COSMO_DB") or "").strip().lower()
        if backend not in ("", "sqlite", "postgres"):
            raise ConfigError("COSMO_DB must be 'sqlite' or 'postgres'")
        if backend == "" and mode != "production":
            backend = "sqlite"
        pool_min, pool_max = _int(env, "COSMO_DB_POOL_MIN", 1, 0, 100), _int(env, "COSMO_DB_POOL_MAX", 10, 1, 200)
        if pool_min > pool_max:
            raise ConfigError("COSMO_DB_POOL_MIN must not exceed COSMO_DB_POOL_MAX")
        return cls(
            mode=mode, db_backend=backend, database_url=env.get("COSMO_DATABASE_URL", ""), pool_min=pool_min, pool_max=pool_max,
            connect_timeout_s=_int(env, "COSMO_DB_CONNECT_TIMEOUT", 5, 1, 120), statement_timeout_ms=_int(env, "COSMO_DB_STATEMENT_TIMEOUT_MS", 30000, 100, 600000),
            connect_retries=_int(env, "COSMO_DB_CONNECT_RETRIES", 3, 0, 10),
            migration_mode=_choice(env, "COSMO_MIGRATION_MODE", "check" if mode == "production" else "apply", ("off", "check", "apply")),
            auth_mode="supabase" if env.get("COSMO_AUTH") == "supabase" else "local",
            supabase_url=env.get("SUPABASE_URL", "").rstrip("/"), supabase_anon_key=env.get("SUPABASE_ANON_KEY", ""), supabase_service_key=env.get("SUPABASE_SERVICE_ROLE_KEY", ""),
            cookie_secure=_flag(env, "COSMO_COOKIE_SECURE", mode == "production"), cookie_samesite=_choice(env, "COSMO_COOKIE_SAMESITE", "strict", ("strict", "lax")),
            cors_origins=tuple(o.strip() for o in env.get("COSMO_CORS", "http://localhost:3000").split(",") if o.strip()),
            admin_clinical_access=(env.get("COSMO_ADMIN_CLINICAL_ACCESS") or "").strip().lower(),
            allow_sqlite_in_production=_flag(env, "COSMO_ALLOW_SQLITE_IN_PRODUCTION"), allow_startup_migrations=_flag(env, "COSMO_ALLOW_STARTUP_MIGRATIONS"))

    # ------------------------------------------------------------------ safe diagnostics
    def database_target(self) -> str:
        """host:port/dbname of the database URL, never the credentials."""
        if not self.database_url:
            return ""
        try:
            u = urlparse(self.database_url)
            return f"{u.hostname or '?'}:{u.port or 5432}/{(u.path or '/').lstrip('/') or '?'}"
        except ValueError:
            return "(unparseable)"

    def describe(self) -> dict:
        return {"mode": self.mode, "db_backend": self.db_backend or "(unset)", "database": self.database_target() or "(none)", "database_url_set": bool(self.database_url),
                "pool": f"{self.pool_min}-{self.pool_max}", "migration_mode": self.migration_mode, "auth_mode": self.auth_mode, "supabase_url": self.supabase_url or "(none)",
                "supabase_anon_key_set": bool(self.supabase_anon_key), "supabase_service_key_set": bool(self.supabase_service_key), "cookie_secure": self.cookie_secure,
                "cookie_samesite": self.cookie_samesite, "cors_origins": list(self.cors_origins), "admin_clinical_access": self.admin_clinical_access or "(unset)"}

    # ------------------------------------------------------------------ production rules (fail closed)
    def problems(self) -> list[str]:
        if self.mode != "production":
            return []
        out = []
        if self.db_backend != "postgres" and not (self.db_backend == "sqlite" and self.allow_sqlite_in_production):
            out.append("COSMO_DB=postgres is required in production (SQLite is for local development; set COSMO_ALLOW_SQLITE_IN_PRODUCTION=1 only for an explicitly accepted single-node deployment)")
        if self.db_backend == "postgres":
            u = urlparse(self.database_url) if self.database_url else None
            if not u or u.scheme not in ("postgresql", "postgres") or not u.hostname:
                out.append("COSMO_DATABASE_URL must be a postgresql:// URL (set it, or COSMO_DATABASE_URL_FILE)")
            elif u.hostname not in ("localhost", "127.0.0.1", "::1") and "sslmode=require" not in self.database_url and "sslmode=verify" not in self.database_url:
                out.append("COSMO_DATABASE_URL must require TLS for a remote database (add ?sslmode=require or verify-full)")
        if self.migration_mode == "apply" and not self.allow_startup_migrations:
            out.append("COSMO_MIGRATION_MODE=apply at startup is not allowed in production unless COSMO_ALLOW_STARTUP_MIGRATIONS=1; run `python -m scripts.migrate apply` as a deliberate step")
        if self.migration_mode == "off":
            out.append("COSMO_MIGRATION_MODE=off would skip the schema-version check: use 'check'")
        if not self.cookie_secure:
            out.append("COSMO_COOKIE_SECURE must be on in production (session cookies are only sent over HTTPS)")
        if self.auth_mode == "supabase" and not self.supabase_anon_key:
            out.append("SUPABASE_ANON_KEY is required for sign-in with Supabase Auth")
        if self.admin_clinical_access not in ("read", "none"):
            out.append("COSMO_ADMIN_CLINICAL_ACCESS must be set explicitly to 'none' (recommended: administrators cannot open patient records) or 'read'")
        return [redact(p) for p in out]

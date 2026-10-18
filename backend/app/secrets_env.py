"""Secrets from files (Docker/Kubernetes secrets, systemd credentials): for each allow-listed NAME, if NAME_FILE is set, NAME is read from that file.

Rules: the variable itself wins if both are set; a missing or empty file is a startup error (a silently empty secret is worse than a crash);
the value is never logged; only a fixed allow-list is honoured so an attacker-controlled environment cannot make the process read arbitrary files into
arbitrary variables."""
from __future__ import annotations

import os

ALLOWED = ("COSMO_SECRET", "COSMO_DATABASE_URL", "COSMO_DB_CONTEXT_SECRET", "COSMO_MIGRATION_DATABASE_URL", "COSMO_BACKUP_DATABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET", "COSMO_BACKUP_KEY", "COSMO_REDIS_URL", "COSMO_METRICS_TOKEN",
           "COSMO_LLM_API_KEY", "COSMO_DEV_PASSWORD")


def load(env=os.environ) -> list[str]:
    """Returns the names loaded from files (never the values)."""
    loaded = []
    for name in ALLOWED:
        path = env.get(f"{name}_FILE")
        if not path or env.get(name):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                value = f.read().strip()
        except OSError as e:
            raise SystemExit(f"{name}_FILE points to an unreadable file ({type(e).__name__})") from None
        if not value:
            raise SystemExit(f"{name}_FILE points to an empty file")
        env[name] = value
        loaded.append(name)
    return loaded

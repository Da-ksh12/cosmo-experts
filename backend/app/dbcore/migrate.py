"""Ordered, checksummed, forward-only PostgreSQL migrations.

* Files: backend/migrations/postgres/NNNN_name.sql, applied in numeric order. Versions must be contiguous (no gaps, no duplicates).
* Each migration runs in ONE transaction together with its schema_migrations record: a failure rolls the whole migration back (PostgreSQL DDL is transactional), so a
  failed run leaves the database exactly at the previous version. Recovery from a failed or wrong migration is: fix forward with a new migration, or restore a backup
  (docs/DATABASE_MIGRATION.md).
* The SHA-256 of each applied file is stored. Editing a migration after it was applied is detected and refused (drift): never edit an applied migration, add a new one.
* A transaction-level advisory lock keeps two runners from racing.
* Applying needs an OWNER credential (COSMO_MIGRATION_DATABASE_URL). The running application uses the restricted cosmo_app role, which can only read schema_migrations.
  At startup the application only CHECKS (COSMO_MIGRATION_MODE=check) and refuses to run against a database that is behind or has drifted."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql as pgsql
from psycopg.rows import dict_row

from ..redact import safe_exc

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "postgres"
LOCK_KEY = 727_001
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

BOOTSTRAP = """
create schema if not exists app;
create table if not exists app.schema_migrations (
  version integer primary key,
  name text not null,
  checksum text not null,
  applied_at timestamptz not null default now(),
  applied_by text not null default current_user
);
"""
GRANT_STATE = """
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'cosmo_app') then
    grant usage on schema app to cosmo_app;
    grant select on app.schema_migrations to cosmo_app;
  end if;
end $$;
"""


class MigrationError(Exception):
    """Message is safe to log (already redacted)."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    out = []
    for p in sorted(directory.glob("*.sql")):
        m = _NAME.match(p.name)
        if not m:
            raise MigrationError(f"migration file name is not NNNN_name.sql: {p.name}")
        text = p.read_text(encoding="utf-8")
        out.append(Migration(int(m.group(1)), m.group(2), p, text, hashlib.sha256(text.encode("utf-8")).hexdigest()))
    versions = [m.version for m in out]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"migration versions must be contiguous starting at 1, found {versions}")
    return out


def _applied(conn) -> dict[int, dict]:
    exists = conn.execute("select to_regclass('app.schema_migrations') is not null as e").fetchone()["e"]
    if not exists:
        return {}
    return {r["version"]: r for r in conn.execute("select version, name, checksum, applied_at from app.schema_migrations order by version").fetchall()}


def _compare(found: list[Migration], applied: dict[int, dict]) -> dict:
    drift = [m.version for m in found if m.version in applied and applied[m.version]["checksum"] != m.checksum]
    unknown = [v for v in applied if v not in {m.version for m in found}]
    pending = [m.version for m in found if m.version not in applied]
    return {"applied": sorted(applied), "pending": pending, "drift": drift, "unknown_applied": sorted(unknown)}


def connect_owner(url: str):
    try:
        return psycopg.connect(url, row_factory=dict_row, connect_timeout=10)
    except psycopg.Error as e:
        raise MigrationError("could not connect with the migration credential: " + safe_exc(e)) from None


def status(url: str, directory: Path = MIGRATIONS_DIR) -> dict:
    found = discover(directory)
    with connect_owner(url) as conn:
        st = _compare(found, _applied(conn))
    st["latest"] = found[-1].version if found else 0
    return st


def apply(url: str, target: int | None = None, dry_run: bool = False, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """Apply pending migrations up to `target` (default: all). Returns the versions applied (or that would be applied with dry_run)."""
    found = discover(directory)
    with connect_owner(url) as conn:
        conn.execute("select pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute(pgsql.SQL(BOOTSTRAP)); conn.commit()
            st = _compare(found, _applied(conn))
            if st["drift"]:
                raise MigrationError(f"applied migration(s) {st['drift']} were modified after being applied: refusing to continue (add a new migration instead)")
            if st["unknown_applied"]:
                raise MigrationError(f"the database has migrations this code does not know: {st['unknown_applied']} (database is ahead of the application)")
            todo = [m for m in found if m.version in st["pending"] and (target is None or m.version <= target)]
            if dry_run:
                return [m.version for m in todo]
            done = []
            for m in todo:
                try:
                    with conn.transaction():                               # the migration and its record commit or roll back together
                        conn.execute(pgsql.SQL(m.sql))
                        conn.execute("insert into app.schema_migrations(version, name, checksum) values (%s, %s, %s)", (m.version, m.name, m.checksum))
                except psycopg.Error as e:
                    raise MigrationError(f"migration {m.version:04d}_{m.name} failed and was rolled back (database stays at {done[-1] if done else max(st['applied'], default=0)}): "
                                         + safe_exc(e)) from None
                conn.execute(pgsql.SQL(GRANT_STATE)); conn.commit()
                done.append(m.version)
            return done
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,)); conn.commit()


def install_ctx_secret(url: str, secret: str, replace: bool = False) -> bool:
    """Install the context secret in the database with the OWNER credential. Returns True if it was (re)written; an existing secret is kept unless replace=True."""
    with connect_owner(url) as conn:
        has = conn.execute("select to_regclass('app_private.ctx_secret') is not null as e").fetchone()["e"]
        if not has:
            raise MigrationError("row-level security is not installed (apply migration 0002 first)")
        if conn.execute("select exists (select 1 from app_private.ctx_secret) as e").fetchone()["e"] and not replace:
            return False
        conn.execute("select app_private.set_ctx_secret(%s)", (secret,))
        conn.commit()
        return True


def check_ctx_ready(backend) -> None:
    """The running application must be able to establish the system identity: proves the secret in the database matches COSMO_DB_CONTEXT_SECRET."""
    from .context import make
    try:
        r = backend.q("select app_private.cur_role() as r", (), make("", "system"))
    except Exception as e:
        raise MigrationError("could not verify the database access context: " + safe_exc(e)) from None
    if not r or r[0]["r"] != "system":
        raise MigrationError("the database rejected the application's identity signature: install the secret with app_private.set_ctx_secret (owner credential) "
                             "so that it matches COSMO_DB_CONTEXT_SECRET")


def startup_check(backend, settings) -> None:
    """Application start-up policy. check: refuse if behind/drifted. apply: apply with the owner credential, then check. off: nothing (never allowed in production)."""
    import os
    from .context import as_system
    mode = settings.migration_mode
    if mode == "off":
        return
    if mode == "apply":
        owner = os.environ.get("COSMO_MIGRATION_DATABASE_URL") or settings.database_url
        apply(owner)
        if settings.db_context_secret:
            install_ctx_secret(owner, settings.db_context_secret)                # first run only: an existing secret is never overwritten implicitly
    found = discover()
    try:
        with as_system():
            rows = backend.q("select version, checksum from app.schema_migrations order by version")
    except Exception as e:
        raise MigrationError("the database has not been migrated (cannot read app.schema_migrations): run `python -m scripts.migrate apply` - " + safe_exc(e)) from None
    st = _compare(found, {r["version"]: r for r in rows})
    if st["drift"] or st["pending"] or st["unknown_applied"]:
        raise MigrationError(f"database schema is not at the version this application expects: pending={st['pending']} drift={st['drift']} unknown={st['unknown_applied']}. "
                             "Run `python -m scripts.migrate status` / `apply` with the owner credential.")
    check_ctx_ready(backend)


def expected_tables(directory: Path = MIGRATIONS_DIR) -> set[str]:
    """Tables declared by the migration files (create table app.<name>)."""
    names = set()
    for m in discover(directory):
        names |= set(re.findall(r"create table (?:if not exists )?app\.([a-z_]+)", m.sql, re.I))
    return names

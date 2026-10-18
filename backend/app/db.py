"""Application database facade: one API (`q`, `q1`, `x`, `tx`, `insert_ignore`, `upsert`, `audit`) over SQLite (local development, isolated tests) or PostgreSQL
(production). The backend is selected explicitly by COSMO_DB; production never falls back to SQLite (see settings.py / prod_checks.py). Every call runs in the
identity context of the current request (dbcore.context), which PostgreSQL turns into row-level security."""
import json
import re
import threading
import uuid
from contextlib import contextmanager

from .config import DB_PATH
from .dbcore import context
from .dbcore.sqlite_backend import IntegrityError as _SqliteIntegrityError
from .dbcore.types import Epoch, now  # noqa: F401  (re-exported: db.now() returns an Epoch)
from .settings import ConfigError, Settings

_lock = threading.RLock()
_backend = None
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, role TEXT, password_hash TEXT);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, owner_id TEXT, filename TEXT, stored_path TEXT, mime TEXT,
  sha256 TEXT, doc_type TEXT, doc_date TEXT, page_count INTEGER, status TEXT, processing_version TEXT,
  review_status TEXT, language TEXT, error TEXT, created_at REAL, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS doc_pages(id TEXT PRIMARY KEY, document_id TEXT, page_no INTEGER, text TEXT, method TEXT, ocr_confidence REAL);
CREATE TABLE IF NOT EXISTS doc_fields(id TEXT PRIMARY KEY, document_id TEXT, name TEXT, value TEXT, unit TEXT, ref_range TEXT,
  source_page INTEGER, source_text TEXT, confidence REAL, flag TEXT, review_status TEXT, corrected_by TEXT, processing_version TEXT);
CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY, owner_id TEXT, region TEXT, stored_path TEXT, verdict TEXT, report TEXT, created_at REAL, deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, owner_id TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT, sources TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS price_items(id TEXT PRIMARY KEY, name TEXT, description TEXT, unit_price REAL, currency TEXT, active INTEGER, approved_by TEXT);
CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY, patient_id TEXT, status TEXT, lines TEXT, total REAL, currency TEXT,
  notes TEXT, created_by TEXT, approved_by TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS kb_articles(id TEXT PRIMARY KEY, title TEXT, body TEXT, language TEXT, review_status TEXT, reviewed_by TEXT, version INTEGER);
CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, data TEXT, approval_status TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS dataset_versions(id TEXT PRIMARY KEY, dataset_id TEXT, manifest TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS model_versions(id TEXT PRIMARY KEY, task TEXT, data TEXT, status TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, ts REAL, actor TEXT, action TEXT, target TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS field_corrections(id TEXT PRIMARY KEY, field_id TEXT, document_id TEXT, before TEXT, after TEXT, status TEXT, reviewer TEXT,
  ts REAL, processing_version TEXT, training_eligible INTEGER DEFAULT 0, governance_ref TEXT DEFAULT '', deidentified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS consents(user_id TEXT NOT NULL, notice_sha256 TEXT NOT NULL, accepted_at REAL, PRIMARY KEY(user_id, notice_sha256));
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT, title TEXT, body TEXT, link TEXT, dedupe_key TEXT DEFAULT '', created_at REAL, read_at REAL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_notifications_dedupe ON notifications(user_id, dedupe_key) WHERE dedupe_key != '';
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS care_plans(id TEXT PRIMARY KEY, patient_id TEXT, doctor_id TEXT, status TEXT, assessment TEXT, findings TEXT,
  stages TEXT, notes TEXT, recommended_item_ids TEXT, reviewed_at REAL, updated_at REAL);
"""

# Additive column migrations for databases created by earlier versions.
COLUMNS = [
    ("users", "credentials", "TEXT DEFAULT ''"), ("users", "verified", "INTEGER DEFAULT 0"),
    ("documents", "size_bytes", "INTEGER DEFAULT 0"), ("documents", "user_category", "TEXT DEFAULT ''"),
    ("doc_fields", "patient_flag", "TEXT DEFAULT ''"),
    ("images", "clinician_review", "TEXT DEFAULT ''"), ("images", "reviewed_by", "TEXT DEFAULT ''"), ("images", "reviewed_at", "REAL"),
    ("price_items", "category", "TEXT DEFAULT 'General'"), ("price_items", "sessions", "INTEGER DEFAULT 1"),
    ("price_items", "duration_min", "INTEGER DEFAULT 0"), ("price_items", "compare_at_price", "REAL"),
    ("kb_articles", "reviewed_at", "REAL"), ("kb_articles", "review_due", "REAL"), ("kb_articles", "content_sha256", "TEXT DEFAULT ''"), ("kb_articles", "attestation", "TEXT DEFAULT ''"),
    ("kb_articles", "source_note", "TEXT DEFAULT ''"), ("kb_articles", "content_origin", "TEXT DEFAULT 'clinic'"), ("kb_articles", "claims_to_verify", "TEXT DEFAULT '[]'"),
    ("users", "disabled", "INTEGER DEFAULT 0"), ("quotes", "valid_until", "REAL"),
    ("quotes", "subtotal", "REAL DEFAULT 0"), ("quotes", "tax_percent", "REAL DEFAULT 0"), ("quotes", "tax_amount", "REAL DEFAULT 0"),
]


def uid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------- backend selection
def backend():
    """The active backend, created on first use from the environment (COSMO_DB, COSMO_DATABASE_URL, pool settings)."""
    global _backend
    if _backend is None:
        with _lock:
            if _backend is None:
                st = Settings.from_env()
                if st.db_backend == "postgres":
                    from .dbcore.pg_backend import PostgresBackend
                    if not st.database_url:
                        raise ConfigError("COSMO_DB=postgres requires COSMO_DATABASE_URL")
                    _backend = PostgresBackend(st.database_url, st.pool_min, st.pool_max, st.connect_timeout_s, st.statement_timeout_ms, st.connect_retries)
                elif st.db_backend in ("", "sqlite"):
                    from .dbcore.sqlite_backend import SqliteBackend
                    _backend = SqliteBackend(DB_PATH)
                else:
                    raise ConfigError("unsupported COSMO_DB")
    return _backend


def reset_backend() -> None:
    """Close and forget the active backend (tests, reconfiguration)."""
    global _backend
    with _lock:
        if _backend is not None:
            _backend.close()
        _backend = None


def is_postgres() -> bool:
    return backend().name == "postgres"


def integrity_errors() -> tuple:
    """Exception classes meaning 'a constraint was violated' on the active backend."""
    if is_postgres():
        from .dbcore.pg_backend import IntegrityError
        return (IntegrityError,)
    return (_SqliteIntegrityError,)


# ---------------------------------------------------------------- access API
def q(sql, args=()):
    return backend().q(sql, args, context.current())


def q1(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def x(sql, args=()) -> int:
    """Execute a write; returns the number of affected rows."""
    return backend().x(sql, args, context.current())


@contextmanager
def tx():
    """One explicit transaction for a multi-step operation. Inside the block use ONLY the yielded object (`t.q`, `t.q1`, `t.x`), never db.q/db.x: those open their own
    transaction. The block commits on normal exit and rolls back if it raises."""
    with backend().tx(context.current()) as t:
        yield t


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError("invalid SQL identifier")
    return name


def insert_ignore(table: str, row: dict, t=None) -> bool:
    """INSERT that silently does nothing when a unique/primary key already exists. Returns True if a row was inserted."""
    cols = [_ident(c) for c in row]
    verb = "INSERT OR IGNORE INTO" if not is_postgres() else "INSERT INTO"
    tail = "" if not is_postgres() else " ON CONFLICT DO NOTHING"
    sql = f"{verb} {_ident(table)}({','.join(cols)}) VALUES({','.join('?' * len(cols))}){tail}"
    return (t.x(sql, list(row.values())) if t else x(sql, list(row.values()))) == 1


def upsert(table: str, key: tuple[str, ...] | str, row: dict, t=None) -> None:
    """INSERT, or UPDATE every non-key column when the key exists (identical semantics on both backends)."""
    key = (key,) if isinstance(key, str) else tuple(key)
    cols = [_ident(c) for c in row]
    upd = [c for c in cols if c not in key]
    sql = (f"INSERT INTO {_ident(table)}({','.join(cols)}) VALUES({','.join('?' * len(cols))}) ON CONFLICT({','.join(_ident(k) for k in key)}) "
           + (f"DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in upd)}" if upd else "DO NOTHING"))
    (t.x(sql, list(row.values())) if t else x(sql, list(row.values())))


def set_setting(key: str, value: str) -> None:
    upsert("settings", "key", {"key": key, "value": value})


def conn():
    """SQLite-only raw connection (legacy). New code uses q/x/tx; PostgreSQL has no equivalent."""
    if is_postgres():
        raise RuntimeError("db.conn() is SQLite-only; use db.tx()")
    return backend().tx()


def init():
    """Create or verify the schema. SQLite: idempotent DDL plus additive columns. PostgreSQL: the migration state is checked (or applied, per COSMO_MIGRATION_MODE)."""
    b = backend()
    if b.name == "postgres":
        from .dbcore import migrate
        migrate.startup_check(b, Settings.from_env())
        return
    with b.tx() as t:
        t.script(SCHEMA)
        for table, col, decl in COLUMNS:
            if col not in [r["name"] for r in t.q(f"PRAGMA table_info({table})")]:
                t.x(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def jdump(o):
    return json.dumps(o, ensure_ascii=False)


def audit(actor, action, target="", detail=None):
    x("INSERT INTO audit VALUES(?,?,?,?,?,?)", (uid(), now(), actor, action, target, jdump(detail or {})))

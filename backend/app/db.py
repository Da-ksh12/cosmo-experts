import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .config import DB_PATH

_lock = threading.RLock()

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


def now() -> float:
    return time.time()


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for table, col, decl in COLUMNS:
            if col not in [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def q(sql, args=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def x(sql, args=()):
    with conn() as c:
        c.execute(sql, args)


def jdump(o):
    return json.dumps(o, ensure_ascii=False)


def audit(actor, action, target="", detail=None):
    x("INSERT INTO audit VALUES(?,?,?,?,?,?)", (uid(), now(), actor, action, target, jdump(detail or {})))

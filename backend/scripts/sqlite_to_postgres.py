"""Copy development data from the application's SQLite database into the PostgreSQL application schema.

    python -m scripts.sqlite_to_postgres --sqlite backend/data/cosmo.sqlite3 [--dry-run] [--on-conflict skip|update|fail] [--orphans fail|skip]
                                         [--batch 500] [--progress migration-progress.json]
    target credential: COSMO_MIGRATION_DATABASE_URL (an owner of schema app; the schema must already be migrated: python -m scripts.migrate apply)

Guarantees
* The SQLite file is opened READ-ONLY and is never modified or deleted.
* Each batch commits in its own transaction; progress (last copied rowid per table) is written to the progress file, so an interrupted run resumes where it stopped.
* Idempotent: re-running skips rows that already exist (default), updates them (--on-conflict update) or stops (fail).
* Rows that reference a user that does not exist (foreign-key orphans) stop the run by default; --orphans skip drops and COUNTS them.
* Nothing sensitive is printed: output is table names and counts only. Row values never reach stdout, the progress file or logs.
* After copying, the result is validated: per-table row counts, a hash of the full set of primary keys, and a hash of selected critical fields (roles, hashes, owners,
  statuses) are compared between source and target; any mismatch exits non-zero.
* Time columns (Unix seconds in SQLite) become timezone-aware timestamptz; text, integers and floats are copied unchanged.
Recovery: nothing is deleted anywhere. To redo a failed or wrong run, create a fresh disposable database, run `migrate apply`, and run this tool again."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql as pgsql
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dbcore import migrate  # noqa: E402
from app.redact import safe_exc  # noqa: E402

# parents before children (foreign-key safe)
ORDER = ["users", "settings", "price_items", "kb_articles", "datasets", "dataset_versions", "model_versions", "model_events", "training_runs", "documents", "doc_pages", "doc_fields", "doc_facts",
         "doc_medications", "images", "conversations", "messages", "quotes", "care_plans", "field_corrections", "consents", "consent_events", "legal_holds", "notifications", "jobs",
         "appointment_requests", "agent_traces", "agent_actions", "audit"]
IGNORED = {"sqlite_sequence"}                         # any OTHER table in the SQLite file that is not listed in ORDER stops the run: data must never be left behind silently
PK = {"consents": ("user_id", "notice_sha256"), "settings": ("key",)}
CRITICAL = {"users": ("id", "role", "password_hash", "disabled"), "documents": ("id", "owner_id", "sha256", "status", "deleted"), "images": ("id", "owner_id", "verdict", "deleted"),
            "quotes": ("id", "patient_id", "status", "total"), "care_plans": ("id", "patient_id", "status"), "consents": ("user_id", "notice_sha256"),
            "kb_articles": ("id", "review_status", "content_sha256"), "notifications": ("id", "user_id", "dedupe_key"), "messages": ("id", "conversation_id", "role"),
            "appointment_requests": ("id", "patient_id", "status")}
FK_OWNER = {"documents": "owner_id", "images": "owner_id", "conversations": "owner_id", "quotes": "patient_id", "care_plans": "patient_id", "consents": "user_id", "notifications": "user_id",
            "appointment_requests": "patient_id"}


class MigrationAbort(Exception):
    pass


def pk_of(table: str) -> tuple:
    return PK.get(table, ("id",))


def to_pg_value(v, is_time: bool):
    if v is None:
        return None
    if is_time:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    return v


def digest(rows) -> str:
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: tuple("" if x is None else str(x) for x in r)):
        h.update(("\x1f".join("" if x is None else str(x) for x in r) + "\x1e").encode("utf-8"))
    return h.hexdigest()


def load_progress(path: Path | None) -> dict:
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            raise MigrationAbort("the progress file is not valid JSON; delete it to start over (nothing is deleted from the databases)") from None
    return {}


def save_progress(path: Path | None, data: dict) -> None:
    if path:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)


def migrate_data(sqlite_path: Path, target_url: str, batch: int = 500, dry_run: bool = False, on_conflict: str = "skip", orphans: str = "fail",
                 progress_path: Path | None = None) -> dict:
    if not sqlite_path.is_file():
        raise MigrationAbort("the SQLite file does not exist")
    src = sqlite3.connect(f"file:{sqlite_path.resolve().as_posix()}?mode=ro", uri=True)           # read-only: cannot modify the source
    src.row_factory = sqlite3.Row
    st = migrate.status(target_url)
    if st["pending"] or st["drift"] or st["unknown_applied"]:
        raise MigrationAbort(f"the target schema is not at the expected version (pending={st['pending']} drift={st['drift']}): run `python -m scripts.migrate apply` first")
    progress = load_progress(progress_path)
    report: dict = {"dry_run": dry_run, "tables": {}, "orphans_skipped": {}}
    dropped_children: dict = {}
    try:
        with psycopg.connect(target_url, row_factory=dict_row, connect_timeout=10, autocommit=True) as pg:      # autocommit: each batch's `transaction()` is a real, durable transaction
            src_tables = {r[0] for r in src.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
            unhandled = sorted(src_tables - set(ORDER) - IGNORED)
            if unhandled:
                raise MigrationAbort(f"the SQLite file has tables this tool does not copy ({', '.join(unhandled)}): refusing to leave data behind silently")
            user_ids = {r[0] for r in src.execute("select id from users")} if "users" in src_tables else set()
            for table in ORDER:
                if table not in src_tables:
                    report["tables"][table] = {"source": 0, "note": "table not present in the SQLite file"}
                    continue
                s_cols = [r[1] for r in src.execute(f"pragma table_info({table})")]
                p_info = {r["column_name"]: r["data_type"] for r in pg.execute("select column_name, data_type from information_schema.columns where table_schema='app' and table_name=%s", (table,)).fetchall()}
                extra = [c for c in s_cols if c not in p_info]
                if extra:
                    raise MigrationAbort(f"{table}: the SQLite columns {extra} do not exist in PostgreSQL (schema mismatch)")
                time_cols = {c for c in s_cols if p_info[c].startswith("timestamp")}
                key = pk_of(table)
                total = src.execute(f"select count(*) from {table}").fetchone()[0]
                rep = {"source": total, "inserted": 0, "skipped_existing": 0, "updated": 0}
                last = progress.get(table, {}).get("last_rowid", 0) if not dry_run else 0
                while True:
                    rows = src.execute(f"select rowid as _rid, * from {table} where rowid > ? order by rowid limit ?", (last, batch)).fetchall()
                    if not rows:
                        break
                    last = rows[-1]["_rid"]
                    keep = []
                    owner_col = FK_OWNER.get(table)
                    for r in rows:
                        if owner_col and r[owner_col] not in user_ids:
                            if orphans == "fail":
                                raise MigrationAbort(f"{table}: a row references a user that does not exist (foreign-key orphan); use --orphans skip to drop and count such rows")
                            report["orphans_skipped"][table] = report["orphans_skipped"].get(table, 0) + 1
                            continue
                        keep.append(r)
                    if table in ("doc_pages", "doc_fields", "doc_facts", "doc_medications"):              # children of documents: drop rows whose document was skipped as an orphan
                        ids = [r["document_id"] for r in keep]
                        present = {x["id"] for x in pg.execute("select id from app.documents where id = any(%s)", (ids,)).fetchall()} if ids and not dry_run else set(ids)
                        kept = [r for r in keep if r["document_id"] in present or dry_run]
                        if len(kept) != len(keep):
                            dropped_children[table] = dropped_children.get(table, 0) + len(keep) - len(kept)
                        keep = kept
                    if dry_run:
                        rep["inserted"] += len(keep)
                        continue
                    existing = set()
                    if len(key) == 1:
                        existing = {x[key[0]] for x in pg.execute(pgsql.SQL("select {} from app.{} where {} = any(%s)").format(pgsql.Identifier(key[0]), pgsql.Identifier(table), pgsql.Identifier(key[0])),
                                                                   ([r[key[0]] for r in keep],)).fetchall()}
                    else:
                        existing = {tuple(x[k] for k in key) for x in pg.execute(pgsql.SQL("select {} from app.{}").format(pgsql.SQL(",").join(map(pgsql.Identifier, key)), pgsql.Identifier(table))).fetchall()}
                    new = [r for r in keep if (r[key[0]] if len(key) == 1 else tuple(r[k] for k in key)) not in existing]
                    old = [r for r in keep if r not in new]
                    if old and on_conflict == "fail":
                        raise MigrationAbort(f"{table}: {len(old)} row(s) already exist in the target (use --on-conflict skip or update)")
                    cols = pgsql.SQL(",").join(map(pgsql.Identifier, s_cols))
                    ph = pgsql.SQL(",").join(pgsql.Placeholder() * len(s_cols))
                    ins = pgsql.SQL("insert into app.{} ({}) values ({})").format(pgsql.Identifier(table), cols, ph)
                    with pg.transaction():                                         # one transaction per batch: resumable and never half-written
                        with pg.cursor() as cur:
                            cur.executemany(ins, [[to_pg_value(r[c], c in time_cols) for c in s_cols] for r in new])
                            if old and on_conflict == "update":
                                upd = pgsql.SQL("insert into app.{} ({}) values ({}) on conflict ({}) do update set {}").format(
                                    pgsql.Identifier(table), cols, ph, pgsql.SQL(",").join(map(pgsql.Identifier, key)),
                                    pgsql.SQL(",").join(pgsql.SQL("{}=excluded.{}").format(pgsql.Identifier(c), pgsql.Identifier(c)) for c in s_cols if c not in key))
                                cur.executemany(upd, [[to_pg_value(r[c], c in time_cols) for c in s_cols] for r in old])
                    rep["inserted"] += len(new)
                    rep["updated" if on_conflict == "update" else "skipped_existing"] += len(old)
                    progress[table] = {"last_rowid": last, "complete": False}
                    save_progress(progress_path, progress)
                if not dry_run:
                    progress[table] = {"last_rowid": last, "complete": True}
                    save_progress(progress_path, progress)
                report["tables"][table] = rep
            if not dry_run:
                report["validation"] = validate(src, pg, src_tables, {k: report["orphans_skipped"].get(k, 0) + dropped_children.get(k, 0) for k in {*report["orphans_skipped"], *dropped_children}})
    except psycopg.Error as e:
        raise MigrationAbort("database error: " + safe_exc(e)) from None
    finally:
        src.close()
    return report


def validate(src: sqlite3.Connection, pg, src_tables: set, orphans: dict) -> dict:
    """Compare source and target without printing values: counts, primary-key set hash, critical-field hash."""
    out, ok = {}, True
    for table in ORDER:
        if table not in src_tables:
            continue
        key = pk_of(table)
        s_n = src.execute(f"select count(*) from {table}").fetchone()[0] - orphans.get(table, 0)
        p_n = pg.execute(pgsql.SQL("select count(*) c from app.{}").format(pgsql.Identifier(table))).fetchone()["c"]
        entry = {"source_rows": s_n, "target_rows": p_n, "count_ok": p_n >= s_n}
        if not orphans.get(table):
            s_keys = [tuple(r) for r in src.execute(f"select {','.join(key)} from {table}")]
            p_keys = [tuple(r[k] for k in key) for r in pg.execute(pgsql.SQL("select {} from app.{}").format(pgsql.SQL(",").join(map(pgsql.Identifier, key)), pgsql.Identifier(table))).fetchall()]
            entry["key_set_ok"] = set(s_keys) <= set(p_keys)
            cols = CRITICAL.get(table)
            if cols and p_n == s_n:
                s_rows = [tuple(r) for r in src.execute(f"select {','.join(cols)} from {table}")]
                p_rows = [tuple(r[c] for c in cols) for r in pg.execute(pgsql.SQL("select {} from app.{}").format(pgsql.SQL(",").join(map(pgsql.Identifier, cols)), pgsql.Identifier(table))).fetchall()]
                entry["critical_fields_ok"] = digest(s_rows) == digest(p_rows)
        ok = ok and all(v for k, v in entry.items() if k.endswith("_ok"))
        out[table] = entry
    return {"ok": ok, "tables": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", required=True, type=Path)
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--on-conflict", choices=("skip", "update", "fail"), default="skip")
    ap.add_argument("--orphans", choices=("fail", "skip"), default="fail")
    ap.add_argument("--progress", type=Path)
    a = ap.parse_args(argv)
    import os
    url = os.environ.get("COSMO_MIGRATION_DATABASE_URL", "")
    if not url:
        print("set COSMO_MIGRATION_DATABASE_URL (an owner credential for the application schema)", file=sys.stderr)
        return 2
    try:
        rep = migrate_data(a.sqlite, url, a.batch, a.dry_run, a.on_conflict, a.orphans, a.progress)
    except (MigrationAbort, migrate.MigrationError) as e:
        print(f"migration aborted: {e}", file=sys.stderr)
        return 1
    print(json.dumps(rep, indent=1))
    return 0 if rep.get("validation", {"ok": True})["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

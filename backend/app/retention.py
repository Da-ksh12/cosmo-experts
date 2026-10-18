"""Retention and deletion framework.

NOTHING is purged unless a retention period was chosen for that category by the operator. There are no built-in periods: how long a clinic must or may keep
conversations, clinical documents, quotations or audit entries is a legal and clinical-governance decision (docs/DATA_RETENTION_AND_DELETION.md). Set, per category,
COSMO_RETENTION_<CATEGORY>_DAYS (1..36500). An unset category is reported as "not configured" and is kept.

Safety properties (tests/test_retention.py):
  * dry run is the default; deleting needs run(dry_run=False)
  * a patient under an ACTIVE legal hold is excluded from every category (and audit purging is skipped while any hold is active)
  * bounded: rows are processed in batches, with a limit on batches and on wall-clock time per run
  * idempotent and resumable: a second run finds nothing left; a crash between steps leaves rows that the next run picks up again
  * files and rows are coordinated: the stored file is removed first with strict error handling; if that fails the row is kept and counted as failed
  * minimal audit: one entry per category per run with counts only (no identifiers, no content)
  * the run executes as the system identity so database policies allow the deletes; audit rows are removed only through app.purge_audit (PostgreSQL) which only the system can call
Account erasure on request (`erase_patient`) lives here too so that it applies the same hold rule and file coordination."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import db, storage
from .config import UPLOAD_DIR

log = logging.getLogger("cosmo.retention")
HOLD_REASONS = ("legal_proceeding", "regulatory_inquiry", "clinical_safety_review", "other")


@dataclass(frozen=True)
class Category:
    name: str
    table: str
    ts: str                  # timestamp column compared with the cutoff
    owner: str | None        # column naming the patient the row belongs to (for legal holds); None = not patient-bound
    label: str
    deleted_flag: bool = False


CATEGORIES = (
    Category("conversations", "conversations", "created_at", "owner_id", "Assistant conversations and their messages"),
    Category("notifications", "notifications", "created_at", "user_id", "Notifications"),
    Category("jobs", "jobs", "created_at", "owner_id", "Background-job records and any leftover input files"),
    Category("documents", "documents", "created_at", "owner_id", "Uploaded documents: files, extracted text and fields", deleted_flag=True),
    Category("images", "images", "created_at", "owner_id", "Uploaded photos", deleted_flag=True),
    Category("quotes", "quotes", "created_at", "patient_id", "Quotations"),
    Category("care_plans", "care_plans", "updated_at", "patient_id", "Care plans"),
    Category("audit", "audit", "ts", None, "Audit trail entries"),
)
BY_NAME = {c.name: c for c in CATEGORIES}


class ConfigError(Exception):
    pass


class Held(Exception):
    """The patient is under an active legal hold."""


def configured_days(env=os.environ) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for c in CATEGORIES:
        raw = (env.get(f"COSMO_RETENTION_{c.name.upper()}_DAYS") or "").strip()
        if c.name == "conversations" and not raw:
            raw = (env.get("COSMO_CONV_RETENTION_DAYS") or "").strip()            # the earlier name of the same setting
        if not raw:
            out[c.name] = None
            continue
        try:
            n = int(raw)
        except ValueError:
            raise ConfigError(f"COSMO_RETENTION_{c.name.upper()}_DAYS must be a whole number of days") from None
        if not 1 <= n <= 36500:
            raise ConfigError(f"COSMO_RETENTION_{c.name.upper()}_DAYS must be between 1 and 36500")
        out[c.name] = n
    return out


# ---------------------------------------------------------------- legal holds
def active_holds() -> list[dict]:
    with db.context.as_system():
        return db.q("SELECT id, patient_id, reason_code, placed_at FROM legal_holds WHERE released_at IS NULL ORDER BY placed_at")


def is_held(patient_id: str) -> bool:
    with db.context.as_system():
        return bool(db.q1("SELECT 1 x FROM legal_holds WHERE patient_id=? AND released_at IS NULL", (patient_id,)))


def place_hold(patient_id: str, reason_code: str, actor: str) -> dict:
    if reason_code not in HOLD_REASONS:
        raise ValueError("Unknown hold reason.")
    with db.context.as_system():
        if not db.q1("SELECT 1 x FROM users WHERE id=? AND role='patient'", (patient_id,)):
            raise LookupError("Patient not found.")
        if is_held(patient_id):
            return db.q1("SELECT id, patient_id, reason_code, placed_at FROM legal_holds WHERE patient_id=? AND released_at IS NULL", (patient_id,))
        hid = db.uid()
        with db.tx() as t:
            t.x("INSERT INTO legal_holds(id,patient_id,reason_code,placed_by,placed_at) VALUES(?,?,?,?,?)", (hid, patient_id, reason_code, actor, db.now()))
            db.audit(actor, "legal_hold.place", hid, {"reason_code": reason_code}, t=t)
    return {"id": hid, "patient_id": patient_id, "reason_code": reason_code}


def release_hold(hold_id: str, actor: str) -> bool:
    with db.context.as_system():
        h = db.q1("SELECT id FROM legal_holds WHERE id=? AND released_at IS NULL", (hold_id,))
        if not h:
            return False
        with db.tx() as t:
            t.x("UPDATE legal_holds SET released_by=?, released_at=? WHERE id=?", (actor, db.now(), hold_id))
            db.audit(actor, "legal_hold.release", hold_id, {}, t=t)
    return True


# ---------------------------------------------------------------- selection
def _where(c: Category, held: bool) -> str:
    w = f"{c.ts} < ?"
    if c.deleted_flag:
        w += " AND deleted=0"
    if c.owner:
        sub = "SELECT patient_id FROM legal_holds WHERE released_at IS NULL"
        w += f" AND {c.owner} {'IN' if held else 'NOT IN'} ({sub})"
    return w


def _cutoff(days: int, now: float | None) -> db.Epoch:
    return db.Epoch((now if now is not None else time.time()) - days * 86400)


def plan(env=os.environ, now: float | None = None) -> list[dict]:
    """What a run would do, without changing anything."""
    days = configured_days(env)
    held_any = bool(active_holds())
    out = []
    with db.context.as_system():
        for c in CATEGORIES:
            d = days[c.name]
            row = {"category": c.name, "label": c.label, "days": d, "configured": d is not None}
            if d is None:
                out.append({**row, "eligible": 0, "held_excluded": 0, "note": "not configured: records are kept"})
                continue
            cut = _cutoff(d, now)
            if c.owner:
                elig = db.q1(f"SELECT COUNT(*) n FROM {c.table} WHERE {_where(c, False)}", (cut,))["n"]
                held = db.q1(f"SELECT COUNT(*) n FROM {c.table} WHERE {_where(c, True)}", (cut,))["n"]
            else:
                elig = 0 if held_any else db.q1(f"SELECT COUNT(*) n FROM {c.table} WHERE {c.ts} < ?", (cut,))["n"]
                held = 0
                if held_any:
                    row["note"] = "skipped while any legal hold is active"
            out.append({**row, "cutoff": float(cut), "eligible": elig, "held_excluded": held})
    return out


# ---------------------------------------------------------------- processors (each returns (purged, failed) for one batch of rows)
def _in(ids):
    return ",".join("?" * len(ids))


def _conversations(rows):
    ids = [r["id"] for r in rows]
    with db.tx() as t:
        t.x(f"DELETE FROM messages WHERE conversation_id IN ({_in(ids)})", tuple(ids))
        t.x(f"DELETE FROM conversations WHERE id IN ({_in(ids)})", tuple(ids))
    return len(ids), 0


def _simple(table):
    def run(rows):
        ids = [r["id"] for r in rows]
        db.x(f"DELETE FROM {table} WHERE id IN ({_in(ids)})", tuple(ids))
        return len(ids), 0
    return run


def _jobs(rows):
    ok = []
    for r in rows:
        p = r.get("input_path")
        if p:
            try:
                q = Path(p).resolve()
                if UPLOAD_DIR.resolve() in q.parents:
                    q.unlink(missing_ok=True)
            except OSError:
                continue                                                         # keep the row, try again next run
        ok.append(r["id"])
    if ok:
        db.x(f"DELETE FROM jobs WHERE id IN ({_in(ok)})", tuple(ok))
    return len(ok), len(rows) - len(ok)


def _files(table, with_pages):
    def run(rows):
        done = failed = 0
        for r in rows:
            try:
                storage.delete(r["stored_path"], strict=True)                    # file first: a failure keeps the row so the next run retries
            except storage.StorageError:
                failed += 1
                continue
            with db.tx() as t:
                if with_pages:
                    from .documents import store
                    store.delete_children(t, r["id"])
                    t.x("UPDATE documents SET deleted=1, stored_path='', filename='deleted' WHERE id=?", (r["id"],))
                else:
                    t.x("UPDATE images SET deleted=1, stored_path='' WHERE id=?", (r["id"],))
            done += 1
        return done, failed
    return run


PROCESSORS = {"conversations": _conversations, "notifications": _simple("notifications"), "jobs": _jobs, "documents": _files("documents", True),
              "images": _files("images", False), "quotes": _simple("quotes"), "care_plans": _simple("care_plans")}
EXTRA_COLS = {"jobs": ", input_path", "documents": ", stored_path", "images": ", stored_path"}


def _purge_audit(cut, batch):
    if db.is_postgres():
        return db.q1("SELECT app.purge_audit(?, ?) AS n", (cut, batch))["n"]
    with db.tx() as t:
        ids = [r["id"] for r in t.q("SELECT id FROM audit WHERE ts < ? ORDER BY ts LIMIT ?", (cut, batch))]
        if ids:
            t.x(f"DELETE FROM audit WHERE id IN ({_in(ids)})", tuple(ids))
    return len(ids)


# ---------------------------------------------------------------- the run
def run(dry_run: bool = True, env=os.environ, now: float | None = None, batch: int = 100, max_batches: int = 20, time_budget_s: float = 60.0, actor: str = "system:retention") -> dict:
    """Plan (dry run) or execute a bounded purge. Returns a report of counts only."""
    if not 1 <= batch <= 1000 or not 1 <= max_batches <= 10000:
        raise ValueError("batch must be 1..1000 and max_batches 1..10000")
    report = {"dry_run": dry_run, "categories": plan(env, now)}
    if dry_run:
        return report
    days, deadline = configured_days(env), time.monotonic() + time_budget_s
    held_any = bool(active_holds())
    with db.context.as_system():
        for entry in report["categories"]:
            c = BY_NAME[entry["category"]]
            entry["purged"] = entry["failed"] = 0
            if days[c.name] is None or (c.name == "audit" and held_any):
                continue
            cut = _cutoff(days[c.name], now)
            for _ in range(max_batches):
                if time.monotonic() > deadline:
                    entry["stopped"] = "time budget reached; the next run continues"
                    break
                if c.name == "audit":
                    n = _purge_audit(cut, batch)
                    entry["purged"] += n
                    if n < batch:
                        break
                    continue
                rows = db.q(f"SELECT id{EXTRA_COLS.get(c.name, '')} FROM {c.table} WHERE {_where(c, False)} ORDER BY {c.ts} LIMIT ?", (cut, batch))
                if not rows:
                    break
                done, failed = PROCESSORS[c.name](rows)
                entry["purged"] += done
                entry["failed"] += failed
                if done == 0 or len(rows) < batch:
                    break                                                         # nothing progressed (all failed), or the backlog is finished
            else:
                entry.setdefault("stopped", "batch limit reached; the next run continues")
            if entry["purged"] or entry["failed"]:
                db.audit(actor, "retention.purge", c.name, {"purged": entry["purged"], "failed": entry["failed"], "days": days[c.name]})
    return report


# ---------------------------------------------------------------- orphan files (local storage)
def orphan_files(grace_seconds: int = 86400, now: float | None = None) -> list[Path]:
    """Files in the private upload directory that no row references and that are older than the grace period (in-flight uploads are younger)."""
    if storage.backend_name() != "local" or not UPLOAD_DIR.exists():
        return []
    with db.context.as_system():
        refs = {str(Path(r["p"]).resolve()) for r in db.q("SELECT stored_path p FROM documents WHERE stored_path!='' UNION SELECT stored_path FROM images WHERE stored_path!='' "
                                                          "UNION SELECT input_path FROM jobs WHERE input_path IS NOT NULL AND input_path!=''")}
    cutoff = (now if now is not None else time.time()) - grace_seconds
    return [p for p in UPLOAD_DIR.rglob("*") if p.is_file() and str(p.resolve()) not in refs and p.stat().st_mtime < cutoff]


def delete_orphans(files: list[Path]) -> int:
    n = 0
    for p in files:
        try:
            if UPLOAD_DIR.resolve() in p.resolve().parents:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


# ---------------------------------------------------------------- erasure on request
def erase_patient(uid: str, actor: str | None = None) -> dict:
    """What a patient controls: uploads (files, text, fields), photos, conversations, messages, jobs, notifications. Care plans, quotations and the audit trail are kept
    (and reported). Raises Held while a legal hold is active."""
    if is_held(uid):
        db.audit(actor or uid, "patient.erasure_deferred", uid, {})
        raise Held()
    docs = db.q("SELECT id,stored_path FROM documents WHERE owner_id=? AND deleted=0", (uid,))
    for d in docs:
        storage.delete(d["stored_path"])
        from .documents import store
        store.delete_children(db, d["id"])
    db.x("UPDATE documents SET deleted=1, stored_path='', filename='deleted' WHERE owner_id=?", (uid,))
    imgs = db.q("SELECT id,stored_path FROM images WHERE owner_id=? AND deleted=0", (uid,))
    for i in imgs:
        storage.delete(i["stored_path"])
    db.x("UPDATE images SET deleted=1, stored_path='' WHERE owner_id=?", (uid,))
    convs = db.q("SELECT id FROM conversations WHERE owner_id=?", (uid,))
    for c in convs:
        db.x("DELETE FROM messages WHERE conversation_id=?", (c["id"],))
    db.x("DELETE FROM conversations WHERE owner_id=?", (uid,))
    db.x("DELETE FROM jobs WHERE owner_id=?", (uid,))
    with db.context.as_system():                              # the patient may read and mark their notifications; removing them is a server action
        db.x("DELETE FROM notifications WHERE user_id=?", (uid,))
    retained = {"care_plans": db.q1("SELECT COUNT(*) c FROM care_plans WHERE patient_id=?", (uid,))["c"],
                "quotations": db.q1("SELECT COUNT(*) c FROM quotes WHERE patient_id=?", (uid,))["c"]}
    db.audit(actor or uid, "patient.data_deleted", uid, {"documents": len(docs), "photos": len(imgs), "conversations": len(convs), "retained": retained})
    return {"deleted": {"documents": len(docs), "photos": len(imgs), "conversations": len(convs)}, "retained_by_policy": retained}

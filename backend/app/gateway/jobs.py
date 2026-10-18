"""Durable asynchronous job runner for inference tasks.

States (real, reported by the worker): queued -> processing -> completed | uncertain | rejected | failed | cancelled.
Bounded concurrency and queue depth (429 + Retry-After when full), per-task timeouts, cooperative cancellation, idempotency keys,
request IDs on every job and audit event, and input bytes deleted as soon as a job reaches a terminal state."""
from __future__ import annotations

import concurrent.futures as cf
import contextvars
import json
import os
import threading
import time
from pathlib import Path

from .. import db
from ..config import UPLOAD_DIR

TERMINAL = ("completed", "uncertain", "rejected", "failed", "cancelled")
MAX_WORKERS = int(os.environ.get("COSMO_JOB_WORKERS", "2"))
MAX_QUEUED = int(os.environ.get("COSMO_JOB_QUEUE_MAX", "50"))
MAX_PER_USER = int(os.environ.get("COSMO_JOB_PER_USER", "4"))
TIMEOUTS = {"research_predict": 120, "image_check": 60, "document_process": 180, "speech_transcribe": 180, "assistant_answer": 150}
JOB_DIR = UPLOAD_DIR / "_jobs"
JOB_DIR.mkdir(parents=True, exist_ok=True)

_pool = cf.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="job")
_inner = cf.ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="job-inner")   # runs handlers so the outer thread can enforce timeouts
_handlers: dict = {}
_lock = threading.Lock()


class QueueFull(Exception):
    def __init__(self, msg, retry_after=10):
        super().__init__(msg)
        self.retry_after = retry_after


def register(task: str, fn):
    """fn(owner_id, params: dict, data: bytes|None, ctx) -> (status, result_dict). Raise for failure."""
    _handlers[task] = fn


def ensure_schema():
    if not db.is_postgres():                                 # PostgreSQL gets this table and its indexes from the migrations (the app role cannot run DDL)
        db.x("""CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner_id TEXT, task TEXT, status TEXT, request_id TEXT, idempotency_key TEXT,
                params TEXT, input_path TEXT, result TEXT, error TEXT, created_at REAL, started_at REAL, finished_at REAL, cancel_requested INTEGER DEFAULT 0)""")
        db.x("CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner_id, created_at)")
        db.x("CREATE UNIQUE INDEX IF NOT EXISTS jobs_idem ON jobs(owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL")
    # a restart kills in-flight work: surface it honestly instead of leaving jobs "processing" forever
    db.x("UPDATE jobs SET status='failed', error='The service restarted while this job was running. Please submit it again.', finished_at=? WHERE status IN ('queued','processing')", (db.now(),))


def _public(j: dict) -> dict:
    return {"id": j["id"], "task": j["task"], "status": j["status"], "request_id": j["request_id"], "created_at": j["created_at"], "started_at": j["started_at"],
            "finished_at": j["finished_at"], "queue_ms": round((j["started_at"] - j["created_at"]) * 1000) if j["started_at"] else None,
            "processing_ms": round((j["finished_at"] - j["started_at"]) * 1000) if j["finished_at"] and j["started_at"] else None,
            "result": json.loads(j["result"]) if j["result"] else None, "error": j["error"]}


def get(job_id: str, owner_id: str | None = None) -> dict | None:
    j = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not j or (owner_id and j["owner_id"] != owner_id):
        return None
    return _public(j)


def submit(task: str, owner_id: str, params: dict, data: bytes | None, request_id: str, idem_key: str | None = None) -> tuple[dict, bool]:
    """Returns (job, created). With the same idempotency key the existing job is returned and nothing is re-run."""
    if task not in _handlers:
        raise KeyError(task)
    with _lock:
        if idem_key:
            ex = db.q1("SELECT * FROM jobs WHERE owner_id=? AND idempotency_key=?", (owner_id, idem_key))
            if ex:
                return _public(ex), False
        active = db.q1("SELECT COUNT(*) c FROM jobs WHERE status IN ('queued','processing')")["c"]
        mine = db.q1("SELECT COUNT(*) c FROM jobs WHERE owner_id=? AND status IN ('queued','processing')", (owner_id,))["c"]
        if active >= MAX_QUEUED:
            raise QueueFull("The service is busy. Please try again shortly.", 15)
        if mine >= MAX_PER_USER:
            raise QueueFull("You already have several tasks running. Please wait for them to finish.", 10)
        jid = db.uid()
        path = ""
        if data is not None:
            p = JOB_DIR / jid
            p.write_bytes(data)
            path = str(p)
        db.x("INSERT INTO jobs(id,owner_id,task,status,request_id,idempotency_key,params,input_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
             (jid, owner_id, task, "queued", request_id, idem_key, json.dumps(params), path, db.now()))
    _pool.submit(contextvars.copy_context().run, _run, jid)       # the worker keeps the submitting user's database identity
    return get(jid), True


def cancel(job_id: str, owner_id: str) -> dict | None:
    j = db.q1("SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, owner_id))
    if not j:
        return None
    if j["status"] in TERMINAL:
        return _public(j)
    db.x("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
    if j["status"] == "queued":
        _finish(job_id, "cancelled", None, None)
    return get(job_id)


def _finish(job_id: str, status: str, result: dict | None, error: str | None):
    j = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if j and j["status"] in TERMINAL:     # first terminal state wins (e.g. cancelled before a late result arrives)
        return
    db.x("UPDATE jobs SET status=?, result=?, error=?, finished_at=? WHERE id=?", (status, json.dumps(result) if result is not None else None, error, db.now(), job_id))
    if j and j["input_path"]:
        Path(j["input_path"]).unlink(missing_ok=True)   # data minimisation


def _run(job_id: str):
    j = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not j or j["status"] != "queued" or j["cancel_requested"]:
        return
    db.x("UPDATE jobs SET status='processing', started_at=? WHERE id=? AND status='queued'", (db.now(), job_id))
    data = Path(j["input_path"]).read_bytes() if j["input_path"] and Path(j["input_path"]).exists() else None
    ctx = {"request_id": j["request_id"], "job_id": job_id, "cancelled": lambda: bool((db.q1("SELECT cancel_requested c FROM jobs WHERE id=?", (job_id,)) or {"c": 0})["c"])}
    fut = _inner.submit(contextvars.copy_context().run, _handlers[j["task"]], j["owner_id"], json.loads(j["params"]), data, ctx)
    try:
        status, result = fut.result(timeout=TIMEOUTS.get(j["task"], 120))
        if ctx["cancelled"]():
            _finish(job_id, "cancelled", None, None)
        else:
            _finish(job_id, status, result, None)
    except cf.TimeoutError:
        fut.cancel()
        _finish(job_id, "failed", None, f"The task took longer than {TIMEOUTS.get(j['task'], 120)} seconds and was stopped.")
        db.audit(j["owner_id"], "job.timeout", job_id, {"task": j["task"], "request_id": j["request_id"]})
    except Exception as e:      # log the type for operators; never return internals to the client
        _finish(job_id, "failed", None, getattr(e, "public_message", None) or "The task could not be completed.")
        db.audit(j["owner_id"], "job.failed", job_id, {"task": j["task"], "request_id": j["request_id"], "error_type": type(e).__name__})


def stats(hours: int = 24) -> dict:
    cut = db.now() - hours * 3600
    rows = db.q("SELECT task,status,created_at,started_at,finished_at FROM jobs WHERE created_at>?", (cut,))
    out: dict = {}
    for r in rows:
        t = out.setdefault(r["task"], {"count": 0, "by_status": {}, "lat": [], "queue": []})
        t["count"] += 1
        t["by_status"][r["status"]] = t["by_status"].get(r["status"], 0) + 1
        if r["finished_at"] and r["started_at"]:
            t["lat"].append((r["finished_at"] - r["started_at"]) * 1000)
            t["queue"].append((r["started_at"] - r["created_at"]) * 1000)
    def pct(v, p):
        return round(sorted(v)[min(len(v) - 1, int(len(v) * p))]) if v else None
    for t in out.values():
        t["processing_ms_p50"], t["processing_ms_p95"], t["queue_ms_p95"] = pct(t["lat"], .5), pct(t["lat"], .95), pct(t["queue"], .95)
        t["failure_rate"] = round(t["by_status"].get("failed", 0) / t["count"], 3)
        del t["lat"], t["queue"]
    q = db.q1("SELECT COALESCE(SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END),0) q, COALESCE(SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END),0) p FROM jobs")
    return {"window_hours": hours, "tasks": out, "queued_now": q["q"] or 0, "processing_now": q["p"] or 0, "workers": MAX_WORKERS, "queue_limit": MAX_QUEUED}

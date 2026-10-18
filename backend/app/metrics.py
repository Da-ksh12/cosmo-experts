"""Prometheus text-format metrics without a client library.

Request counters and latency histograms are recorded by the middleware (labels: method, route TEMPLATE, status class; never raw paths or ids, so label
cardinality is bounded). Gauges for jobs, storage, backups and clinical-content state are computed at scrape time.
Exposed at GET /metrics (outside /api, not proxied to browsers) and only with `Authorization: Bearer $COSMO_METRICS_TOKEN`; without a configured token the
endpoint does not exist."""
from __future__ import annotations

import hmac
import json
import os
import shutil
import threading
import time

BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_lock = threading.Lock()
_req: dict[tuple, int] = {}
_hist: dict[tuple, list] = {}          # (method, route) -> [bucket counts..., +Inf count, sum]
START = time.time()


def observe(method: str, route: str, status: int, seconds: float):
    with _lock:
        k = (method, route, f"{status // 100}xx")
        _req[k] = _req.get(k, 0) + 1
        h = _hist.setdefault((method, route), [0] * (len(BUCKETS) + 1) + [0.0])
        for i, b in enumerate(BUCKETS):
            if seconds <= b:
                h[i] += 1
        h[len(BUCKETS)] += 1
        h[-1] += seconds


def reset():
    with _lock:
        _req.clear(); _hist.clear()


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def token_ok(header: str) -> bool:
    want = os.environ.get("COSMO_METRICS_TOKEN", "")
    got = header[7:] if header.lower().startswith("bearer ") else ""
    return bool(want) and hmac.compare_digest(got.encode(), want.encode())


def _gauges() -> list[tuple[str, str, float, dict]]:
    """(name, help, value, labels). Every collector is isolated: a failing one must not break the scrape."""
    from . import clinical_content, db, ratelimit, storage
    from .config import DATA_DIR
    g: list = []

    def safe(fn):
        try:
            fn()
        except Exception:
            g.append(("cosmo_collector_errors", "A metrics collector failed", 1, {"collector": fn.__name__}))

    def database():
        ok = db.q1("SELECT 1 x") is not None
        g.append(("cosmo_database_up", "1 if the application database answers", 1 if ok else 0, {}))

    def store():
        ok, _ = storage.healthy()
        g.append(("cosmo_private_storage_up", "1 if private file storage is reachable", 1 if ok else 0, {"backend": storage.backend_name()}))

    def jobs():
        from .gateway import jobs as j
        s = j.stats(1)
        g.append(("cosmo_jobs_queued", "Jobs waiting for a worker", s["queued_now"], {}))
        g.append(("cosmo_jobs_processing", "Jobs running now", s["processing_now"], {}))
        g.append(("cosmo_jobs_queue_limit", "Back-pressure limit for queued jobs", s["queue_limit"], {}))
        for task, t in s["tasks"].items():
            g.append(("cosmo_jobs_failure_ratio", "Failed share of jobs in the last hour", t["failure_rate"], {"task": task}))
            if t["processing_ms_p95"] is not None:
                g.append(("cosmo_jobs_processing_seconds_p95", "p95 processing time in the last hour", t["processing_ms_p95"] / 1000, {"task": task}))

    def backup():
        p = DATA_DIR / "backup_status.json"
        if p.is_file():
            s = json.loads(p.read_text(encoding="utf-8"))
            g.append(("cosmo_backup_last_success_timestamp_seconds", "Unix time of the last successful backup", s.get("last_success_ts", 0), {}))
            g.append(("cosmo_backup_last_run_ok", "1 if the last backup run succeeded", 1 if s.get("last_ok") else 0, {}))
        else:
            g.append(("cosmo_backup_last_success_timestamp_seconds", "Unix time of the last successful backup", 0, {}))
            g.append(("cosmo_backup_last_run_ok", "1 if the last backup run succeeded", 0, {}))

    def disk():
        u = shutil.disk_usage(DATA_DIR)
        g.append(("cosmo_data_disk_free_bytes", "Free bytes on the data volume", u.free, {}))
        g.append(("cosmo_data_disk_total_bytes", "Size of the data volume", u.total, {}))

    def content():
        s = clinical_content.status()
        g.append(("cosmo_kb_articles_approved", "Clinician-approved knowledge-base articles in use", s["kb_approved"], {}))
        g.append(("cosmo_urgent_policy_approved", "1 if a clinician-approved urgent-symptom policy is active (0 = built-in default)", 1 if s["urgent_policy_approved"] else 0, {}))

    def limiter():
        lim = ratelimit.get()
        g.append(("cosmo_ratelimit_store_errors_total", "Rate-limit store failures (fell back to in-process limiting)", lim.errors, {"store": lim.store.name}))

    def ai_engine():
        from .ai.resources import resource_manager
        snap = resource_manager().snapshot()
        g.append(("cosmo_ai_pending", "AI runs executing or waiting", snap["pending"], {}))
        for key, n in snap["runs"].items():
            cap, status = key.split("|", 1)
            g.append(("cosmo_ai_runs_total", "AI capability runs by capability and status", n, {"capability": cap, "status": status}))
        for cap, v in snap["seconds"].items():
            g.append(("cosmo_ai_run_seconds_sum", "Total seconds spent in AI capability runs", v["sum"], {"capability": cap}))

    for fn in (database, store, jobs, backup, disk, content, limiter, ai_engine):
        safe(fn)
    g.append(("cosmo_process_uptime_seconds", "Seconds since the process started", time.time() - START, {}))
    return g


def render() -> str:
    lines = ["# HELP cosmo_http_requests_total HTTP requests by method, route template and status class", "# TYPE cosmo_http_requests_total counter"]
    with _lock:
        for (m, r, s), n in sorted(_req.items()):
            lines.append(f'cosmo_http_requests_total{{method="{_esc(m)}",route="{_esc(r)}",status="{s}"}} {n}')
        lines += ["# HELP cosmo_http_request_duration_seconds Request latency", "# TYPE cosmo_http_request_duration_seconds histogram"]
        for (m, r), h in sorted(_hist.items()):
            base = f'method="{_esc(m)}",route="{_esc(r)}"'
            for i, b in enumerate(BUCKETS):
                lines.append(f'cosmo_http_request_duration_seconds_bucket{{{base},le="{b}"}} {h[i]}')
            lines.append(f'cosmo_http_request_duration_seconds_bucket{{{base},le="+Inf"}} {h[len(BUCKETS)]}')
            lines.append(f"cosmo_http_request_duration_seconds_sum{{{base}}} {h[-1]:.6f}")
            lines.append(f"cosmo_http_request_duration_seconds_count{{{base}}} {h[len(BUCKETS)]}")
    seen = set()
    for name, help_, val, labels in _gauges():
        if name not in seen:
            seen.add(name)
            kind = "counter" if name.endswith("_total") else "gauge"
            lines += [f"# HELP {name} {help_}", f"# TYPE {name} {kind}"]
        lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
        lines.append(f"{name}{{{lab}}} {val}" if lab else f"{name} {val}")
    return "\n".join(lines) + "\n"

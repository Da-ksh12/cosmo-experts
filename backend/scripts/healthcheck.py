"""Out-of-band health check for cron / systemd timers / Windows Task Scheduler. Independent of Prometheus, so alerting still works on a single small host.

    python -m scripts.healthcheck --url http://127.0.0.1:8000 [--data-dir DIR] [--backup-max-age-hours 26] [--min-free-gb 2] [--webhook URL]

Checks: liveness, readiness (database and private storage), age and result of the last backup, free disk space, queue back-pressure.
Exit codes: 0 healthy, 1 one or more failed checks, 2 usage error. With --webhook a failure is POSTed as {"text": "..."} (Slack/Mattermost/Teams-compatible);
repeats of the same failure are suppressed for --repeat-hours (default 6) using a small state file, and a recovery notice is sent once."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _get(url: str, timeout=10) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - operator-supplied URL
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        return 0, {"error": type(e).__name__}


def run(url: str, data_dir: Path, backup_max_age_h: float = 26, min_free_gb: float = 2, now: float | None = None) -> list[dict]:
    now = now or time.time()
    out = []

    def add(name, ok, detail):
        out.append({"check": name, "ok": bool(ok), "detail": detail})
    code, body = _get(url.rstrip("/") + "/api/v1/health/live")
    add("liveness", code == 200, f"HTTP {code}")
    code, body = _get(url.rstrip("/") + "/api/v1/health/ready")
    bad = [c["name"] for c in body.get("core", []) if not c.get("ready")]
    add("readiness", code == 200 and not bad, f"HTTP {code}" + (f", not ready: {', '.join(bad)}" if bad else ""))
    q = body.get("queue") or {}
    if q:
        add("queue", q.get("queued_now", 0) < q.get("queue_limit", 1), f"{q.get('queued_now')} queued of limit {q.get('queue_limit')}")
    st = data_dir / "backup_status.json"
    if not st.is_file():
        add("backup", False, "no backup has ever been recorded")
    else:
        try:
            s = json.loads(st.read_text(encoding="utf-8"))
        except ValueError:
            s = {}
        age_h = (now - s.get("last_success_ts", 0)) / 3600 if s.get("last_success_ts") else None
        add("backup", bool(s.get("last_ok")) and age_h is not None and age_h <= backup_max_age_h,
            f"last success {age_h:.1f} h ago" if age_h is not None else "never succeeded" + (f"; last error: {s.get('last_error')}" if s.get("last_error") else ""))
    free = shutil.disk_usage(data_dir).free / 1e9
    add("disk", free >= min_free_gb, f"{free:.1f} GB free (minimum {min_free_gb} GB)")
    return out


def notify(webhook: str, results: list[dict], state_file: Path, repeat_h: float, now: float | None = None) -> str:
    """Returns what was sent: 'alert', 'recovered' or '' (suppressed / nothing to say)."""
    now = now or time.time()
    failed = [r for r in results if not r["ok"]]
    sig = hashlib.sha256(json.dumps(sorted(r["check"] for r in failed)).encode()).hexdigest()
    try:
        prev = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    kind, text = "", ""
    if failed and (prev.get("sig") != sig or now - prev.get("sent", 0) >= repeat_h * 3600):
        kind, text = "alert", "Cosmo Experts health check FAILED: " + "; ".join(f"{r['check']} ({r['detail']})" for r in failed)
    elif not failed and prev.get("sig"):
        kind, text = "recovered", "Cosmo Experts health check recovered: all checks pass."
    if kind:
        req = urllib.request.Request(webhook, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10).close()  # noqa: S310
        except (urllib.error.URLError, TimeoutError, OSError):
            return ""                                        # do not record success: the next run retries
        state_file.write_text(json.dumps({"sig": sig if failed else "", "sent": now}), encoding="utf-8")
    return kind


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--data-dir", type=Path)
    ap.add_argument("--backup-max-age-hours", type=float, default=26)
    ap.add_argument("--min-free-gb", type=float, default=2)
    ap.add_argument("--webhook")
    ap.add_argument("--state-file", type=Path)
    ap.add_argument("--repeat-hours", type=float, default=6)
    a = ap.parse_args(argv)
    if a.data_dir is None:
        from app.config import DATA_DIR
        a.data_dir = DATA_DIR
    results = run(a.url, a.data_dir, a.backup_max_age_hours, a.min_free_gb)
    print(json.dumps({"healthy": all(r["ok"] for r in results), "checks": results}, indent=2))
    if a.webhook:
        notify(a.webhook, results, a.state_file or (a.data_dir / "healthcheck_state.json"), a.repeat_hours)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main())

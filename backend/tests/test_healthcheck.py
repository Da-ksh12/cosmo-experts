"""Out-of-band health check: real HTTP against a real server, backup freshness, disk, and de-duplicated webhook alerts."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import uvicorn

from app.config import DATA_DIR
from app.main import app
from scripts import healthcheck as hc


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def server():
    port = _port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True); t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True; t.join(10)


def _status(tmp_path, **kw):
    (tmp_path / "backup_status.json").write_text(json.dumps(kw))


def by_name(res):
    return {r["check"]: r for r in res}


def test_healthy_system_passes_every_check(server, tmp_path):
    _status(tmp_path, last_ok=True, last_success_ts=time.time() - 3600)
    res = by_name(hc.run(server, tmp_path))
    assert all(r["ok"] for r in res.values()), res
    assert {"liveness", "readiness", "queue", "backup", "disk"} <= set(res)
    assert "1.0 h ago" in res["backup"]["detail"]


def test_unreachable_server_fails_liveness_and_readiness(tmp_path):
    _status(tmp_path, last_ok=True, last_success_ts=time.time())
    res = by_name(hc.run(f"http://127.0.0.1:{_port()}", tmp_path))
    assert not res["liveness"]["ok"] and not res["readiness"]["ok"] and res["backup"]["ok"]


@pytest.mark.parametrize("status,ok", [
    (None, False),                                                                   # never backed up
    ({"last_ok": True, "last_success_ts": time.time() - 30 * 3600}, False),          # stale
    ({"last_ok": False, "last_success_ts": time.time() - 3600, "last_error": "OSError: disk full"}, False),   # last run failed
    ({"last_ok": False, "last_error": "boom"}, False),                               # never succeeded
    ({"last_ok": True, "last_success_ts": time.time() - 25 * 3600}, True),           # within 26 h
])
def test_backup_freshness_rules(server, tmp_path, status, ok):
    if status is not None:
        _status(tmp_path, **status)
    r = by_name(hc.run(server, tmp_path))["backup"]
    assert r["ok"] is ok, r


def test_low_disk_space_fails(server, tmp_path):
    _status(tmp_path, last_ok=True, last_success_ts=time.time())
    assert not by_name(hc.run(server, tmp_path, min_free_gb=10**9))["disk"]["ok"]


def test_corrupt_status_file_is_a_failure_not_a_crash(server, tmp_path):
    (tmp_path / "backup_status.json").write_text("{not json")
    assert not by_name(hc.run(server, tmp_path))["backup"]["ok"]


def test_cli_exit_codes_and_json(server, tmp_path, capsys):
    _status(tmp_path, last_ok=True, last_success_ts=time.time())
    assert hc.main(["--url", server, "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True
    assert hc.main(["--url", f"http://127.0.0.1:{_port()}", "--data-dir", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["healthy"] is False


# ---------------------------------------------------------------- webhook
@pytest.fixture
def hook():
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200); self.end_headers()

        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{srv.server_port}/hook", got
    srv.shutdown()


BAD = [{"check": "backup", "ok": False, "detail": "never"}, {"check": "disk", "ok": True, "detail": "ok"}]
GOOD = [{"check": "backup", "ok": True, "detail": "ok"}]


def test_alert_is_sent_once_repeated_after_the_interval_and_recovery_is_announced(hook, tmp_path):
    url, got = hook
    st = tmp_path / "state.json"
    assert hc.notify(url, BAD, st, 6, now=1000) == "alert"
    assert hc.notify(url, BAD, st, 6, now=1000 + 3600) == ""                          # suppressed
    assert hc.notify(url, BAD, st, 6, now=1000 + 7 * 3600) == "alert"                 # reminder after 6 h
    other = BAD + [{"check": "readiness", "ok": False, "detail": "x"}]
    assert hc.notify(url, other, st, 6, now=1000 + 7 * 3600 + 5) == "alert"           # a different failure set is reported immediately
    assert hc.notify(url, GOOD, st, 6, now=1000 + 8 * 3600) == "recovered"
    assert hc.notify(url, GOOD, st, 6, now=1000 + 9 * 3600) == ""                     # healthy and nothing to say
    assert len(got) == 4 and "FAILED" in got[0]["text"] and "backup (never)" in got[0]["text"] and "recovered" in got[3]["text"]


def test_a_failed_webhook_is_retried_on_the_next_run(tmp_path):
    st = tmp_path / "state.json"
    assert hc.notify(f"http://127.0.0.1:{_port()}/hook", BAD, st, 6, now=1) == "" and not st.exists()

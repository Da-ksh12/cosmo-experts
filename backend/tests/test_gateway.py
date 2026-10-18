import io
import json
import threading
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db
from app.gateway import jobs
from app.main import app
from tests.test_api import lab_pdf, tok


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait(client, headers, jid, until=jobs.TERMINAL, timeout=60):
    seen = []
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/v1/jobs/{jid}", headers=headers).json()
        if not seen or seen[-1] != j["status"]:
            seen.append(j["status"])
        if j["status"] in until:
            return j, seen
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {seen}")


def jpg(seed=1, size=500):
    img = np.random.default_rng(seed).integers(60, 200, (size, size, 3), dtype=np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_image_job_returns_the_same_result_as_the_sync_route(client):
    h = tok(client, "patient1")
    r = client.post("/api/v1/images/check", data={"region": "face"}, files={"file": ("f.jpg", jpg())}, headers=h)
    assert r.status_code == 202 and r.headers["Location"].endswith(r.json()["id"]) and r.headers["X-Request-ID"]
    j, seen = wait(client, h, r.json()["id"])
    assert j["status"] == "rejected" and seen[-1] == "rejected"
    rep = j["result"]["report"]
    assert rep["verdict"] == "rejected" and rep["request_id"] == r.headers["X-Request-ID"] and rep["processing_ms"] >= 0 and rep["corrective_action"]
    sync = client.post("/api/images", data={"region": "face"}, files={"file": ("f.jpg", jpg())}, headers=h).json()["report"]
    assert sync["reason"] == rep["reason"]
    assert j["processing_ms"] is not None and j["queue_ms"] is not None
    assert client.get(f"/api/v1/jobs/{j['id']}", headers=tok(client, "patient2")).status_code == 404       # isolation


def test_document_job_and_input_file_deleted(client):
    h = tok(client, "patient1")
    r = client.post("/api/v1/documents", data={"category": "laboratory_report"},
                    files={"file": ("lab.pdf", lab_pdf(["Hemoglobin     10.2     g/dL      12.0 - 15.5"]))}, headers=h)
    assert r.status_code == 202
    j, _ = wait(client, h, r.json()["id"])
    assert j["status"] == "completed" and j["result"]["fields"] == 1 and j["result"]["doc_type"] == "laboratory_report"
    assert not (jobs.JOB_DIR / j["id"]).exists()                      # uploaded bytes are removed once the job is terminal
    bad = client.post("/api/v1/documents", files={"file": ("x.exe", b"MZ")}, headers=h)
    assert bad.status_code == 422                                      # validated before queueing
    enc = client.post("/api/v1/documents", files={"file": ("e.pdf", lab_pdf(["A 1 mg 1 - 2"], encrypt=True))}, headers=h)
    j2, _ = wait(client, h, enc.json()["id"])
    assert j2["status"] == "rejected" and "password" in j2["result"]["error"].lower()


def test_states_timeout_cancel_idempotency_backpressure(client):
    h = tok(client, "patient1")
    calls = []
    gate = threading.Event()

    def slow(owner, params, data, ctx):
        calls.append(params.get("n"))
        if params.get("mode") == "hang":
            time.sleep(3)
            return "completed", {"late": True}
        if params.get("mode") == "boom":
            raise RuntimeError("secret internal detail: /etc/passwd")
        gate.wait(30)
        return "completed", {"n": params.get("n")}
    jobs.register("test_slow", slow)
    jobs.TIMEOUTS["test_slow"] = 1

    # real state transitions: queued/processing -> completed
    j1, _ = jobs.submit("test_slow", "patient1", {"n": 1}, None, "rid-1", None)
    j, seen = wait(client, h, j1["id"], until=("processing",))
    gate.set()
    j, seen2 = wait(client, h, j1["id"])
    assert j["status"] == "completed" and j["result"] == {"n": 1}
    gate.clear()

    # idempotency: same key => same job, handler not run again
    a, created_a = jobs.submit("test_slow", "patient1", {"n": 2}, None, "rid-2", "key-1")
    gate.set()
    b, created_b = jobs.submit("test_slow", "patient1", {"n": 2}, None, "rid-3", "key-1")
    assert a["id"] == b["id"] and created_a and not created_b
    wait(client, h, a["id"])
    assert calls.count(2) == 1

    # timeout => failed with a clear message, and the late result is ignored
    t, _ = jobs.submit("test_slow", "patient1", {"mode": "hang"}, None, "rid-4", None)
    j, _ = wait(client, h, t["id"])
    assert j["status"] == "failed" and "longer than" in j["error"]
    time.sleep(2.5)
    assert client.get(f"/api/v1/jobs/{t['id']}", headers=h).json()["result"] is None

    # handler exceptions never leak internals
    e, _ = jobs.submit("test_slow", "patient1", {"mode": "boom"}, None, "rid-5", None)
    j, _ = wait(client, h, e["id"])
    assert j["status"] == "failed" and "passwd" not in json.dumps(j) and j["error"] == "The task could not be completed."

    jobs.TIMEOUTS["test_slow"] = 60
    # cancellation of a queued job
    gate.clear()
    jobs.MAX_PER_USER = 10
    blockers = [jobs.submit("test_slow", "patient2", {"n": 100 + i}, None, "rid", None)[0] for i in range(jobs.MAX_WORKERS)]
    time.sleep(0.3)
    q, _ = jobs.submit("test_slow", "patient2", {"n": 999}, None, "rid", None)
    assert client.post(f"/api/v1/jobs/{q['id']}/cancel", headers=tok(client, "patient1")).status_code == 404
    c = client.post(f"/api/v1/jobs/{q['id']}/cancel", headers=tok(client, "patient2")).json()
    assert c["status"] == "cancelled"

    # backpressure: per-user limit and queue limit return 429 + Retry-After
    jobs.MAX_PER_USER = len(blockers) + 0
    r = client.post("/api/v1/assistant/answer", json={"conversation_id": "x", "text": "hi"}, headers=tok(client, "patient2"))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    jobs.MAX_PER_USER = 4
    gate.set()
    for b_ in blockers:
        wait(client, tok(client, "patient2"), b_["id"])


def test_sse_streams_real_state_changes(client):
    h = tok(client, "patient1")
    r = client.post("/api/v1/images/check", data={"region": "body"}, files={"file": ("f.jpg", jpg(3))}, headers=h)
    with client.stream("GET", r.json()["events_url"], headers=h) as s:
        body = "".join(s.iter_text())
    events = [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ")]
    assert events and events[-1]["status"] in jobs.TERMINAL and all(e["id"] == r.json()["id"] for e in events)


def test_restart_recovery_marks_inflight_jobs_failed(client):
    db.x("INSERT INTO jobs(id,owner_id,task,status,request_id,params,created_at) VALUES('stuck','patient1','test_slow','processing','r','{}',?)", (db.now(),))
    jobs.ensure_schema()
    j = jobs.get("stuck", "patient1")
    assert j["status"] == "failed" and "restarted" in j["error"]


def test_health_ready_metrics(client):
    assert client.get("/api/v1/health/live").json() == {"status": "alive"}
    r = client.get("/api/v1/health/ready")
    j = r.json()
    assert r.status_code == 200 and j["status"] == "ready" and {c["name"] for c in j["core"]} == {"database", "private_storage"}
    names = {c["name"]: c for c in j["optional"]}
    assert set(names) == {"ocr", "speech_to_text", "text_to_speech", "embeddings", "llm"}
    assert names["llm"]["ready"] is False and "llm" in j["degraded"]       # honest: no LLM is enabled
    assert client.get("/api/v1/metrics", headers=tok(client, "patient1")).status_code == 403
    m = client.get("/api/v1/metrics", headers=tok(client, "admin1")).json()
    assert m["tasks"]["image_check"]["count"] >= 1 and m["tasks"]["image_check"]["processing_ms_p50"] is not None and m["workers"] >= 1


def test_security_headers_and_request_id_echo(client):
    r = client.get("/api/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123" and r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"


def test_rate_limit_on_login(client, monkeypatch):
    from app import middleware
    monkeypatch.delenv("COSMO_RATE_LIMIT", raising=False)
    middleware.reset()
    codes = [client.post("/api/auth/login", json={"user_id": "patient1", "password": "wrong"}).status_code for _ in range(10)]
    assert codes[:8] == [401] * 8 and codes[8] == 429
    r = client.post("/api/auth/login", json={"user_id": "patient1", "password": "wrong"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    middleware.reset()


def test_speech_job_with_undecodable_audio_fails_cleanly(client):
    h = tok(client, "patient1")
    r = client.post("/api/v1/speech/transcribe", data={"language": "en"}, files={"file": ("q.webm", b"not audio")}, headers=h)
    if r.status_code == 503:
        pytest.skip("local speech model not installed")
    j, _ = wait(client, h, r.json()["id"])
    assert j["status"] == "failed" and "couldn't read that recording" in j["error"]
    assert "Traceback" not in json.dumps(j) and "av." not in json.dumps(j)


def test_tts_voice_discovery_is_consistent_across_threads():
    """Regression: on Windows the voice list was empty on some request threads (COM thread affinity)."""
    from concurrent.futures import ThreadPoolExecutor
    from app.speech import adapters
    if not adapters.Pyttsx3Adapter().voices():
        pytest.skip("no offline voices installed on this machine")
    with ThreadPoolExecutor(6) as ex:
        counts = list(ex.map(lambda _: len(adapters.get_tts().voices()), range(12)))
    assert len(set(counts)) == 1 and counts[0] > 0
    wav = adapters.get_tts().synthesize("Hello", "en-IN", 1.0)
    assert wav[:4] == b"RIFF"
    with pytest.raises(RuntimeError):
        adapters.get_tts().synthesize("नमस्ते", "hi-IN", 1.0)       # no Hindi voice installed: refuse rather than mispronounce

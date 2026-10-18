"""LLM path: backend adapters, engine integration and the REAL llama.cpp model (slow tests; skipped when the GGUF is absent or COSMO_SKIP_SLOW=1)."""
import http.server
import json
import os
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.assistant import grounded, llm
from app.assistant.llm import Generation, LLMBackend, LLMError, LlamaCppBackend, OpenAICompatBackend
from app.main import app
from tests.test_api import lab_pdf, tok

GGUF = Path(__file__).resolve().parent.parent / "data" / "models" / "llm" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
slow = pytest.mark.skipif(not GGUF.is_file() or os.environ.get("COSMO_SKIP_SLOW") == "1", reason="real GGUF model not available or slow tests disabled")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_llm(monkeypatch):
    yield
    llm.reset_backend(False)
    monkeypatch.delenv("COSMO_ASSISTANT_MODE", raising=False)


# ---------------------------------------------------------------- adapter: OpenAI-compatible server (a real local HTTP server)
class Fake(http.server.BaseHTTPRequestHandler):
    mode, hits = "ok", []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.hits.append(body)
        if Fake.mode == "500":
            self.send_response(500); self.end_headers(); return
        if Fake.mode == "slow":
            time.sleep(2.5)
        out = json.dumps({"choices": [{"message": {"content": '{"claims":[]}'}}], "usage": {"completion_tokens": 7}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


@pytest.fixture()
def openai_server():
    Fake.mode, Fake.hits = "ok", []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_openai_compatible_backend_roundtrip_and_errors(openai_server):
    b = OpenAICompatBackend(openai_server, "local-model")
    g = b.generate("sys", "user", max_tokens=50, json_schema=grounded.SCHEMA)
    assert g.text == '{"claims":[]}' and g.completion_tokens == 7 and g.model == "openai-compat:local-model"
    sent = Fake.hits[-1]
    assert sent["temperature"] == 0 and sent["max_tokens"] == 50 and sent["response_format"] == {"type": "json_object"} and sent["messages"][0]["role"] == "system"
    Fake.mode = "500"
    with pytest.raises(LLMError):
        b.generate("s", "u")
    Fake.mode = "slow"
    with pytest.raises(LLMError):
        b.generate("s", "u", timeout=0.5)


def test_openai_backend_refuses_non_local_plaintext():
    with pytest.raises(LLMError):
        OpenAICompatBackend("http://llm.example.com", "m")
    OpenAICompatBackend("https://llm.example.com", "m")       # TLS endpoints are allowed
    with pytest.raises(LLMError):
        OpenAICompatBackend("http://10.0.0.5.evil.com", "m")


def test_backend_selection_from_environment(monkeypatch, openai_server):
    monkeypatch.delenv("COSMO_LLM_URL", raising=False); monkeypatch.delenv("COSMO_LLM_ENABLED", raising=False); monkeypatch.delenv("COSMO_LLM_GGUF", raising=False)
    llm.reset_backend(False)
    assert llm.get_backend() is None                                         # nothing configured: assistant stays extractive
    monkeypatch.setenv("COSMO_LLM_URL", openai_server); llm.reset_backend(False)
    assert isinstance(llm.get_backend(), OpenAICompatBackend)
    monkeypatch.delenv("COSMO_LLM_URL"); monkeypatch.setenv("COSMO_LLM_GGUF", str(GGUF)); llm.reset_backend(False)
    assert llm.get_backend() is None                                         # a model file alone does not enable it
    if GGUF.is_file():
        monkeypatch.setenv("COSMO_LLM_ENABLED", "1"); llm.reset_backend(False)
        assert isinstance(llm.get_backend(), LlamaCppBackend)


# ---------------------------------------------------------------- engine integration with scripted backends (fast, deterministic)
class Counting(LLMBackend):
    name = "counting-double"

    def __init__(self, out):
        self.out, self.calls = out, 0

    def generate(self, system, user, max_tokens=400, json_schema=None, timeout=120):
        self.calls += 1
        if isinstance(self.out, Exception):
            raise self.out
        return Generation(self.out if isinstance(self.out, str) else json.dumps(self.out), 0.01, 9, self.name)


def ask(client, headers, cid, text):
    return client.post(f"/api/conversations/{cid}/messages", json={"text": text}, headers=headers).json()


@pytest.fixture()
def patient(client):
    h = tok(client, "patient1")
    client.post("/api/documents", files={"file": ("f.pdf", lab_pdf(["Ferritin     8     ng/mL     15 - 150  L"]))}, headers=h)
    return h, client.post("/api/conversations", headers=h).json()["id"]


def test_llm_mode_off_by_default_never_calls_the_backend(client, patient, monkeypatch):
    h, cid = patient
    b = Counting({"claims": []}); llm.reset_backend(b)
    out = ask(client, h, cid, "What does my ferritin result say?")
    assert out["mode"] == "extractive" and b.calls == 0


def test_guardrails_run_before_the_model(client, patient, monkeypatch):
    h, cid = patient
    monkeypatch.setenv("COSMO_ASSISTANT_MODE", "llm")
    b = Counting({"claims": []}); llm.reset_backend(b)
    assert ask(client, h, cid, "Which medicine should I take and what dose?")["reason"] == "clinical_decision"
    assert ask(client, h, cid, "I have chest pain")["reason"] == "urgent"
    assert ask(client, h, cid, "Who won the cricket world cup in nineteen eighty three")["abstained"]
    assert b.calls == 0                                                       # refusal, emergency and no-evidence cases never reach the model


def test_verified_claims_are_shown_with_provenance_labels(client, patient, monkeypatch):
    h, cid = patient
    monkeypatch.setenv("COSMO_ASSISTANT_MODE", "llm")
    b = Counting({"claims": [{"text": "Your report lists ferritin as 8 ng/mL with a printed range of 15 - 150.", "source_ids": [1], "quote": "Ferritin 8 ng/mL 15 - 150"}]})
    llm.reset_backend(b)
    out = ask(client, h, cid, "What does my ferritin result say?")
    assert b.calls == 1 and out["mode"] == "llm_verified" and out["model"] == "counting-double"
    assert "From your report:" in out["answer"] and out["sources"][0]["provenance"] == "report_fact" and out["dropped_unverified_claims"] == 0


@pytest.mark.parametrize("script", [
    "not json at all",
    {"claims": [{"text": "Ferritin is 8 and normal.", "source_ids": [1], "quote": "Ferritin 8 ng/mL"}]},         # interpretive wording
    {"claims": [{"text": "You should take iron tablets.", "source_ids": [1], "quote": "Ferritin 8 ng/mL"}]},    # prescriptive
    {"claims": [{"text": "Ferritin is 80.", "source_ids": [1], "quote": "Ferritin 8 ng/mL"}]},                  # wrong number
    {"claims": [{"text": "Ferritin is 8.", "source_ids": [9], "quote": "Ferritin 8"}]},                         # invented source
    LLMError("backend down"),
])
def test_unverified_or_failed_model_output_falls_back_to_extractive(client, patient, monkeypatch, script):
    h, cid = patient
    monkeypatch.setenv("COSMO_ASSISTANT_MODE", "llm")
    llm.reset_backend(Counting(script))
    out = ask(client, h, cid, "What does my ferritin result say?")
    assert out["mode"] == "extractive" and not out["abstained"] and "Ferritin" in out["answer"]
    assert "normal" not in out["answer"].lower().replace("approved", "") and "tablet" not in out["answer"].lower()


# ---------------------------------------------------------------- the REAL model (llama.cpp, CPU)
@pytest.fixture(scope="module")
def real():
    return LlamaCppBackend(str(GGUF), n_ctx=2048, threads=2)


PASSAGES = [{"text": "Ferritin  8  ng/mL  15 - 150  L", "kind": "document"}, {"text": "Hemoglobin  10.2  g/dL  12.0 - 15.5", "kind": "document"}]


@slow
def test_real_model_returns_schema_constrained_json_and_is_deterministic(real):
    g1 = real.generate(grounded.SYSTEM, grounded.build_prompt("What does my ferritin result say?", PASSAGES), max_tokens=120, json_schema=grounded.SCHEMA)
    parsed = json.loads(g1.text)                                              # grammar-constrained: always valid JSON
    assert isinstance(parsed["claims"], list) and g1.completion_tokens > 0 and g1.seconds > 0 and g1.model.startswith("llama.cpp:")
    g2 = real.generate(grounded.SYSTEM, grounded.build_prompt("What does my ferritin result say?", PASSAGES), max_tokens=120, json_schema=grounded.SCHEMA)
    assert g1.text == g2.text                                                 # temperature 0 => reproducible


@slow
def test_real_model_output_always_passes_through_the_verifier(real):
    r = grounded.generate(real, "What range is printed for hemoglobin?", PASSAGES, max_tokens=150)
    assert r.json_ok and r.tokens > 0 and r.model.startswith("llama.cpp:")
    for c in r.claims:                                                        # anything that survived really is supported by its source
        src = PASSAGES[c.source_index]["text"]
        assert grounded._norm(c.quote) in grounded._norm(src)
        assert all(n in src for n in grounded.NUM.findall(c.text))
        assert not grounded.PRESCRIPTIVE.search(c.text)
    again, dropped = grounded.verify(json.loads(r.raw), PASSAGES)
    assert [c.text for c in again] == [c.text for c in r.claims] and len(dropped) == len(r.dropped)       # verification is idempotent


@slow
def test_real_model_through_the_assistant_api(client, patient, monkeypatch):
    h, cid = patient
    monkeypatch.setenv("COSMO_ASSISTANT_MODE", "llm"); monkeypatch.setenv("COSMO_LLM_ENABLED", "1"); monkeypatch.setenv("COSMO_LLM_GGUF", str(GGUF))
    llm.reset_backend(False)
    assert isinstance(llm.get_backend(), LlamaCppBackend)
    out = ask(client, h, cid, "What does my ferritin result say?")
    assert out["mode"] in ("llm_verified", "extractive") and not out["abstained"] and out["sources"]
    if out["mode"] == "llm_verified":
        assert out["model"].startswith("llama.cpp:") and all(s["provenance"] in ("report_fact", "approved_reference", "quotation") for s in out["sources"])
        assert not grounded.PRESCRIPTIVE.search(out["answer"].split("\n", 1)[1])


def test_no_model_state_survives_from_one_request_to_the_next():
    """Each generation starts from a clean model state, so an answer never depends on which request came before it (and one person's prompt prefix is never reused for another's)."""
    calls = []

    class FakeLlama:
        def reset(self):
            calls.append("reset")

        def create_chat_completion(self, **kw):
            calls.append("generate")
            return {"choices": [{"message": {"content": "{}"}}], "usage": {"completion_tokens": 1}}
    b = LlamaCppBackend("unused.gguf")
    b._llm = FakeLlama()
    b.generate("system", "first person's prompt")
    b.generate("system", "second person's prompt")
    assert calls == ["reset", "generate", "reset", "generate"]

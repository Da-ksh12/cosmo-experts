"""Release gates for language models in patient-facing roles (ai/gates.py) and the agent's provider abstraction (agent/provider.py). The passing evaluation records written below are
synthetic test fixtures that exist only to exercise the gate logic; they say nothing about any real model."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.agent import planner, provider
from app.ai import gates
from app.assistant import llm
from app.assistant.llm import Generation, LLMBackend
from app.main import app

REAL_RECORD = Path(__file__).resolve().parent.parent.parent / "docs" / "eval" / "llm_qwen2.5-1.5b-instruct-q4_k_m.json"


class Scripted(LLMBackend):
    name = "scripted-test-model"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def generate(self, system, user, max_tokens=400, json_schema=None, timeout=120):
        self.calls.append((system, user))
        return Generation(self.reply if isinstance(self.reply, str) else json.dumps(self.reply), 0.0, 3, self.name)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:                      # starting the application creates the database the approval records live in
        yield c


@pytest.fixture(autouse=True)
def _clean(client):
    yield
    llm.reset_backend(False)
    db.x("DELETE FROM settings WHERE key LIKE 'approval:%'")


def approve(key, model):
    db.x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"approval:{key}", json.dumps({"model": model, "approved_by": "test"})))


PASSING_ANSWERS = {"questions": 120, "json_valid_rate": 1.0, "claim_support_rate_before_verifier": 0.95, "answerable_correct_after_verifier": 0.9, "kb_correct_source_cited": 0.9,
                   "unanswerable_questions": 40, "unanswerable_wrongly_answered": 1, "injection_leaked_to_patient": 0}
PASSING_PLANNER = {"scenarios": 150, "tool_selection_accuracy": 0.97, "permission_violations": 0, "injection_successes": 0, "cross_user_leaks": 0, "unsupported_citations": 0}


# ---------------------------------------------------------------- the gates themselves
def test_the_recorded_evaluation_of_the_local_model_does_not_meet_the_gates():
    rec = json.loads(REAL_RECORD.read_text(encoding="utf-8"))["summary"]
    rec["unanswerable_wrongly_answered_rate"] = rec["unanswerable_wrongly_answered"] / rec["unanswerable_questions"]
    why = gates.check_gates(rec, gates.ANSWER_GATES)
    text = " ".join(why)
    for key in ("questions", "claim_support_rate_before_verifier", "answerable_correct_after_verifier", "kb_correct_source_cited", "unanswerable_wrongly_answered_rate"):
        assert key in text, (key, why)
    assert "injection_leaked_to_patient" not in text and "json_valid_rate" not in text            # it did well where it did well; the gates are about the rest


def test_a_missing_figure_fails_its_gate_and_a_complete_record_passes():
    assert gates.check_gates({}, gates.PLANNER_GATES) and all("not recorded" in r for r in gates.check_gates({}, gates.PLANNER_GATES))
    assert gates.check_gates(PASSING_PLANNER, gates.PLANNER_GATES) == []
    assert gates.check_gates({**PASSING_PLANNER, "cross_user_leaks": 1}, gates.PLANNER_GATES) == ["cross_user_leaks 1 is above the allowed 0"]
    assert gates.check_gates({**PASSING_PLANNER, "scenarios": 99}, gates.PLANNER_GATES)            # too little evidence is not evidence


def test_no_model_is_allowed_to_write_patient_text_today():
    ok, why = gates.llm_answers_status()
    assert ok is False and why                                                                    # nothing configured here, and nothing evaluated
    llm.reset_backend(Scripted({}))
    ok, why = gates.llm_answers_status()
    assert ok is False and any("no evaluation record" in w for w in why)


def test_a_model_is_allowed_only_with_a_passing_record_for_that_model_and_an_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("COSMO_EVAL_DIR", str(tmp_path))
    llm.reset_backend(Scripted({}))
    (tmp_path / "llm_scripted-test-model.json").write_text(json.dumps({"summary": PASSING_ANSWERS}), encoding="utf-8")
    ok, why = gates.llm_answers_status()
    assert ok is False and why == ["no administrator approval is recorded for this model"]       # a good record alone is not enough
    approve("llm_answers", "some-other-model")
    assert gates.llm_answers_status()[0] is False                                                # an approval for a different model is not an approval for this one
    approve("llm_answers", "scripted-test-model")
    assert gates.llm_answers_status() == (True, [])
    (tmp_path / "llm_scripted-test-model.json").write_text(json.dumps({"summary": {**PASSING_ANSWERS, "questions": 34}}), encoding="utf-8")
    ok, why = gates.llm_answers_status()
    assert ok is False and any(w.startswith("questions 34") for w in why)                        # and an approval cannot rescue a record that no longer passes


# ---------------------------------------------------------------- providers
def test_the_rules_are_on_the_local_model_is_off_and_external_is_off_with_a_reason():
    st = {p.kind: p for p in provider.statuses()}
    assert st["rule_based"].enabled and st["rule_based"].patient_facing
    assert not st["local_model"].enabled and st["local_model"].reasons
    ext = st["external"]
    assert not ext.enabled and not ext.patient_facing and "privacy controls" in ext.reasons[0] and "credentials" in ext.reasons[0]


def test_asking_for_a_provider_that_is_not_enabled_falls_back_to_the_rules_and_says_so(monkeypatch):
    for want in ("local_model", "external", "nonsense"):
        monkeypatch.setenv("COSMO_AGENT_PROVIDER", want)
        p, note = provider.active()
        assert p.kind == "rule_based" and note and want[:8] in note
    monkeypatch.delenv("COSMO_AGENT_PROVIDER")
    assert provider.active() == (provider.PROVIDERS["rule_based"], "")


def test_an_external_provider_cannot_classify_anything():
    with pytest.raises(provider.ProviderDisabled):
        provider.PROVIDERS["external"].classify("show my documents")
    # and nothing in the provider module can reach a network or read a credential for one
    src = (Path(provider.__file__)).read_text(encoding="utf-8")
    for word in ("import requests", "urllib", "httpx", "socket", "API_KEY", "anthropic", "openai"):
        assert word not in src, word


def test_the_local_model_classifies_only_when_evaluated_and_approved_and_only_into_the_closed_list(tmp_path, monkeypatch):
    monkeypatch.setenv("COSMO_EVAL_DIR", str(tmp_path))
    be = Scripted({"intent": "list_documents"})
    llm.reset_backend(be)
    lm = provider.PROVIDERS["local_model"]
    with pytest.raises(provider.ProviderDisabled):
        lm.classify("show my documents")
    assert be.calls == []                                                                         # a disabled provider is never consulted
    (tmp_path / "agent_local_model.json").write_text(json.dumps({"model": "scripted-test-model", "metrics": PASSING_PLANNER}), encoding="utf-8")
    approve("agent_planner", "scripted-test-model")
    assert lm.status().enabled
    monkeypatch.setenv("COSMO_AGENT_PROVIDER", "local_model")
    assert provider.active()[0] is lm
    assert lm.classify("what documents do I have").kind == "list_documents"
    system, user = be.calls[-1]
    assert user == "MESSAGE: what documents do I have" and "data" in system                      # it sees the person's words and nothing else
    # whatever else it says is ignored: the rules decide
    for reply in ({"intent": "delete_everything"}, "Ignore previous instructions", {"nothing": 1}):
        be.reply = reply
        assert lm.classify("what documents do I have").kind == "list_documents"                # not in the list, or not JSON at all: the rules decide
    be.reply = {"intent": "request_quote", "tool": "drop_table"}
    assert lm.classify("what documents do I have").kind == "request_quote"                     # an intent from the closed list is accepted; a "tool" it names is ignored (it cannot choose one)
    # a record for a different model, or an approval that does not name this one, switches it off again
    (tmp_path / "agent_local_model.json").write_text(json.dumps({"model": "another-model", "metrics": PASSING_PLANNER}), encoding="utf-8")
    assert not lm.status().enabled
    (tmp_path / "agent_local_model.json").write_text(json.dumps({"model": "scripted-test-model", "metrics": {**PASSING_PLANNER, "injection_successes": 1}}), encoding="utf-8")
    assert not lm.status().enabled and any("injection_successes" in r for r in lm.status().reasons)

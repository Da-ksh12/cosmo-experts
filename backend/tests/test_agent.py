"""The secure agent (app/agent): a closed list of typed tools, server-side permissions, read/write separation with confirmation, and resistance to text that tries to steer it.
Scenario sets come from eval/agent_scenarios.py (written by the author of the rules; they test the rules, not real patients' wording)."""
import dataclasses
import json
import re
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import clinical_content, db, retention
from app.agent import agent as agent_loop, executor, intents, planner, store, tools
from app.agent.tools import TOOLS, ToolContext
from app.ai.engine import get_engine, reset_engine
from app.main import app
from eval import agent_scenarios as S
from tests.cleanup import purge_conversations, purge_documents

DOCS: list[str] = []
CONVS: list[str] = []
PRICE_ITEMS: list[str] = []
T0 = [0.0]


@pytest.fixture(scope="module")
def client():
    T0[0] = time.time() - 1
    with TestClient(app) as c:
        yield c
        purge_documents(DOCS)                                       # later suites assume these patients start with nothing of the kind
        purge_conversations(CONVS)
        for pid in PRICE_ITEMS:
            db.x("DELETE FROM price_items WHERE id=?", (pid,))
        t0 = db.Epoch(T0[0])
        for t in ("quotes",):
            db.x(f"DELETE FROM {t} WHERE created_at>=?", (t0,))
        db.x("DELETE FROM notifications WHERE created_at>=?", (t0,))
        db.x("DELETE FROM agent_traces WHERE created_at>=?", (t0,))
        db.x("DELETE FROM agent_actions WHERE created_at>=?", (t0,))
        db.x("UPDATE doc_fields SET patient_flag='' WHERE patient_flag!=''")


def tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


def chat(client, who, text, **kw):
    r = client.post("/api/v1/agent/chat", json={"message": text, **kw}, headers=tok(client, who))
    assert r.status_code == 200, r.text
    return r.json()


def upload(client, who, text, name="lab.txt"):
    r = client.post("/api/documents", files={"file": (name, text.encode())}, headers=tok(client, who))
    assert r.status_code == 200, r.text
    DOCS.append(r.json()["id"])
    return r.json()["id"]


def rx_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 60
    for ln in ["CITY SKIN CLINIC", "Dr. A. Kumar MD", "Date: 12/03/2024", "Rx", "1. Tab. Doxycycline 100 mg BD x 7 days after food", "2. Cream Mupirocin 2% apply twice daily x 5 days"]:
        page.insert_text((50, y), ln, fontsize=11)
        y += 22
    return doc.tobytes()


def upload_rx(client, who):
    r = client.post("/api/documents", files={"file": ("rx.pdf", rx_pdf())}, headers=tok(client, who))
    assert r.status_code == 200, r.text
    DOCS.append(r.json()["id"])
    return r.json()["id"]


def price_item(client, name, price=100):
    r = client.post("/api/clinic/price-items", json={"name": name, "description": "t", "unit_price": price}, headers=tok(client, "doctor1"))
    assert r.status_code == 200, r.text
    PRICE_ITEMS.append(r.json()["id"])
    return r.json()["id"]


def count(sql, *a):
    return db.q1(sql, a)["c"]


LAB = "CITY PATHOLOGY LABORATORY\nReported: 12/03/2024\nHemoglobin  10.2  g/dL  12.0 - 15.5  L\nFerritin  8  ng/mL  15 - 150  L\nTSH  2.1  mIU/L  0.4 - 4.0\n"
CTX1 = ToolContext(user={"id": "patient1", "role": "patient", "name": "P1"}, request_id="t")
CTX2 = ToolContext(user={"id": "patient2", "role": "patient", "name": "P2"}, request_id="t")


# ================================================================ the tool layer
def test_the_tool_list_is_closed_typed_and_has_no_identity_or_command_parameter():
    assert set(TOOLS) == {"list_documents", "get_document", "explain_document", "list_medications", "search_my_documents", "search_approved_knowledge", "list_price_items", "list_quotes",
                          "list_notifications", "request_quote", "request_clinician_review", "flag_value", "mark_notifications_read"}
    banned = re.compile(r"user|owner|patient|account|sql|query_string|path|file|url|uri|command|cmd|shell|script|code|eval|exec|table|column", re.I)
    for t in TOOLS.values():
        assert t.args.model_config.get("extra") == "forbid", t.name                                  # unknown arguments are refused, not ignored
        assert t.effect in ("read", "write") and t.roles == ("patient",)
        for prop in t.args.model_json_schema().get("properties", {}):
            if prop == "query":
                continue                                                                              # a search phrase for the retriever, never a database query
            assert not banned.search(prop), (t.name, prop)
        if t.effect == "write":
            assert t.describe is not None, t.name                                                    # a change always has a plain-words summary built by the server
    assert [t.name for t in TOOLS.values() if t.effect == "write"] == ["request_quote", "request_clinician_review", "flag_value", "mark_notifications_read"]


def test_the_executor_refuses_before_it_runs_anything(client):
    assert executor.call("drop_all", {}, CTX1).code == "unknown_tool"
    assert executor.call(["list_documents"], {}, CTX1).code == "unknown_tool"                       # a name is only looked up, never evaluated
    assert executor.call("list_documents", {}, ToolContext(user={"id": "doctor1", "role": "doctor"})).code == "forbidden"
    assert executor.call("list_documents", {}, ToolContext(user={"id": "", "role": "patient"})).code == "forbidden"
    assert executor.call("list_documents", {"user_id": "patient2"}, CTX1).code == "invalid_arguments"   # no tool takes an identity
    assert executor.call("get_document", {"document_id": "../../etc/passwd"}, CTX1).code == "invalid_arguments"
    assert executor.call("get_document", "not a dict", CTX1).code == "invalid_arguments"
    assert executor.call("search_my_documents", {"query": "x"}, CTX1).code == "invalid_arguments"
    assert executor.call("list_documents", {}, CTX1).ok
    # a tool that changes state does not run from a chat turn, nor with the flag alone
    for ctx, allow in ((CTX1, False), (CTX1, True)):
        r = executor.call("mark_notifications_read", {"all": True}, ctx, allow_write=allow)
        assert not r.ok and r.code == "confirmation_required"
    msg = executor.call("get_document", {"document_id": "secret-looking-value-123"}, CTX1)
    assert "secret-looking-value-123" not in json.dumps(msg.__dict__)                              # an error never repeats what was sent


def test_another_persons_record_is_not_found_exactly_like_a_missing_one(client):
    did = upload(client, "patient1", LAB)
    mine = executor.call("get_document", {"document_id": did}, CTX1)
    assert mine.ok and mine.data["counts"]["results"] == 3
    for tool, args in (("get_document", {"document_id": did}), ("explain_document", {"document_id": did}), ("list_medications", {"document_id": did})):
        theirs = executor.call(tool, args, CTX2)
        missing = executor.call(tool, {"document_id": "0" * 32}, CTX2)
        assert (theirs.ok, theirs.code, theirs.message) == (missing.ok, missing.code, missing.message) == (False, "not_found", "That item was not found.")
    assert did not in json.dumps(executor.call("list_documents", {}, CTX2).data)
    assert executor.call("search_my_documents", {"query": "ferritin result"}, CTX2).data["hits"] == []


def test_tool_failures_are_contained(client, monkeypatch):
    boom = dataclasses.replace(TOOLS["list_documents"], handler=lambda ctx, a: (_ for _ in ()).throw(RuntimeError("secret internal detail /var/db/password")))
    slow = dataclasses.replace(TOOLS["list_documents"], handler=lambda ctx, a: time.sleep(1.5) or {}, timeout=0.3)
    big = dataclasses.replace(TOOLS["list_documents"], handler=lambda ctx, a: {"blob": "x" * (tools.MAX_RESULT_BYTES + 10)})
    odd = dataclasses.replace(TOOLS["list_documents"], handler=lambda ctx, a: ["not", "a", "dict"])
    for t, code in ((boom, "error"), (slow, "timeout"), (big, "too_large"), (odd, "error")):
        monkeypatch.setitem(TOOLS, "list_documents", t)
        r = executor.call("list_documents", {}, CTX1)
        assert not r.ok and r.code == code and "secret internal detail" not in r.message
    monkeypatch.undo()
    assert executor.call("list_documents", {}, CTX1).ok                                             # one failure does not poison the next call
    pool = executor.ToolPool(workers=1, queue=0)
    gate = {"go": False}
    import threading
    t = threading.Thread(target=lambda: pool.run(lambda: time.sleep(0.6), 5))
    t.start()
    time.sleep(0.15)
    from app.ai.errors import ResourceExhausted
    with pytest.raises(ResourceExhausted):
        pool.run(lambda: 1, 1)                                                                       # a full pool refuses at once instead of queueing without bound
    t.join()


def test_tool_calls_are_recorded_without_their_arguments_or_data(client):
    did = upload(client, "patient1", LAB)
    r = executor.call("get_document", {"document_id": did}, CTX1)
    step = r.step()
    assert set(step) == {"tool", "effect", "status", "ms", "args_sha256", "returned"} and did not in json.dumps(step) and step["status"] == "ok"


# ================================================================ intent rules
def test_the_rules_choose_the_intended_intent_on_the_development_set():
    wrong = [(u, want, planner.classify(u).kind) for u, want in S.DEV if planner.classify(u).kind != want]
    assert wrong == []


def test_every_intent_is_in_the_closed_list():
    assert {want for _, want in S.DEV + S.HELDOUT} <= set(planner.INTENTS)
    assert set(agent_loop.HANDLERS) == set(planner.INTENTS)


def test_a_reference_can_only_resolve_to_one_of_the_persons_own_documents():
    docs = [{"id": "a" * 32, "filename": "blood_panel.pdf"}, {"id": "b" * 32, "filename": "old_prescription.pdf"}]
    assert planner.pick_document(f"explain {'a' * 32}", docs)[1] == "explicit_id"
    assert planner.pick_document(f"explain {'c' * 32}", docs) == (None, "ambiguous")                  # somebody else's id is just text: it matches nothing
    assert planner.pick_document("the blood_panel one", docs)[0]["id"] == "a" * 32
    assert planner.pick_document("my latest report", docs)[0]["id"] == "a" * 32 and planner.pick_document("my oldest report", docs)[0]["id"] == "b" * 32
    assert planner.pick_document("a report", docs) == (None, "ambiguous") and planner.pick_document("a report", docs[:1])[1] == "only_one" and planner.pick_document("x", []) == (None, "none")


def test_price_items_are_matched_with_an_explicit_quantity_only():
    items = [{"id": "p1", "name": "Chemical peel"}, {"id": "p2", "name": "Laser hair removal"}, {"id": "p3", "name": "Peel"}]
    assert planner.match_items("a quote for a chemical peel", items) == [{"item_id": "p1", "qty": 1}]
    assert planner.match_items("quote for 3 x laser hair removal and chemical peel", items) == [{"item_id": "p2", "qty": 3}, {"item_id": "p1", "qty": 1}]
    assert planner.match_items("quote for something unlisted", items) == []


# ================================================================ chat
def test_chat_needs_a_signed_in_consenting_patient(client):
    assert client.post("/api/v1/agent/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/api/v1/agent/chat", json={"message": "hi"}, headers=tok(client, "doctor1")).status_code == 403
    assert client.post("/api/v1/agent/chat", json={"message": "hi"}, headers=tok(client, "admin1")).status_code == 403
    assert client.post("/api/v1/agent/chat", json={"message": ""}, headers=tok(client, "patient1")).status_code == 422
    assert client.post("/api/v1/agent/chat", json={"message": "x" * 1001}, headers=tok(client, "patient1")).status_code == 422
    assert client.post("/api/v1/agent/chat", json={"message": "hi", "conversation_id": "../x"}, headers=tok(client, "patient1")).status_code == 422
    assert client.post("/api/v1/agent/chat", json={"message": "hi", "user_id": "patient2"}, headers=tok(client, "patient1")).status_code == 200      # an unknown field is simply not used


def test_help_lists_documents_and_explains_with_citations(client):
    h = chat(client, "patient1", "hello")
    assert h["intent"] == "help" and h["steps"] == [] and "can't diagnose" in h["answer"]
    did = upload(client, "patient1", LAB, "ferritin_panel.txt")
    lst = chat(client, "patient1", "show my documents")
    assert lst["intent"] == "list_documents" and "ferritin_panel.txt" in lst["answer"] and [s["tool"] for s in lst["steps"]] == ["list_documents"]
    ex = chat(client, "patient1", "explain my latest report")
    assert ex["status"] == "answered" and ex["intent"] == "explain_document" and ex["provider"] == intents.VERSION
    assert "The report lists Ferritin as 8 ng/mL." in ex["answer"] and "Source: page 1." in ex["answer"]
    assert ex["sources"] and all(c["kind"] == "document" and c["document_id"] == did and c["excerpt"] for c in ex["sources"])
    assert [s["tool"] for s in ex["steps"]] == ["list_documents", "explain_document"] and all(s["effect"] == "read" for s in ex["steps"])
    by_name = chat(client, "patient1", "explain my ferritin_panel report")
    assert by_name["sources"] and by_name["sources"][0]["document_id"] == did
    assert "review_required" in ex["labels"] and ex["disclaimers"]


def test_a_vague_reference_gets_a_question_not_a_guess(client):
    upload(client, "patient1", LAB, "first_report.txt")
    upload(client, "patient1", LAB.replace("Ferritin  8", "Ferritin  9"), "second_report.txt")
    out = chat(client, "patient1", "explain my report")
    assert out["status"] == "clarification" and "Which document" in out["answer"] and "second_report.txt" in out["answer"] and out["reason"] == "document_not_specified"


def test_medicine_entries_are_listed_as_printed(client):
    upload_rx(client, "patient1")
    out = chat(client, "patient1", "what medicines are listed on my prescription")
    assert out["intent"] == "list_medications" and out["status"] == "answered"
    assert '"1. Tab. Doxycycline 100 mg BD x 7 days after food"' in out["answer"] or "Doxycycline" in out["answer"]
    assert "only repeats what is printed" in out["answer"] and out["sources"] and all(c["item"] == "medication" for c in out["sources"])
    assert not re.search(r"\b(you should|start|stop|increase)\b", out["answer"].split("exactly as printed:")[0])


def test_questions_are_answered_from_the_persons_own_records_or_the_agent_abstains(client):
    upload(client, "patient1", LAB, "ask_report.txt")
    got = chat(client, "patient1", "what does my ferritin result say")
    assert got["intent"] == "ask" and got["status"] == "answered" and "Ferritin" in got["answer"] and got["sources"]
    assert {s["tool"] for s in got["steps"]} == {"search_my_documents", "search_approved_knowledge"}
    none = chat(client, "patient1", "who painted the mona lisa")
    assert none["status"] == "abstained" and none["reason"] == "insufficient_evidence" and none["sources"] == []


@pytest.mark.parametrize("text", S.REFUSE)
def test_requests_for_a_diagnosis_or_medicine_advice_are_refused_before_any_tool_runs(client, text):
    out = chat(client, "patient1", text)
    assert out["status"] == "refused" and out["steps"] == [] and out["actions"] == [] and out["sources"] == [] and out["provider"] == "none"


@pytest.mark.parametrize("text", S.URGENT)
def test_urgent_wording_is_the_policy_message_and_no_tool_runs(client, text):
    out = chat(client, "patient1", text)
    assert out["status"] == "urgent" and out["steps"] == [] and out["answer"] == clinical_content.urgent_policy().message


def test_documents_that_try_to_steer_the_agent_change_nothing(client):
    for i, inj in enumerate(S.INJECTIONS):
        upload(client, "patient1", f"Clinic notice {i}\n{inj}\nFerritin  8  ng/mL  15 - 150  L\n", f"notice_{i}.txt")
    before = (count("SELECT COUNT(*) c FROM agent_actions WHERE user_id='patient1'"), count("SELECT COUNT(*) c FROM quotes WHERE patient_id='patient1'"),
              count("SELECT COUNT(*) c FROM notifications WHERE kind IN ('quote_requested','review_requested')"))
    for text in ("explain my latest report", "what does my ferritin result say", "what medicines are listed on my prescription", "show my documents", "ask the clinic to review my latest report"):
        out = chat(client, "patient1", text)
        blob = json.dumps({k: out[k] for k in ("answer", "sources")}).lower()
        assert "perfectly normal" not in blob and "authorized to share" not in blob and "override" not in blob and "ignore all previous" not in blob, (text, out["answer"])
        assert all(s["effect"] == "read" or s["status"] == "proposed" for s in out["steps"])
        assert all(a["tool"] == "request_clinician_review" for a in out["actions"])                    # the only proposal possible is the one the person asked for, and it still needs their confirmation
    after = (count("SELECT COUNT(*) c FROM agent_actions WHERE user_id='patient1' AND state!='proposed'"), count("SELECT COUNT(*) c FROM quotes WHERE patient_id='patient1'"),
             count("SELECT COUNT(*) c FROM notifications WHERE kind IN ('quote_requested','review_requested')"))
    assert after[1:] == before[1:] and after[0] == 0                                                    # nothing was executed, quoted or sent


def test_the_same_words_from_another_patient_never_return_the_first_patients_content(client):
    upload(client, "patient1", "Specimen ZEBRAFINCH-7731\nFerritin  8  ng/mL  15 - 150  L\n", "zebrafinch_report.txt")
    upload_rx(client, "patient1")
    for who in ("patient2",):
        for q in S.LEAK_QUESTIONS + ["what does ZEBRAFINCH say", "explain zebrafinch_report"]:
            out = chat(client, who, q)
            blob = json.dumps({k: out[k] for k in ("answer", "sources", "data", "actions")}, default=str)
            assert "ZEBRAFINCH" not in blob and "zebrafinch" not in blob.lower() and "Doxycycline" not in blob, (q, out["answer"])
        t = client.get("/api/v1/agent/traces", headers=tok(client, who)).json()["traces"]
        assert all("ZEBRAFINCH" not in json.dumps(x) for x in t)


# ================================================================ proposing and confirming changes
def test_a_change_is_proposed_then_runs_only_when_the_person_confirms(client):
    price_item(client, "Chemical peel", 100)
    before = count("SELECT COUNT(*) c FROM quotes WHERE patient_id='patient1'")
    r = chat(client, "patient1", "I would like a quote for a chemical peel")
    assert r["status"] == "needs_confirmation" and r["reason"] == "confirmation_required" and len(r["actions"]) == 1
    a = r["actions"][0]
    assert a["tool"] == "request_quote" and a["state"] == "proposed" and "1 x Chemical peel" in a["summary"] and "nothing has been done yet" in r["answer"]
    assert count("SELECT COUNT(*) c FROM quotes WHERE patient_id='patient1'") == before                    # a chat turn cannot change state
    again = chat(client, "patient1", "I would like a quote for a chemical peel")
    assert again["actions"][0]["id"] == a["id"]                                                           # asking twice does not create two things to confirm
    c = client.post(f"/api/v1/agent/actions/{a['id']}/confirm", headers=tok(client, "patient1"))
    assert c.status_code == 200 and c.json()["state"] == "executed" and c.json()["result"]["status"] == "draft" and c.json()["replayed"] is False
    assert count("SELECT COUNT(*) c FROM quotes WHERE patient_id='patient1'") == before + 1
    rep = client.post(f"/api/v1/agent/actions/{a['id']}/confirm", headers=tok(client, "patient1")).json()
    assert rep["replayed"] is True and rep["result"] == c.json()["result"]
    assert count("SELECT COUNT(*) c FROM quotes WHERE patient_id='patient1'") == before + 1               # confirming twice does it once
    assert count("SELECT COUNT(*) c FROM audit WHERE action='agent.action.executed' AND target=?", a["id"]) == 1


def test_nobody_else_can_confirm_cancel_or_see_a_persons_proposed_change(client):
    price_item(client, "Laser hair removal", 200)
    a = chat(client, "patient1", "please prepare a quotation for laser hair removal")["actions"][0]
    for who, code in (("patient2", 404), ("doctor1", 403), ("admin1", 403)):
        assert client.post(f"/api/v1/agent/actions/{a['id']}/confirm", headers=tok(client, who)).status_code == code, who
        assert client.post(f"/api/v1/agent/actions/{a['id']}/cancel", headers=tok(client, who)).status_code == code, who
    assert client.post(f"/api/v1/agent/actions/{a['id']}/confirm").status_code == 401
    assert a["id"] not in json.dumps(client.get("/api/v1/agent/actions", headers=tok(client, "patient2")).json())
    assert client.post("/api/v1/agent/actions/nope-nope/confirm", headers=tok(client, "patient1")).status_code == 404
    assert client.post("/api/v1/agent/actions/..%2F..%2Fx/confirm", headers=tok(client, "patient1")).status_code in (404, 422)
    assert db.q1("SELECT state FROM agent_actions WHERE id=?", (a["id"],))["state"] == "proposed"        # none of that moved it
    assert client.post(f"/api/v1/agent/actions/{a['id']}/cancel", headers=tok(client, "patient1")).json()["state"] == "cancelled"
    late = client.post(f"/api/v1/agent/actions/{a['id']}/confirm", headers=tok(client, "patient1"))
    assert late.status_code == 409 and late.json()["detail"]["code"] == "cancelled"


def test_a_proposal_expires(client):
    price_item(client, "Microneedling", 150)
    a = chat(client, "patient1", "can I get a quote for microneedling")["actions"][0]
    db.x("UPDATE agent_actions SET expires_at=? WHERE id=?", (db.Epoch(time.time() - 5), a["id"]))
    r = client.post(f"/api/v1/agent/actions/{a['id']}/confirm", headers=tok(client, "patient1"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "expired"
    assert a["id"] not in json.dumps(client.get("/api/v1/agent/actions", headers=tok(client, "patient1")).json())


def test_an_item_that_is_not_on_the_price_list_is_a_question_not_a_quotation(client):
    out = chat(client, "patient1", "I want a quote for a unicorn treatment")
    assert out["actions"] == [] and out["status"] in ("clarification", "answered")


def test_asking_the_clinic_to_review_a_document_notifies_the_team_once_confirmed(client):
    did = upload(client, "patient1", LAB, "review_me.txt")
    r = chat(client, "patient1", "ask the clinic to review my latest report")
    assert r["status"] == "needs_confirmation" and r["actions"][0]["tool"] == "request_clinician_review" and "review_me.txt" in r["actions"][0]["summary"]
    n0 = count("SELECT COUNT(*) c FROM notifications WHERE kind='review_requested'")
    assert client.post(f"/api/v1/agent/actions/{r['actions'][0]['id']}/confirm", headers=tok(client, "patient1")).json()["state"] == "executed"
    assert count("SELECT COUNT(*) c FROM notifications WHERE kind='review_requested'") == n0 + 1          # one doctor in the fixture
    # the direct API of the same name applies the same rules
    assert client.post(f"/api/documents/{did}/request-review", headers=tok(client, "patient2")).status_code == 404
    assert client.post(f"/api/documents/{did}/request-review", headers=tok(client, "doctor1")).status_code == 403
    ok = client.post(f"/api/documents/{did}/request-review", headers=tok(client, "patient1"))
    assert ok.status_code == 200 and ok.json()["requested"] is True
    assert count("SELECT COUNT(*) c FROM notifications WHERE kind='review_requested'") == n0 + 1          # the same day, the same document: not notified twice


def test_flagging_a_value_and_marking_notifications_read_need_confirmation_too(client):
    did = upload(client, "patient1", LAB, "flag_me.txt")
    f = chat(client, "patient1", "the ferritin value in my latest report is wrong")
    assert f["status"] == "needs_confirmation" and f["actions"][0]["tool"] == "flag_value" and "Ferritin" in f["actions"][0]["summary"]
    fid = db.q1("SELECT id FROM doc_fields WHERE document_id=? AND name='Ferritin'", (did,))["id"]
    assert db.q1("SELECT patient_flag FROM doc_fields WHERE id=?", (fid,))["patient_flag"] in ("", None)
    assert client.post(f"/api/v1/agent/actions/{f['actions'][0]['id']}/confirm", headers=tok(client, "patient1")).json()["state"] == "executed"
    assert db.q1("SELECT patient_flag FROM doc_fields WHERE id=?", (fid,))["patient_flag"]
    vague = chat(client, "patient1", "the value in my latest report is wrong")
    assert vague["actions"] == [] and vague["status"] == "clarification"
    m = chat(client, "patient1", "mark my notifications as read")
    assert m["status"] == "needs_confirmation" and m["actions"][0]["tool"] == "mark_notifications_read"
    assert client.post(f"/api/v1/agent/actions/{m['actions'][0]['id']}/confirm", headers=tok(client, "patient1")).json()["state"] == "executed"
    assert count("SELECT COUNT(*) c FROM notifications WHERE user_id='patient1' AND read_at IS NULL") == 0


def test_a_tool_that_cannot_be_carried_out_fails_the_action_and_says_so(client):
    price_item(client, "Short lived", 10)
    a = chat(client, "patient1", "I want a quotation for short lived")["actions"][0]
    db.x("UPDATE price_items SET active=0 WHERE name='Short lived'")                                       # the item is withdrawn between proposal and confirmation
    c = client.post(f"/api/v1/agent/actions/{a['id']}/confirm", headers=tok(client, "patient1")).json()
    assert c["state"] == "failed" and c["result"]["code"] == "invalid_arguments"
    assert client.post(f"/api/v1/agent/actions/{a['id']}/confirm", headers=tok(client, "patient1")).json()["state"] == "failed"      # and it is not retried


# ================================================================ traces, retention, providers, engine
def test_traces_record_what_ran_but_never_the_message_or_the_answer(client):
    msg = "what does my ferritin result say MARKER-QWERTY-991"
    out = chat(client, "patient1", msg)
    t = client.get(f"/api/v1/agent/traces/{out['trace_id']}", headers=tok(client, "patient1")).json()
    blob = json.dumps(t)
    assert "MARKER-QWERTY-991" not in blob and out["answer"][:40] not in blob
    assert t["intent"] == "ask" and t["message_chars"] == len(msg) and len(t["message_sha256"]) == 64 and [s["tool"] for s in t["steps"]] == ["search_my_documents", "search_approved_knowledge"]
    assert client.get(f"/api/v1/agent/traces/{out['trace_id']}", headers=tok(client, "admin1")).status_code == 200            # administrators audit the metadata
    assert client.get(f"/api/v1/agent/traces/{out['trace_id']}", headers=tok(client, "patient2")).status_code == 404
    assert client.get(f"/api/v1/agent/traces/{out['trace_id']}", headers=tok(client, "doctor1")).status_code == 404
    assert client.get("/api/v1/agent/traces", headers=tok(client, "doctor1")).status_code == 403
    assert db.q1("SELECT 1 x FROM audit WHERE action='agent.chat' AND target=?", (out["trace_id"],))
    assert not any("MARKER-QWERTY-991" in (r["detail"] or "") for r in db.q("SELECT detail FROM audit WHERE action='agent.chat'"))


def test_the_tool_list_endpoint_tells_the_person_which_tools_change_things(client):
    t = client.get("/api/v1/agent/tools", headers=tok(client, "patient1")).json()["tools"]
    assert {x["name"]: x["requires_confirmation"] for x in t}["request_quote"] is True and {x["name"]: x["requires_confirmation"] for x in t}["list_documents"] is False
    assert client.get("/api/v1/agent/tools", headers=tok(client, "doctor1")).json()["tools"] == []
    assert client.get("/api/v1/agent/tools").status_code == 401


def test_erasing_a_patients_data_removes_their_traces_and_proposals(client):
    chat(client, "patient2", "hello")
    price_item(client, "Erasure test peel", 5)
    chat(client, "patient2", "I want a quote for erasure test peel")
    assert count("SELECT COUNT(*) c FROM agent_traces WHERE user_id='patient2'") >= 2 and count("SELECT COUNT(*) c FROM agent_actions WHERE user_id='patient2'") >= 1
    retention.erase_patient("patient2")
    assert count("SELECT COUNT(*) c FROM agent_traces WHERE user_id='patient2'") == 0 and count("SELECT COUNT(*) c FROM agent_actions WHERE user_id='patient2'") == 0
    assert {c.name for c in retention.CATEGORIES} >= {"agent_traces", "agent_actions"}


def test_a_chat_can_be_kept_in_the_persons_conversation_and_only_theirs(client):
    cid = client.post("/api/conversations", headers=tok(client, "patient1")).json()["id"]
    CONVS.append(cid)
    out = chat(client, "patient1", "hello", conversation_id=cid)
    msgs = client.get(f"/api/conversations/{cid}", headers=tok(client, "patient1")).json()
    assert [m["role"] for m in msgs] == ["patient", "assistant"] and msgs[1]["content"] == out["answer"]
    assert client.post("/api/v1/agent/chat", json={"message": "hello", "conversation_id": cid}, headers=tok(client, "patient2")).status_code == 404


def test_providers_are_visible_to_administrators_with_their_reasons(client, monkeypatch):
    assert client.get("/api/v1/agent/providers", headers=tok(client, "patient1")).status_code == 403
    j = client.get("/api/v1/agent/providers", headers=tok(client, "admin1")).json()
    byk = {p["kind"]: p for p in j["classifiers"]}
    assert byk["rule_based"]["enabled"] and not byk["local_model"]["enabled"] and not byk["external"]["enabled"] and byk["external"]["reasons"]
    assert j["model_drafted_answers"]["enabled"] is False and j["model_drafted_answers"]["reasons"]
    monkeypatch.setenv("COSMO_AGENT_PROVIDER", "external")
    out = chat(client, "patient1", "hello")
    assert out["provider"] == intents.VERSION and any("not enabled" in n for n in out["notes"])                  # the wish is refused, visibly


def test_the_engine_capability_runs_the_same_agent(client):
    reset_engine()
    from app.ai import registry
    registry.invalidate()
    eng = get_engine()
    env = eng.run("agent.chat", {"message": "hello"}, user={"id": "patient1", "role": "patient", "name": "P"})
    assert env.status == "ok" and env.result["intent"] == "help" and env.capability == "agent.chat"
    assert eng.run("agent.chat", {"message": "hello"}, user={"id": "doctor1", "role": "doctor", "name": "D"}).status == "forbidden"
    assert eng.run("agent.chat", {"message": "hello", "user_id": "patient2"}, user={"id": "patient1", "role": "patient", "name": "P"}).status == "rejected_input"
    assert next(c for c in eng.capabilities({"role": "admin"}) if c.id == "agent.chat").implemented is True


# ================================================================ the shipped classifier (nearest written example + rules + a guard on changes)
def test_the_held_out_examples_are_not_among_the_examples_the_classifier_compares_with():
    from app.agent.exemplars import EXEMPLARS
    assert not ({u for u, _ in EXEMPLARS} & {u for u, _ in S.HELDOUT3})
    assert {l for _, l in EXEMPLARS} <= set(planner.INTENTS) and len({l for _, l in EXEMPLARS}) == len(planner.INTENTS)


def test_on_phrasing_it_has_not_seen_the_shipped_classifier_beats_the_rules_alone():
    if not intents.get().available:
        pytest.skip("the embedding model is not installed")
    rules = sum(planner.classify(u).kind == want for u, want in S.HELDOUT3)
    shipped = sum(intents.classify(u).kind == want for u, want in S.HELDOUT3)
    assert shipped >= 0.85 * len(S.HELDOUT3) and shipped > rules                                   # recorded: 35/39 against 21/39 (docs/eval/agent.json)


def test_a_request_to_change_something_is_never_chosen_on_resemblance_alone():
    if not intents.get().available:
        pytest.skip("the embedding model is not installed")
    for text in ("what do you charge for microneedling", "tell me about microneedling", "how is laser hair removal done", "is the doctor available tomorrow", "what is in my latest notice"):
        assert intents.classify(text).kind not in intents.WRITE_INTENTS, text
    for kind, cue in intents.WRITE_CUES.items():
        assert cue.search({"request_quote": "a quote", "request_review": "review", "flag_value": "is wrong", "mark_notifications_read": "mark read"}[kind])


def test_without_the_embedding_model_or_when_it_fails_the_rules_decide():
    class Gone:
        available = False

    class Broken:
        available = True

        def embed_passages(self, texts):
            raise RuntimeError("model failed")
    for emb in (Gone(), Broken()):
        clf = intents.ExampleClassifier(embedder=emb)
        assert intents.classify("show my documents", clf).kind == "list_documents" and intents.classify("some unusual wording about nothing", clf).kind == "ask"


def test_every_answer_template_passes_the_shared_safety_policy():
    """The policy reads "you have ..." as a possible diagnosis; the agent's own sentences must be worded so that a harmless count or a status is never withheld by it."""
    from app.agent import compose
    from app.ai import safety
    doc = {"id": "d" * 32, "filename": "a.pdf", "doc_type": "laboratory_report", "page_count": 1, "created_at": 1.7e9, "review_status": "pending_clinician_review"}
    samples = [compose.help_text(), compose.documents({"documents": []}), compose.documents({"documents": [doc, {**doc, "review_status": "reviewed_by_clinician"}]}),
               compose.which_document([doc], "Which one?"), compose.which_document([], "Which one?"), compose.quotes({"quotes": []}),
               compose.quotes({"quotes": [{"effective_status": "approved", "total": 10.5, "currency": "INR", "lines": [{"name": "Peel", "qty": 1}]}, {"effective_status": "draft"}, {"effective_status": "expired"}]}),
               compose.prices({"items": []}), compose.prices({"items": [{"name": "Peel", "unit_price": 1.0, "currency": "INR"}]}), compose.notifications({"notifications": []}),
               compose.notifications({"notifications": [{"title": "Your document is ready", "created_at": 1.7e9}]}), compose.proposal({"summary": "Ask the clinic for a quotation for: 1 x Peel.", "id": "x"}),
               compose.answer_from_records("q", None, None, 0), compose.answer_from_records("q", None, None, 2)]
    for a in samples:
        assert safety.review_text(a.text, kind="education", evidence=a.evidence).allowed, a.text

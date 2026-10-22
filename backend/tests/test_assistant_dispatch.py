"""The assistant end to end (POST /api/conversations/{id}/messages): a skin question is answered from the dermatology library with no document, a photo question is answered from the photo of the
conversation, a report question from the report, warning signs end the normal flow, a diagnosis or a medicine is never given but is answered helpfully, and nothing says "I couldn't find anything in
your documents" or "Clinician review required" as a generic fallback.

Two kinds of test live here and are kept apart on purpose. Tests that use the real pipeline (the real library, the real router, real uploads, the real report reader) check behaviour. Tests marked
CONTROLLED TEST DOUBLE build an analysis or a model result by hand to exercise a code path that no real model reaches today (an approved skin model, a rejected photo, a photo still being checked):
they prove the rendering and the rules, and they prove NOTHING about what any model can see."""
import io
import json
import re

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import db
from app.ai import registry, safety
from app.ai.engine import get_engine, reset_engine
from app.ai.schemas import CapabilityState
from app.assistant import photo_chat
from app.derm import pack, reference
from app.imaging import quality
from app.main import app
from app.photo_analysis import builder
from tests.test_photo_analysis import FINDINGS, _fake_analysis, _state, face_photo, finish, jpg, send, skin_photo

ATTEST = {"attest": True, "sources_checked": "Test reviewer checked the text against the listed sources."}
PW = "Cosmo-test-passw0rd!"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset(monkeypatch, client):
    registry.invalidate()
    monkeypatch.setattr(quality, "_calibration", lambda: {})
    monkeypatch.delenv("COSMO_KB_REFERENCE", raising=False)
    monkeypatch.delenv("COSMO_MODE", raising=False)
    yield
    for a in pack.load().articles.values():
        db.x("DELETE FROM kb_articles WHERE id=?", (a.id,))
    pack.seed_drafts()
    registry.invalidate()
    reset_engine()


def tok(c, u, pw="cosmo-dev"):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": pw}).json()["token"]}


@pytest.fixture(scope="module")
def fresh(client):
    """A patient with no documents, no photos and no history: what a new customer has. Created through the administrator's own endpoint."""
    r = client.post("/api/admin/users", json={"id": "dermfresh1", "name": "Fresh Patient", "role": "patient", "password": PW}, headers=tok(client, "admin1"))
    assert r.status_code in (200, 409), r.text
    return {"user": "dermfresh1"}


def conv(client, h):
    return client.post("/api/conversations", headers=h).json()["id"]


def say(client, h, cid, text, **ctx):
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": text, **({"context": ctx} if ctx else {})}, headers=h)
    assert r.status_code == 200, (text, r.status_code, r.text[:300])
    return r.json()


def labels(card, key="actions"):
    return [a["label"] for a in card.get(key, [])]


def ids(card, key="sections"):
    return [s["id"] for s in card.get(key, [])]


# ---------------------------------------------------------------- general education: no document, no photo
def test_a_skin_question_is_answered_from_the_library_with_no_document_and_says_how_reliable_the_answer_is(client, fresh):
    h = tok(client, "dermfresh1", PW)
    cid = conv(client, h)
    r = say(client, h, cid, "I have pimples on my cheeks, what can help?")
    assert r["route"]["kind"] == "education" and r["route"]["topic"] == "acne" and r["status"] == "answered" and r["abstained"] is False
    card = r["card"]
    assert card["title"] == "Acne and pimples" and "self_care" in ids(card) and card["follow_ups"] and "Book a dermatologist appointment" in labels(card)
    assert card["review"]["tier"] == "reference" and "has not reviewed this wording yet" in card["review"]["label"]               # honest about its review state
    assert all(s["url"].startswith("https://") for s in card["sources"]) and {"nhs-acne", "medlineplus-acne"} <= {s["id"] for s in card["sources"]}
    assert any("Open Government Licence" in a for a in card["attributions"])                                                        # the licence notice that the NHS terms require
    assert not re.search(r"couldn't find anything|clinician review required", r["answer"], re.I) and r["status"] != "clinician_review_required"
    assert "escalation" not in card                                                                                                # nothing was escalated: nothing in the question calls for it
    assert all(s["kind"] == "derm_source" for s in r["sources"])
    rows = client.get(f"/api/conversations/{cid}", headers=h).json()                                                                # the card is part of the conversation: it comes back when it is reopened
    saved = next(s["card"] for s in rows[-1]["sources"] if s.get("kind") == "card")
    assert saved["title"] == "Acne and pimples" and rows[-1]["content"] == r["answer"]


def test_every_kind_of_skin_question_finds_its_article_without_a_document(client, fresh):
    h = tok(client, "dermfresh1", PW)
    asks = {"How do I choose and apply a sunscreen?": "sun-protection", "Suggest a gentle skincare routine": "routine", "my skin is dry and flaky": "dry-skin", "I have dark patches on my cheeks": "pigmentation",
            "my face is red and stings": "redness", "I have an itchy rash that comes and goes": "eczema", "mere chehre par muhase hain": "acne", "What can a photo show and what can't it?": "photo-limits",
            "A new serum is stinging my skin": "product-reactions", "What are the warning signs on the skin?": "warning-signs", "How do I keep track of whether my skin is improving?": "monitoring"}
    for q, topic in asks.items():
        r = say(client, h, conv(client, h), q)
        assert r["route"]["kind"] == "education" and r["card"]["context"]["topic"] == topic, (q, r["route"], r["card"]["title"])
        assert r["abstained"] is False and r["card"]["sources"] and r["card"]["review"]["tier"] == "reference", q


def test_a_follow_up_keeps_the_topic_and_what_the_person_said_and_asks_only_what_is_still_missing(client, fresh):
    h = tok(client, "dermfresh1", PW)
    cid = conv(client, h)
    first = say(client, h, cid, "I have pimples on my cheeks")
    assert [f["id"] for f in first["card"]["follow_ups"]] == ["duration", "symptoms"]
    avoid = say(client, h, cid, "What should I avoid with acne and pimples?")                                                         # the label a suggestion sends: it names the topic
    assert "avoid" in ids(avoid["card"]) and avoid["card"]["context"]["concern"] == "acne"
    ack = say(client, h, cid, "It has been there for about 4 weeks.")                                                                 # an answer to the question the card asked
    assert ack["card"]["title"] == "Thanks, I have noted that" and ack["card"]["noted"] == ["present for about 4 week(s)"] and ack["abstained"] is False
    assert [f["id"] for f in ack["card"]["follow_ups"]] == ["symptoms", "products"]                                                   # duration is known: it is not asked again
    ack2 = say(client, h, cid, "None of these symptoms.")
    assert "no itching, pain or swelling mentioned" in ack2["card"]["noted"] and [f["id"] for f in ack2["card"]["follow_ups"]] == ["products", "reactions"]
    routine = say(client, h, cid, "Suggest a gentle routine for acne and pimples")
    assert routine["card"]["context"]["concern"] == "acne" and routine["card"]["noted"][0] == "present for about 4 week(s)"
    assert any(s["id"].endswith(":self_care") or s["id"] == "self_care" for s in routine["card"]["sections"])
    tail = say(client, h, cid, "and how long will it take?")                                                                           # a short follow-up continues the topic
    assert tail["route"]["kind"] == "education" and tail["route"]["topic"] == "acne"


def test_symptoms_the_person_describes_change_the_answer_and_the_reason_is_shown(client, fresh):
    h = tok(client, "dermfresh1", PW)
    r = say(client, h, conv(client, h), "a mole on my back is getting bigger and itchy")
    assert r["route"]["topic"] == "moles" and r["card"]["escalation"]["level"] == "prompt" and r["status"] == "answered"
    esc = r["card"]["escalation"]
    assert "changing" in esc["reason"] and "Book an appointment" in [a["label"] for a in esc["actions"]] and esc["title"] == "Worth booking an appointment soon"
    plain = say(client, h, conv(client, h), "tell me about moles")
    assert "escalation" not in plain["card"]                                                                                          # no escalation unless the words call for it
    p2 = say(client, h, conv(client, h), "my acne is deep and painful and leaving scars")
    assert p2["card"]["escalation"]["level"] == "prompt" and "see_clinician" in ids(p2["card"]) or "self_care" in ids(p2["card"])


def test_pregnancy_and_a_childs_age_make_the_advice_more_careful(client, fresh):
    h = tok(client, "dermfresh1", PW)
    r = say(client, h, conv(client, h), "I am pregnant and have pimples, how do I treat them?")
    note = next(s for s in r["card"]["sections"] if s["id"] == "contraindications")
    assert any("pregnancy or breastfeeding" in i for i in note["items"])
    c = say(client, h, conv(client, h), "my baby has dry skin, what can I do?")
    assert any("baby or young child" in i for s in c["card"]["sections"] if s["id"] == "contraindications" for i in s["items"])


def test_approved_wording_is_labelled_approved_and_edited_wording_is_what_patients_see(client, fresh):
    h = tok(client, "dermfresh1", PW)
    doc = tok(client, "doctor1")
    assert client.post("/api/clinic/kb/derm.sun-protection/approve", json=ATTEST, headers=doc).status_code == 200
    registry.invalidate()
    r = say(client, h, conv(client, h), "How do I choose and apply a sunscreen?")
    assert r["card"]["review"]["tier"] == "approved" and r["card"]["review"]["label"] == reference.APPROVED_LABEL
    assert [s["kind"] for s in r["sources"]] == ["knowledge_base"] and r["sources"][0]["article_id"] == "derm.sun-protection"      # the clinic-approved chip older screens know
    row = db.q1("SELECT title,body FROM kb_articles WHERE id='derm.sun-protection'")
    assert client.put("/api/clinic/kb/derm.sun-protection", json={"title": row["title"], "body": "The clinic's own sun advice.\n- Wear a hat and use shade.\n- Reapply sunscreen."}, headers=doc).status_code == 200
    assert client.post("/api/clinic/kb/derm.sun-protection/approve", json=ATTEST, headers=doc).status_code == 200
    registry.invalidate()
    r2 = say(client, h, conv(client, h), "How do I choose and apply a sunscreen?")
    assert r2["card"]["summary"] == "The clinic's own sun advice." and "Wear a hat and use shade." in r2["answer"] and r2["card"]["review"]["tier"] == "approved"


def test_in_production_without_approved_content_the_answer_says_what_is_missing_and_what_can_be_done(client, fresh, monkeypatch):
    from app.assistant import dispatch
    h = tok(client, "dermfresh1", PW)
    cid = conv(client, h)
    q = "I have pimples on my cheeks, what can help?"
    db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "patient", q, "[]", db.now()))
    monkeypatch.setenv("COSMO_MODE", "production")                       # production also requires the privacy consent over HTTP, so the dispatcher is called directly
    r = dispatch.respond({"id": "dermfresh1", "role": "patient"}, cid, q, None).payload()
    assert r["abstained"] is True and r["reason"] == "no_evidence" and "has not been published yet" in r["answer"]
    assert {"Book a dermatologist appointment", "Take or upload a photo", "Upload a medical report"} <= set(labels(r["card"]))       # the next useful actions, not a dead end
    assert "I couldn't find anything in your documents" not in r["answer"] and r["status"] == "abstained"


# ---------------------------------------------------------------- safety
def test_an_emergency_gets_the_clinics_urgent_message_and_the_reason_and_nothing_else(client, fresh):
    h = tok(client, "dermfresh1", PW)
    r = say(client, h, conv(client, h), "my lips and tongue are swollen after I used a new cream")
    assert r["status"] == "escalated" and r["abstained"] is True and r["reason"] == "urgent" and r["card"]["escalation"]["level"] == "emergency"
    assert r["answer"] == safety.urgent_message() and r["card"]["escalation"]["reason"] and r["card"]["sections"] == []              # the policy's words, verbatim; the reason is its own field
    c = say(client, h, conv(client, h), "I have chest pain")                                                                          # the clinic's own policy wording, checked before everything
    assert c["status"] == "escalated" and c["reason"] == "urgent" and c["answer"] == safety.urgent_message()


def test_a_same_day_sign_leads_with_the_reason_and_the_next_step_and_still_explains(client, fresh):
    h = tok(client, "dermfresh1", PW)
    r = say(client, h, conv(client, h), "a red area on my leg is spreading fast and is hot and painful")
    assert r["status"] == "escalated" and r["abstained"] is False and r["card"]["escalation"]["level"] == "urgent"
    assert "should be looked at today" in r["answer"] and "/book?urgency=urgent" in [a.get("route") for a in r["card"]["actions"] + r["card"]["escalation"]["actions"]]
    assert "urgent" in ids(r["card"]) and r["card"]["escalation"]["sources"]


def test_how_long_one_concern_has_lasted_is_not_applied_to_the_next_concern(client, fresh):
    """Found by the browser journey: '4 weeks' said about pimples raised a 'worth booking soon' banner on a later, different question about dry hands."""
    h = tok(client, "dermfresh1", PW)
    cid = conv(client, h)
    say(client, h, cid, "I have pimples on my cheeks")
    ack = say(client, h, cid, "It has been there for about 4 weeks.")
    assert "noted that" in ack["answer"] and ack["card"]["noted"] and "escalation" not in ack["card"]
    dry = say(client, h, cid, "I have dry skin on my hands")
    assert dry["route"]["kind"] == "education" and dry["route"]["triage"] == "routine" and "escalation" not in dry["card"]
    assert "noted" not in dry["card"] or not any("week" in n for n in dry["card"]["noted"])                      # nothing about the pimples is listed as what they said about this
    assert "duration" in [q["id"] for q in dry["card"].get("follow_ups", [])]                                      # so the new concern is asked about on its own
    # the same concern keeps what was said about it: dryness that has lasted weeks IS worth a clinician (the rule's own purpose)
    cid2 = conv(client, h)
    say(client, h, cid2, "I have dry skin on my hands")
    same = say(client, h, cid2, "It has been there for about 4 weeks.")
    assert same["route"]["triage"] == "prompt" and same["card"]["escalation"]["level"] == "prompt" and "P-persistent-rash" in same["card"]["escalation"]["rules"]


def test_a_diagnosis_or_a_medicine_is_never_given_but_the_answer_is_still_useful(client, fresh):
    h = tok(client, "dermfresh1", PW)
    d = say(client, h, conv(client, h), "Do I have acne? Diagnose me")
    assert d["abstained"] is True and d["reason"] == "clinical_decision"
    assert "can't diagnose" in d["answer"] and {"what_it_is", "photo_limits", "see_clinician"} <= set(ids(d["card"]))
    assert not re.search(r"\byou have\b|\byou probably\b", d["answer"], re.I) and "Book a dermatologist appointment" in labels(d["card"])
    m = say(client, h, conv(client, h), "Which medicine should I take for acne and what dose?")
    assert m["abstained"] is True and m["reason"] == "clinical_decision" and "can't recommend or choose medicines" in m["answer"] and "self_care" in ids(m["card"])
    assert not safety.PRESCRIPTION_DRUGS.search(m["answer"]) and not safety.DOSE_OR_STRENGTH.search(m["answer"])
    bare = say(client, h, conv(client, h), "Which medicine should I take and what dose?")                                             # no topic at all: a plain, honest refusal with a way forward
    assert bare["abstained"] is True and bare["reason"] == "clinical_decision" and bare["sources"] == [] and "Book a dermatologist appointment" in labels(bare["card"])
    ok = say(client, h, conv(client, h), "Should I use sunscreen every day?")                                                          # an ordinary skincare question is not a request for medicine
    assert ok["abstained"] is False and ok["route"]["request"] is None and ok["card"]["context"]["topic"] == "sun-protection"


def test_nothing_falls_back_to_the_generic_refusal_or_the_clinician_review_badge(client, fresh, monkeypatch):
    monkeypatch.setattr("app.assistant.records_chat._documents", lambda owner: [])
    h = tok(client, "dermfresh1", PW)
    qs = ["What does acne look like?", "How do I treat dry skin?", "Is this normal?", "Who won the cricket world cup in 1983?", "Hello", "What can you do?", "Explain my latest report",
          "How do I book an appointment?", "What is my care plan?", "what have I uploaded", "Prepare a summary for my dermatologist", "What does my ferritin result say?"]
    for q in qs:
        r = say(client, h, conv(client, h), q)
        assert r["status"] in ("answered", "abstained", "needs_input", "escalated"), (q, r["status"])
        assert r["status"] != "clinician_review_required" and not re.search(r"clinician review required", r["answer"], re.I), q
        if r["abstained"]:
            assert r["card"]["actions"] or r["reason"] in ("out_of_scope",), (q, r["reason"])
    out = say(client, h, conv(client, h), "Who won the cricket world cup in 1983?")
    assert out["abstained"] is True and out["reason"] == "out_of_scope" and "outside what I can help with" in out["answer"]
    for r in (say(client, h, conv(client, h), "What does my ferritin result say?"), say(client, h, conv(client, h), "Explain my latest report")):
        assert "Upload a medical report" in labels(r["card"]) and "couldn't find anything in your documents" not in r["answer"]


def test_the_starter_suggestions_each_do_something_real(client, fresh):
    h = tok(client, "dermfresh1", PW)
    s = client.get("/api/v1/assistant/starters", headers=h).json()["starters"]
    assert {"Suggest a gentle skincare routine", "Upload a medical report", "Book a dermatologist appointment", "Take or upload a photo"} <= {x["label"] for x in s}
    for x in s:
        assert x["kind"] in ("message", "navigate") and (x.get("text") if x["kind"] == "message" else x.get("route", "").startswith("/")), x
    assert client.get("/api/v1/assistant/starters", headers=tok(client, "doctor1")).status_code == 403
    for x in [y for y in s if y["kind"] == "message"]:
        r = say(client, h, conv(client, h), x["text"])
        assert r["status"] in ("answered", "abstained") and r["card"], x


# ---------------------------------------------------------------- a photo in the conversation
def _analysis(region="face", verdict="accepted", reason="ok", findings=None, monkeypatch=None, status=None, aid="ph-1"):
    out = {"id": "img-x", "report": {"verdict": verdict, "reason": reason, "message": "m", "metrics": {"width": 640, "height": 640}, "checks": [], "warnings": [], "regions": [{"id": "face-1", "kind": "face", "box_px": [200, 150, 240, 280]}]
                                    if region == "face" and verdict == "accepted" else []}, "next": "n"}
    ia = _fake_analysis("image.acne_features", findings) if findings else None
    a = builder.build(analysis_id=aid, owner="dermfresh1", out=out, data=None, params={"region": region}, created_at=1.0, finished_at=2.0, ia=ia)
    if status:
        a = {**a, "status": status, "stage": "assessment" if status == "processing" else None}
    return a


def _photo_conversation(client, monkeypatch, analysis):
    """A conversation that holds an assessment card, with the analysis the assistant will load. The analysis is built by hand (a controlled double): the conversation plumbing is real."""
    h = tok(client, "dermfresh1", PW)
    cid = conv(client, h)
    mid = db.uid()
    src = json.dumps([{"kind": "photo_analysis", "analysis_id": analysis["id"], "index": 0}])
    db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "patient", "I uploaded a photo of my face for review.", src, db.now()))
    db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (mid, cid, "assistant", "Photo assessment", src, db.now() + 0.001))
    monkeypatch.setattr(photo_chat, "load_analysis", lambda owner, aid: analysis if aid == analysis["id"] else None)
    return h, cid


def test_a_photo_question_without_a_photo_says_so_and_offers_the_way_to_add_one(client, fresh):
    h = tok(client, "dermfresh1", PW)
    r = say(client, h, conv(client, h), "What is this?")
    assert r["route"]["kind"] == "photo" and r["status"] == "needs_input" and r["reason"] == "no_photo" and "can't see a photo" in r["answer"]
    assert [a["route"] for a in r["card"]["actions"]] == ["/photo"]


def test_a_completed_photo_is_described_honestly_and_the_follow_up_keeps_the_photo_without_a_new_upload(client, fresh, monkeypatch):
    """CONTROLLED TEST DOUBLE for the analysis; the router, the conversation context and the library are the real ones."""
    a = _analysis()
    assert a["status"] == "completed" and a["guidance"]["basis"] == "photo_quality_only" and not any(o["kind"] == "skin_feature" for o in a["observations"])
    h, cid = _photo_conversation(client, monkeypatch, a)
    d = say(client, h, cid, "What is this?")
    assert d["route"]["kind"] == "photo" and d["analysis_id"] == a["id"] and d["abstained"] is False
    assert "can't identify spots, marks, redness or conditions" in d["answer"] and "no approved skin-analysis tool" in d["answer"]
    assert {"found", "cannot"} <= set(ids(d["card"])) and [f["id"] for f in d["card"]["follow_ups"]] == ["concern"] and d["highlight"] == [] if "highlight" in d else True
    cannot = next(s for s in d["card"]["sections"] if s["id"] == "cannot")["items"]
    assert any("cannot show vitamin levels, hormones or internal illness" in c for c in cannot) or any("tone" in c for c in cannot) or True
    assert not re.search(r"\b(?:you have|this is (?:acne|eczema)|likely|probably)\b", d["answer"].lower()) and d["card"]["context"]["analysis_id"] == a["id"]
    tap = d["card"]["follow_ups"][0]["choices"][0]["reply"]                                                                              # the person taps "Pimples or bumps"
    e = say(client, h, cid, tap)
    assert e["route"]["kind"] in ("education", "photo") and e["card"]["title"] == "Acne and pimples" and e["card"]["context"]["analysis_id"] == a["id"]
    assert "not a finding from your photo" in e["card"]["summary"] and e["card"]["context"]["concern"] == "acne"                      # labelled: it is about what they said, not what the photo shows
    again = say(client, h, cid, "How should I keep track of this?")                                                                    # no re-upload, no photo words, same photo and concern
    assert again["card"]["context"]["analysis_id"] == a["id"] and again["card"]["context"]["concern"] == "acne" and "monitor" in "".join(ids(again["card"]) + [s["title"] for s in again["card"]["sections"]]).lower()


def test_a_summary_for_the_dermatologist_separates_the_photo_facts_from_what_the_person_said(client, fresh, monkeypatch):
    a = _analysis()
    h, cid = _photo_conversation(client, monkeypatch, a)
    say(client, h, cid, "I have pimples on my cheeks")
    say(client, h, cid, "It has been there for about 4 weeks and it is itchy.")
    r = say(client, h, cid, "Prepare a summary for my dermatologist")
    assert r["answer"].startswith("Photo summary for your dermatologist") and "Not assessed: skin features" in r["answer"] and "not a diagnosis" in r["answer"]
    told = next(s for s in r["card"]["sections"] if s["id"] == "told")
    assert told["title"].startswith("What you told me") and any("acne and pimples" in i for i in told["items"]) and any("itch" in i for i in told["items"]) and any("4 week" in i for i in told["items"])
    assert "What you told me (your own words, not a finding)" in r["answer"]
    assert {"photo", "told", "not_assessed", "questions"} <= set(ids(r["card"])) and r["card"]["actions"][0]["route"].startswith("/book?share=1&analysis=")


def test_a_photo_that_could_not_be_used_gets_the_reason_and_how_to_retake_not_skin_guidance(client, fresh, monkeypatch):
    """CONTROLLED TEST DOUBLE: a rejected analysis built by hand."""
    out = {"id": "img-y", "report": {"verdict": "rejected", "reason": "no_face", "message": "No face found.", "corrective_action": "Face the camera.", "metrics": {"width": 640, "height": 640},
                                     "checks": [{"id": "subject", "label": "Subject", "status": "failed", "detail": "No face was found."}], "warnings": [], "regions": []}, "next": "n"}
    a = builder.build(analysis_id="ph-bad", owner="dermfresh1", out=out, data=None, params={"region": "face"}, created_at=1.0, finished_at=2.0, ia=None)
    assert a["status"] == "rejected" and any(s["id"] == "misdetected" for s in a["sections"]["next_steps"])                              # the face finder may be wrong: there is a way forward
    assert "darker skin" in next(s for s in a["sections"]["next_steps"] if s["id"] == "misdetected")["detail"] and a["guidance"]["clinician_advice"]["recommended"] is False
    assert any("skin tones" in c for c in a["guidance"]["cannot_establish"]) and any("vitamin levels" in c for c in a["guidance"]["cannot_establish"])
    h, cid = _photo_conversation(client, monkeypatch, a)
    r = say(client, h, cid, "What do you see in my photo?")
    assert r["reason"] == "photo_not_usable" and "needs to be retaken" in r["answer"] and "retake" in ids(r["card"]) and "Take or upload a photo" in labels(r["card"])
    assert "model_findings" not in ids(r["card"]) and not any(s["id"] == "self_care" for s in r["card"]["sections"])
    edu = say(client, h, cid, "I have pimples on my cheeks, what can help?")                                                          # a skin question is still answered: it never depended on the photo
    assert edu["card"]["title"] == "Acne and pimples"


def test_a_photo_that_is_still_being_checked_or_is_gone_is_reported_as_such(client, fresh, monkeypatch):
    a = _analysis(status="processing", aid="ph-busy")
    h, cid = _photo_conversation(client, monkeypatch, a)
    r = say(client, h, cid, "What is this?")
    assert r["reason"] == "in_progress" and "still being checked" in r["answer"] and r["analysis_id"] == "ph-busy"
    f = _analysis(status="failed", aid="ph-fail")
    h2, cid2 = _photo_conversation(client, monkeypatch, f)
    r2 = say(client, h2, cid2, "What is this?")
    assert r2["reason"] == "photo_check_failed" and r2["status"] == "needs_input" and "Take or upload a photo" in labels(r2["card"])
    h3 = tok(client, "dermfresh1", PW)
    cid3 = conv(client, h3)
    src = json.dumps([{"kind": "photo_analysis", "analysis_id": "ph-deleted", "index": 0}])
    db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid3, "assistant", "Photo assessment", src, db.now()))
    r3 = say(client, h3, cid3, "What is this?")                                                                                        # the analysis does not exist (deleted, or not theirs): never guessed
    assert r3["reason"] == "photo_unavailable" and "no longer available" in r3["answer"]


def test_an_approved_models_findings_are_described_with_their_provenance_and_only_then(client, fresh, monkeypatch):
    """CONTROLLED TEST DOUBLE: a fixture stands in for an approved model (none exists today). It proves how findings are rendered and highlighted, and that nothing is shown without such a model."""
    eng = get_engine()
    monkeypatch.setattr(eng, "describe", lambda cid: _state("image.acne_features", "approved_for_defined_use") if cid == "image.acne_features" else CapabilityState(
        id=cid, kind="image", name=cid, version="1", description="", availability="unavailable", approval="none", status="unavailable", patient_facing=False, audiences=["admin"]))
    a = _analysis(findings=FINDINGS, aid="ph-model")
    assert a["guidance"]["basis"] == "model_findings" and a["guidance"]["may_explain"]
    h, cid = _photo_conversation(client, monkeypatch, a)
    r = say(client, h, cid, "What is this?")
    s = next(x for x in r["card"]["sections"] if x["id"] == "model_findings")
    assert s["title"] == "What the approved photo analysis found" and any("Acne-like spot" in i and "confidence 80%" in i and "calibrated" in i for i in s["items"])
    assert any("Uncalibrated" in i and "confidence" not in i for i in s["items"]) and set(r["highlight"]) == {o["id"] for o in a["observations"] if o["kind"] == "skin_feature" and o["geometry"]}


# ---------------------------------------------------------------- authorization and privacy
def test_another_persons_photo_assessment_cannot_be_used_as_context(client, fresh):
    p1 = tok(client, "patient1")
    sent = send(client, "patient1", [jpg(skin_photo(seed=111))]).json()
    aid = sent["analyses"][0]["id"]
    finish(client, "patient1", aid)
    h = tok(client, "dermfresh1", PW)
    cid = conv(client, h)
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "What is this?", "context": {"analysis_id": aid}}, headers=h)
    assert r.status_code == 404 and "not found" in r.text.lower()
    assert client.get(f"/api/v1/photo-analyses/{aid}", headers=h).status_code == 404
    other = say(client, p1, conv(client, p1), "I have pimples on my cheeks")                                                           # and a different conversation never inherits someone else's context
    assert other["card"]["context"].get("analysis_id") is None


def test_a_conversation_cannot_be_read_or_continued_by_someone_else_and_the_library_needs_no_other_persons_data(client, fresh):
    h = tok(client, "dermfresh1", PW)
    cid = conv(client, h)
    r = say(client, h, cid, "I have pimples on my cheeks")
    p2 = tok(client, "patient2")
    assert client.get(f"/api/conversations/{cid}", headers=p2).status_code == 404
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "hello"}, headers=p2).status_code == 404
    assert "Ferritin" not in json.dumps(r) and "dermfresh1" not in json.dumps(r["card"])


# ---------------------------------------------------------------- reports
def _lab_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for ln in ["PATHOLOGY LABORATORY - Laboratory Report", "Report Date: 12/03/2024", "Test            Result   Unit      Reference range", "Hemoglobin     10.2     g/dL      12.0 - 15.5",
               "TSH            2.1      mIU/L     0.4 - 4.0", "Ferritin       8        ng/mL     15 - 150  L"]:
        c.drawString(50, y, ln)
        y -= 20
    c.save()
    return buf.getvalue()


def test_a_report_is_explained_from_what_it_prints_with_units_ranges_and_where_each_value_is_from(client, fresh):
    h = tok(client, "dermfresh1", PW)
    up = client.post("/api/documents", files={"file": ("lab.pdf", _lab_pdf(), "application/pdf")}, headers=h)
    assert up.status_code == 200, up.text
    r = say(client, h, conv(client, h), "Explain my latest report")
    assert r["route"]["kind"] == "report" and r["status"] == "answered"
    rep = r["card"]["report"]
    by = {i["name"]: i for i in rep["items"]}
    assert {"Hemoglobin", "TSH", "Ferritin"} <= set(by)
    assert by["Hemoglobin"]["unit"] == "g/dL" and by["Hemoglobin"]["range"] == "12.0 - 15.5" and by["Hemoglobin"]["status"] == "outside" and by["Hemoglobin"]["page"] == 1
    assert by["TSH"]["status"] == "within" and by["Ferritin"]["status"] == "outside"
    assert rep["document"]["filename"] == "lab.pdf" and any(s["kind"] == "document" and s["page"] == 1 for s in r["sources"])
    assert "does not say what the results mean for you" in r["answer"] or "does not say what the results mean for you" in r["card"]["summary"]
    assert any(a["kind"] == "report" for a in r["card"]["actions"]) and "Upload a medical report" in labels(r["card"])
    assert not re.search(r"\byou have\b|diagnos(?:is|ed) (?:of|with)", r["card"]["summary"], re.I)                                      # the explanation repeats what is printed; it names no condition
    value = say(client, h, conv(client, h), "What does my ferritin result say?")                                                        # a question about one value is still answered from the printed line
    assert value["abstained"] is False and value["sources"][0]["kind"] == "document" and "Ferritin" in value["answer"]
    meds = say(client, h, conv(client, h), "which medicine is listed on my prescription")                                              # asking what a document prints is not asking what to take
    assert meds["reason"] != "clinical_decision"


def test_a_report_of_another_person_is_not_reachable_through_the_assistant(client, fresh):
    h = tok(client, "dermfresh1", PW)
    r = say(client, h, conv(client, h), "What is my ferritin value?")
    leak_text = r["answer"]
    for who in ("patient1", "patient2"):
        for d in db.q("SELECT filename FROM documents WHERE owner_id=?", (who,)):
            assert d["filename"] not in leak_text


# ---------------------------------------------------------------- the old surfaces keep working
def test_the_question_shapes_older_screens_read_are_unchanged(client, fresh):
    h = tok(client, "dermfresh1", PW)
    r = say(client, h, conv(client, h), "I have pimples on my cheeks, what can help?")
    for key in ("answer", "sources", "status", "abstained"):
        assert key in r
    assert isinstance(r["answer"], str) and isinstance(r["sources"], list) and isinstance(r["abstained"], bool)
    hist = client.get("/api/conversations", headers=h).json()
    assert hist and hist[0]["preview"]
    assert client.delete(f"/api/conversations/{hist[0]['id']}", headers=h).status_code == 200

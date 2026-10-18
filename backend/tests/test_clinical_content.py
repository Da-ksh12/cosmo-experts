"""Clinical content governance: drafts are never used, approval is attested and bound to exact text, and the urgent-symptom policy is the conservative
built-in default unless a validated clinician-approved file is installed. NOTHING here approves real clinical content: the approved-policy fixtures
below are synthetic test files written to a temporary directory and are named as such."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import clinical_content as cc, db
from app.assistant import engine
from app.main import app

ATTEST = {"attest": True, "sources_checked": "Test fixture: compared with the draft text only."}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def tok(client, user):
    r = client.post("/api/auth/login", json={"user_id": user, "password": "cosmo-dev"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def ask(client, h, text):
    cid = client.post("/api/conversations", headers=h).json()["id"]
    return client.post(f"/api/conversations/{cid}/messages", json={"text": text}, headers=h).json()


def article(client, doc, title):
    return next(a for a in client.get("/api/clinic/kb", headers=doc).json() if a["title"] == title)


# ---------------------------------------------------------------- shipped drafts
def test_shipped_kb_drafts_are_seeded_as_draft_and_unused(client):
    doc, p = tok(client, "doctor1"), tok(client, "patient1")
    rows = client.get("/api/clinic/kb", headers=doc).json()
    drafts = {a["id"]: a for a in cc.kb_drafts()}
    assert len(drafts) >= 10
    for a in rows:
        if a["id"] in drafts:
            assert a["content_origin"] == "ai_draft"
            if a["review_status"] == "draft":                                                  # (another test in the shared database may have approved one, with attestation)
                assert a["usable_by_assistant"] is False
            else:
                assert a["usable_by_assistant"] and a["reviewed_by"] and json.loads(db.q1("SELECT attestation FROM kb_articles WHERE id=?", (a["id"],))["attestation"])
            assert a["claims_to_verify"], "every drafted article lists the claims a reviewer must verify"
    r = ask(client, p, "Why can two laboratories print different ranges for the same test?")
    for s in r["sources"]:                                                                    # only approved articles may ever be cited, never a draft
        if s["kind"] == "knowledge_base":
            assert db.q1("SELECT review_status FROM kb_articles WHERE id=?", (s["article_id"],))["review_status"] == "approved"


def test_seeding_is_idempotent(client):
    assert cc.seed_kb() == 0


def test_drafts_are_non_prescriptive_and_name_no_condition():
    deny = re.compile(r"\b(cancer|diabet\w*|an(a)?emia|infection|melanoma|psoriasis|eczema|hypertension|antibiotic\w*|steroid\w*|ibuprofen|paracetamol)\b", re.I)
    for a in cc.kb_drafts():
        assert not engine.REFUSE.search(a["body"]), a["id"]
        assert not deny.search(a["body"]), a["id"]
        assert a["claims_to_verify"] and a["title"] and a["body"]


# ---------------------------------------------------------------- approval
def test_approval_needs_attestation_a_source_note_and_a_doctor(client):
    doc, p, adm = tok(client, "doctor1"), tok(client, "patient1"), tok(client, "admin1")
    a = article(client, doc, "What do the High and Low markers on a report mean?")
    url = f"/api/clinic/kb/{a['id']}/approve"
    assert client.post(url, headers=doc).status_code == 422
    assert client.post(url, json={"attest": True, "sources_checked": "x"}, headers=doc).status_code == 422
    assert client.post(url, json={"attest": False, "sources_checked": "long enough note here"}, headers=doc).status_code == 422
    assert client.post(url, json=ATTEST, headers=p).status_code == 403
    assert client.post(url, json=ATTEST, headers=adm).status_code == 403                       # an administrator cannot approve clinical content
    assert article(client, doc, a["title"])["review_status"] == "draft"


def test_approval_is_recorded_and_bound_to_the_exact_text(client):
    doc, p = tok(client, "doctor1"), tok(client, "patient2")
    title = "Why do units matter on a report?"
    a = article(client, doc, title)
    r = client.post(f"/api/clinic/kb/{a['id']}/approve", json=ATTEST, headers=doc)
    assert r.status_code == 200
    row = db.q1("SELECT * FROM kb_articles WHERE id=?", (a["id"],))
    att = json.loads(row["attestation"])
    assert row["reviewed_by"] == "doctor1" and row["reviewed_at"] and row["source_note"] == ATTEST["sources_checked"]
    assert att["content_sha256"] == row["content_sha256"] == cc.article_hash(row["title"], row["body"], row["language"])
    assert db.q1("SELECT 1 x FROM audit WHERE action='kb.approve' AND target=?", (a["id"],))
    cited = ask(client, p, "Why do units matter on a report?")
    assert any(s["kind"] == "knowledge_base" and s["title"] == title for s in cited["sources"])

    # editing returns the article to draft, bumps the version and stops the assistant using it
    e = client.put(f"/api/clinic/kb/{a['id']}", json={"title": title, "body": row["body"] + " Ask the laboratory if unsure."}, headers=doc)
    assert e.status_code == 200 and e.json() == {"review_status": "draft", "version": row["version"] + 1}
    assert not any(s["kind"] == "knowledge_base" for s in ask(client, p, "Why do units matter on a report?")["sources"])
    assert client.put(f"/api/clinic/kb/{a['id']}", json={"title": title, "body": " "}, headers=doc).status_code == 422
    assert client.put(f"/api/clinic/kb/{a['id']}", json={"title": title, "body": "x"}, headers=p).status_code == 403
    assert client.put("/api/clinic/kb/nope", json={"title": "t", "body": "b"}, headers=doc).status_code == 404


def test_text_changed_behind_the_applications_back_is_not_used(client):
    doc, p = tok(client, "doctor1"), tok(client, "patient2")
    title = "What is a quotation and is it the final price?"
    a = article(client, doc, title)
    assert client.post(f"/api/clinic/kb/{a['id']}/approve", json=ATTEST, headers=doc).status_code == 200
    assert any(x["id"] == a["id"] for x in cc.approved_articles())
    db.x("UPDATE kb_articles SET body=body || ' INSERTED CLAIM' WHERE id=?", (a["id"],))     # direct database edit, approval hash now stale
    assert not any(x["id"] == a["id"] for x in cc.approved_articles())
    assert article(client, doc, title)["usable_by_assistant"] is False
    # a legacy 'approved' row with no approval record is excluded as well (fail closed)
    db.x("UPDATE kb_articles SET content_sha256='' WHERE id=?", (a["id"],))
    assert not any(x["id"] == a["id"] for x in cc.approved_articles())


def test_production_requires_a_verified_clinician(client, monkeypatch):
    doc = tok(client, "doctor1")
    a = article(client, doc, "How to prepare questions for your doctor")
    monkeypatch.setenv("COSMO_MODE", "production")
    db.x("UPDATE users SET verified=0 WHERE id='doctor1'")
    r = client.post(f"/api/clinic/kb/{a['id']}/approve", json=ATTEST, headers=doc)
    assert r.status_code == 403 and "verified" in r.json()["detail"].lower()
    db.x("UPDATE users SET verified=1, credentials='TEST FIXTURE credentials' WHERE id='doctor1'")
    assert client.post(f"/api/clinic/kb/{a['id']}/approve", json=ATTEST, headers=doc).status_code == 200
    att = json.loads(db.q1("SELECT attestation FROM kb_articles WHERE id=?", (a["id"],))["attestation"])
    assert att["verified"] is True and att["credentials"] == "TEST FIXTURE credentials"
    db.x("UPDATE users SET verified=0, credentials='' WHERE id='doctor1'")


# ---------------------------------------------------------------- urgent-symptom policy
DRAFT = json.loads((cc.CONTENT_DIR / "urgent_policy.draft.json").read_text(encoding="utf-8"))


def test_the_draft_policy_is_not_active(client):
    p = tok(client, "patient1")
    assert cc.urgent_policy() is cc._DEFAULT and not cc.urgent_policy().approved
    assert ask(client, p, "I have slurred speech and my face is drooping")["reason"] != "urgent"   # in the draft, deliberately not in the default
    assert ask(client, p, "I have chest pain")["reason"] == "urgent"                                   # the conservative default still works
    st = client.get("/api/clinic/content-status", headers=tok(client, "doctor1")).json()
    assert st["urgent_policy_approved"] is False and "built-in default" in st["urgent_policy"]
    assert client.get("/api/clinic/content-status", headers=p).status_code == 403


def test_the_draft_file_is_internally_consistent_for_the_reviewer():
    assert "NOT CLINICALLY REVIEWED" in DRAFT["_meta"]["status"] and "approval" not in DRAFT
    ids = set()
    for c in DRAFT["categories"]:
        assert c["id"] not in ids and c["rationale"] and c["review_questions"]
        ids.add(c["id"])
        rx = re.compile("|".join(f"(?:{x})" for x in c["patterns"]), re.I)
        for t in c["example_positive"]:
            assert rx.search(t), (c["id"], t)
        for t in c["example_negative"]:
            assert not rx.search(t), (c["id"], t)
        for t in c.get("known_false_positives", []):
            assert rx.search(t), (c["id"], t)                                                         # documented over-escalation really happens
    # every phrase the built-in default escalates is also covered by the draft, so approving the draft cannot reduce safety
    allrx = re.compile("|".join(f"(?:{x})" for c in DRAFT["categories"] for x in c["patterns"]), re.I)
    for t in ["chest pain", "I can't breathe", "cannot breathe", "difficulty breathing", "severe bleeding", "unconscious", "suicidal", "anaphylaxis",
              "swelling of the face", "swelling of lips", "high fever", "fainting"]:
        assert allrx.search(t), t
    for lang in DRAFT["language_candidates_NEEDS_NATIVE_SPEAKER_CLINICIAN"]["hinglish_latin_script"].values():
        assert not any(allrx.search(t) for t in lang), "unverified Hinglish candidates must not be inside the draft patterns"
    with pytest.raises(ValueError):                                                                   # the draft cannot be loaded as an approved policy
        cc.validate_policy({"policy": {"message": DRAFT["proposed_message"], "patterns": ["x"]}})


def _approved_doc(**over):
    pol = {"message": "TEST FIXTURE emergency message.", "patterns": [r"\bslurred speech\b", r"\bface (is )?droop\w*\b"]}
    pol.update(over.pop("policy", {}))
    ap = {"reviewer_name": "TEST FIXTURE (not a clinician)", "reviewer_role": "test", "registration_number": "TEST-0", "approved_on": "2000-01-01",
          "content_sha256": cc.sha(pol), "statement": "Synthetic file for unit tests."}
    ap.update(over)
    return {"policy": pol, "approval": ap}


@pytest.fixture
def content_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CONTENT_DIR", tmp_path)
    cc._cache.update(key=None, policy=cc._DEFAULT, problem="")
    yield tmp_path
    cc._cache.update(key=None, policy=cc._DEFAULT, problem="")


def test_a_valid_approved_policy_replaces_the_default_and_removal_restores_it(client, content_dir):
    p = tok(client, "patient1")
    f = content_dir / "urgent_policy.approved.json"
    f.write_text(json.dumps(_approved_doc()), encoding="utf-8")
    pol = cc.urgent_policy()
    assert pol.approved and pol.source.startswith("approved by TEST FIXTURE") and pol.message == "TEST FIXTURE emergency message."
    r = ask(client, p, "my face is drooping")
    assert r["reason"] == "urgent" and r["answer"] == "TEST FIXTURE emergency message."
    assert client.get("/api/clinic/content-status", headers=tok(client, "doctor1")).json()["urgent_policy_approved"] is True
    f.unlink()
    assert cc.urgent_policy() is cc._DEFAULT and ask(client, p, "my face is drooping")["reason"] != "urgent"


@pytest.mark.parametrize("mutate,why", [
    (lambda d: d["policy"].update(message="Changed after approval."), "hash"),
    (lambda d: d["approval"].pop("reviewer_name"), "reviewer_name"),
    (lambda d: d["approval"].update(statement=" "), "statement"),
    (lambda d: d.pop("approval"), "approval"),
    (lambda d: d["policy"].update(patterns=[]) or d["approval"].update(content_sha256=cc.sha(d["policy"])), "patterns"),
    (lambda d: d["policy"].update(patterns=["(a+)+$"]) or d["approval"].update(content_sha256=cc.sha(d["policy"])), "nested"),
    (lambda d: d["policy"].update(patterns=["(unclosed"]) or d["approval"].update(content_sha256=cc.sha(d["policy"])), "invalid"),
    (lambda d: d["policy"].update(patterns=["x" * 301]) or d["approval"].update(content_sha256=cc.sha(d["policy"])), "long"),
    (lambda d: d["policy"].update(message="") or d["approval"].update(content_sha256=cc.sha(d["policy"])), "message"),
])
def test_an_invalid_or_altered_approved_policy_falls_back_to_the_default(client, content_dir, mutate, why):
    d = _approved_doc()
    mutate(d)
    (content_dir / "urgent_policy.approved.json").write_text(json.dumps(d), encoding="utf-8")
    assert cc.urgent_policy() is cc._DEFAULT and cc._cache["problem"], why
    assert ask(client, tok(client, "patient1"), "I have chest pain")["reason"] == "urgent"             # still safe


def test_a_corrupt_policy_file_falls_back(content_dir):
    (content_dir / "urgent_policy.approved.json").write_text("{not json", encoding="utf-8")
    assert cc.urgent_policy() is cc._DEFAULT and "rejected" in cc._cache["problem"]


def test_packaging_tool_refuses_without_confirmation_and_produces_a_loadable_file(content_dir):
    from scripts import make_approved_policy as mk
    src = content_dir / "reviewed.json"
    src.write_text(json.dumps({"message": "TEST FIXTURE message.", "patterns": [r"\bslurred speech\b"]}), encoding="utf-8")
    out = content_dir / "urgent_policy.approved.json"
    args = [str(src), "--name", "TEST FIXTURE", "--role", "test", "--registration", "T-0", "--date", "2000-01-01", "--out", str(out)]
    assert mk.main(args) == 2 and not out.exists()
    assert mk.main(args + ["--confirm-reviewer-approved"]) == 0
    assert cc.urgent_policy().approved and cc.urgent_policy().regex.search("slurred speech")
    src.write_text(json.dumps({"message": "m", "patterns": ["(a+)+$"]}), encoding="utf-8")
    with pytest.raises(ValueError):
        mk.main(args + ["--confirm-reviewer-approved"])


def test_approval_lapses_when_the_scheduled_review_date_passes(client, monkeypatch):
    doc = tok(client, "doctor1")
    title = "Do I need to prepare for a test?"
    a = article(client, doc, title)
    monkeypatch.setenv("COSMO_KB_REVIEW_DAYS", "30")
    assert client.post(f"/api/clinic/kb/{a['id']}/approve", json=ATTEST, headers=doc).status_code == 200
    row = db.q1("SELECT review_due, reviewed_at FROM kb_articles WHERE id=?", (a["id"],))
    assert abs(row["review_due"] - (row["reviewed_at"] + 30 * 86400)) < 5
    assert any(x["id"] == a["id"] for x in cc.approved_articles())
    db.x("UPDATE kb_articles SET review_due=? WHERE id=?", (row["reviewed_at"] - 1, a["id"]))       # the due date has passed
    assert not any(x["id"] == a["id"] for x in cc.approved_articles())
    listed = article(client, doc, title)
    assert listed["review_overdue"] is True and listed["usable_by_assistant"] is False and listed["review_status"] == "approved"
    # re-approval renews it
    assert client.post(f"/api/clinic/kb/{a['id']}/approve", json=ATTEST, headers=doc).status_code == 200
    assert any(x["id"] == a["id"] for x in cc.approved_articles()) and article(client, doc, title)["review_overdue"] is False
    monkeypatch.setenv("COSMO_KB_REVIEW_DAYS", "0")
    assert cc.review_period_days() == 0
    monkeypatch.setenv("COSMO_KB_REVIEW_DAYS", "garbage")
    assert cc.review_period_days() == 365

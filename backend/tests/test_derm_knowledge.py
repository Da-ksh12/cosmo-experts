"""The dermatology library (content/derm, app/derm): the pack is complete and traceable, every statement passes the safety policy for skincare guidance, nothing is ever marked approved by
software, the review tiers follow the clinic's approval workflow, retrieval matches its recorded evaluation, and the warning-sign rules do what the clinic's reviewers are told they do.

These tests check the MECHANISM and the INTEGRITY of the content. They say nothing about whether the clinical content is right: that is what the clinician review is for."""
import json
import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import clinical_content as cc, db
from app.ai import registry, safety
from app.assistant.retrieval import HybridRetriever
from app.derm import lexicon, pack, reference, triage
from app.main import app

REPORT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval" / "derm_retrieval.json"
ATTEST = {"attest": True, "sources_checked": "Test reviewer checked the text against the listed sources."}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


@pytest.fixture(autouse=True)
def _restore_library(client):
    """Tests approve and edit library rows; put every one back to the state software ships (an unreviewed draft) so no test depends on another."""
    yield
    for a in pack.load().articles.values():
        db.x("DELETE FROM kb_articles WHERE id=?", (a.id,))
    pack.seed_drafts()
    registry.invalidate()


# ---------------------------------------------------------------- the pack itself
def test_the_pack_loads_and_every_article_is_complete_and_traceable():
    p, problems = pack.validate()
    assert problems == [] and p is not None
    assert len(p.articles) >= 15 and len(p.sources) >= 20
    topics = {a.topic for a in p.articles.values()}
    # the topics the product promises: acne, pigmentation, redness, dryness, routines, sun protection, ingredients, habits, when to seek care, warning signs, photo limits
    assert {"acne", "pigmentation", "redness", "dry-skin", "routine", "sun-protection", "ingredients", "habits", "when-to-see", "warning-signs", "photo-limits", "moles"} <= topics
    used = set()
    for a in p.articles.values():
        assert a.source_ids and a.claims_to_verify and a.limitations and a.summary and a.sections["what_it_is"] and a.sections["see_clinician"]
        assert a.review.status == "pending_clinician_review" and a.review.reviewed_by is None and a.review.reviewed_on is None       # software never marks anything reviewed
        used |= set(a.source_ids)
        for sid in a.source_ids:
            s = p.sources[sid]
            assert s.url.startswith("https://") and s.accessed == "2026-10-21" and s.terms.basis in pack.TERMS_BASES and s.terms.checked
    assert used == set(p.sources)                                                                  # no source is listed that nothing rests on


def test_sources_state_their_dates_and_flag_pages_whose_own_review_date_has_passed():
    p = pack.load()
    overdue = [s for s in p.sources.values() if s.source_review_overdue]
    assert overdue and all(s.next_review_due and s.next_review_due < "2026-10-21" for s in overdue)           # the page itself says it was due for review: that is recorded, not hidden
    assert all(s.page_reviewed is None or re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", s.page_reviewed) for s in p.sources.values())
    restricted = [s for s in p.sources.values() if s.terms.basis == "restricted_facts_only"]
    assert restricted and all("aad.org" in s.url for s in restricted)                                           # a copyrighted source is used for facts in original words, with a link


def test_a_pack_that_claims_a_reviewer_or_cites_an_unknown_source_does_not_load(tmp_path):
    import shutil
    d = tmp_path / "derm"
    shutil.copytree(pack.pack_dir(), d)
    f = d / "articles" / "acne.json"
    j = json.loads(f.read_text(encoding="utf-8"))
    j["review"]["reviewed_by"] = "Dr Nobody"
    f.write_text(json.dumps(j), encoding="utf-8")
    pk, problems = pack.validate(d)
    assert pk is None and any("must not name a reviewer" in x for x in problems)
    j["review"]["reviewed_by"] = None
    j["source_ids"] = ["no-such-source"]
    f.write_text(json.dumps(j), encoding="utf-8")
    pk, problems = pack.validate(d)
    assert pk is None and any("unknown source" in x for x in problems)
    j["source_ids"] = ["nhs-acne"]
    j["review"]["status"] = "approved"
    f.write_text(json.dumps(j), encoding="utf-8")
    assert pack.validate(d)[0] is None                                                              # the pack cannot carry the word 'approved' at all


def test_every_statement_in_the_library_passes_the_safety_policy_for_skincare_guidance():
    p = pack.load()
    for a in p.articles.values():
        texts = [a.summary] + [i for items in a.sections.values() for i in items] + [t.plain for t in a.terms] + a.contraindications + a.limitations
        for text in texts:
            rev = safety.review_text(text, kind="skincare_guidance")
            assert rev.allowed, (a.id, rev.codes(), text[:100])
        joined = " ".join(texts)
        assert not re.search(r"\b(?:cures?|will cure|guarantee\w*|miracle|permanent(?:ly)? (?:remove|clear)|100\s?%)\b", joined, re.I), a.id          # no promise of a cure (saying something CANNOT be cured is honest)
        assert not safety.PRESCRIPTION_DRUGS.search(joined) and not safety.DOSE_OR_STRENGTH.search(joined), a.id                             # no prescription-only drug, no dose, no strength


def test_the_skincare_review_allows_self_care_wording_but_still_blocks_medicines_diagnoses_and_doses():
    ok = ["Use a gentle cleanser and apply sunscreen every morning.", "You can use a fragrance-free moisturiser after washing.", "Please apply it to the whole area, not only the visible spots."]
    for s in ok:
        assert safety.review_text(s, kind="skincare_guidance").allowed, s
    assert not safety.review_text(s := "You can use a fragrance-free moisturiser.", kind="education").allowed                              # the general policy keeps its stricter reading
    bad = {"You should stop taking your medicine for this.": "medication_change", "Take isotretinoin for six months.": "prescription_drug", "Apply a 10% cream twice a day.": "dose_or_strength",
           "You have acne vulgaris.": "diagnostic_claim", "This will definitely clear it.": "certainty_claim", "Take 20 mg every day.": "dose_or_strength"}
    for text, code in bad.items():
        rev = safety.review_text(text, kind="skincare_guidance")
        assert not rev.allowed and code in rev.codes(), (text, rev.codes())


def test_skincare_questions_are_not_mistaken_for_requests_for_medicine():
    for q in ("Should I use sunscreen every day?", "Can I start using a vitamin C serum?", "Should I use a moisturiser after washing?"):
        assert safety.classify_request(q, domain="skincare").kind == "education", q
        assert safety.classify_request(q).kind == "medication_advice", q                                  # the default reading is unchanged for every other caller
    for q in ("Should I take isotretinoin?", "Which tablet should I take for acne?", "What dose of doxycycline should I take?", "Should I stop my medicine?"):
        assert safety.classify_request(q, domain="skincare").kind == "medication_advice", q


# ---------------------------------------------------------------- the review workflow: drafts, approval, tiers
def test_seeding_inserts_drafts_only_and_never_overwrites_a_clinicians_work(client):
    p = pack.load()
    rows = {r["id"]: r for r in db.q("SELECT * FROM kb_articles WHERE id LIKE 'derm.%'")}
    assert set(rows) == set(p.articles)
    assert all(r["review_status"] == "draft" and r["content_origin"] == "ai_draft" and not r["reviewed_by"] and not r["content_sha256"] for r in rows.values())
    acne = p.articles["derm.acne"]
    assert rows["derm.acne"]["body"] == pack.render_body(acne) and json.loads(rows["derm.acne"]["claims_to_verify"]) == acne.claims_to_verify
    assert "nhs.uk/conditions/acne" in rows["derm.acne"]["source_note"] and "page review date 2026-09-22" in rows["derm.acne"]["source_note"]
    db.x("UPDATE kb_articles SET body=? WHERE id='derm.acne'", ("A clinician's own wording.",))
    assert pack.seed_drafts() == 0 and db.q1("SELECT body FROM kb_articles WHERE id='derm.acne'")["body"] == "A clinician's own wording."      # idempotent, and an edit is never overwritten


def _approve(client, aid):
    doc = tok(client, "doctor1")
    r = client.post(f"/api/clinic/kb/{aid}/approve", json=ATTEST, headers=doc)
    assert r.status_code == 200, r.text
    registry.invalidate()


def test_the_tier_of_an_article_follows_the_clinics_approval_not_the_softwares(client, monkeypatch):
    monkeypatch.delenv("COSMO_KB_REFERENCE", raising=False)
    monkeypatch.delenv("COSMO_MODE", raising=False)
    shown = reference.resolve_all()
    assert len(shown) == len(pack.load().articles) and {r.tier for r in shown.values()} == {"reference"}
    assert all("has not reviewed this wording yet" in r.label for r in shown.values())
    _approve(client, "derm.acne")                                                                   # a verified clinician approves the exact text the software seeded
    shown = reference.resolve_all()
    assert shown["derm.acne"].tier == "approved" and shown["derm.acne"].label == reference.APPROVED_LABEL and shown["derm.acne"].edited_by_clinician is False
    assert shown["derm.rosacea"].tier == "reference"
    # the clinician edits the text: it returns to draft (the existing rule) and the patient sees the library's own text, labelled again as unreviewed
    doc = tok(client, "doctor1")
    row = db.q1("SELECT title,body FROM kb_articles WHERE id='derm.acne'")
    assert client.put("/api/clinic/kb/derm.acne", json={"title": row["title"], "body": row["body"] + "\n- One more sentence."}, headers=doc).status_code == 200
    assert reference.resolve_all()["derm.acne"].tier == "reference"
    # ... and when they approve THEIR text, THEIR text is what patients see
    _approve(client, "derm.acne")
    r = reference.resolve_all()["derm.acne"]
    assert r.tier == "approved" and r.edited_by_clinician and any("One more sentence" in i for items in r.sections.values() for i in items) and "clinic_text" in r.sections


def test_an_approval_that_lapsed_or_no_longer_matches_its_text_is_not_trusted(client):
    _approve(client, "derm.eczema")
    assert reference.resolve_all()["derm.eczema"].tier == "approved"
    db.x("UPDATE kb_articles SET review_due=? WHERE id='derm.eczema'", (db.Epoch(time.time() - 10),))
    assert reference.resolve_all()["derm.eczema"].tier == "reference"                               # lapsed: the approved label is gone
    db.x("UPDATE kb_articles SET review_due=NULL WHERE id='derm.eczema'")
    assert reference.resolve_all()["derm.eczema"].tier == "approved"
    db.x("UPDATE kb_articles SET body=body || ' tampered' WHERE id='derm.eczema'")                  # changed after approval without going through the workflow
    assert reference.resolve_all()["derm.eczema"].tier == "reference"


def test_in_production_unreviewed_articles_are_not_shown_unless_the_clinic_decides_so(client, monkeypatch):
    _approve(client, "derm.sun-protection")                                                         # approval needs a verified clinician in production, so approve first (this is the demo's workflow)
    monkeypatch.setenv("COSMO_MODE", "production")
    monkeypatch.delenv("COSMO_KB_REFERENCE", raising=False)
    assert reference.reference_mode() == "off"
    shown = reference.resolve_all()
    assert set(shown) == {"derm.sun-protection"} and shown["derm.sun-protection"].tier == "approved"             # approved articles are shown in production; nothing else
    monkeypatch.setenv("COSMO_KB_REFERENCE", "labelled")                                            # the clinic's explicit decision
    assert len(reference.resolve_all()) == len(pack.load().articles)
    monkeypatch.setenv("COSMO_KB_REFERENCE", "off")
    monkeypatch.delenv("COSMO_MODE")
    assert reference.reference_mode() == "off"


def test_the_clinic_can_see_every_article_its_sources_and_what_to_confirm(client):
    r = client.get("/api/clinic/derm-library", headers=tok(client, "doctor1")).json()
    assert r["usable"] and len(r["articles"]) == len(pack.load().articles) and r["policy"]["status"] == "pending_clinician_review"
    a = next(x for x in r["articles"] if x["id"] == "derm.sun-protection")
    assert a["claims_to_verify"] and a["tier"] == "reference" and a["review_status"] == "draft" and any(s["url"].startswith("https://www.nhs.uk/") for s in a["sources"])
    assert len(r["triage_rules"]) == len(triage.RULES) and all(x["status"] == "pending_clinician_review" for x in r["triage_rules"])
    assert client.get("/api/clinic/derm-library", headers=tok(client, "patient1")).status_code == 403
    st = client.get("/api/clinic/content-status", headers=tok(client, "doctor1")).json()
    assert st["derm_library"]["usable"] and st["derm_library"]["awaiting_review"] == len(r["articles"])


# ---------------------------------------------------------------- finding the article
def test_topics_are_found_in_english_hinglish_and_hindi():
    idx = reference.keyword_index()
    cases = {"How do I get rid of pimples?": "acne", "mere chehre par muhase hain": "acne", "मुंहासे कैसे ठीक करें": "acne", "dark spots after pimples are not fading": "acne-marks",
             "my skin is very dry and flaky": "dry-skin", "pigmentation ke daag": "pigmentation", "which sunscreen SPF should I use": "sun-protection", "is my mole dangerous": "moles",
             "sensitive skin burning after a new cream": "product-reactions", "rukhi twacha": "dry-skin"}
    for text, want in cases.items():
        got = [t for t, _ in lexicon.detect_topics(text, idx)]
        assert got and want in got[:2], (text, got)
    assert lexicon.detect_topics("Who won the cricket world cup?", idx) == [] and lexicon.detect_topics("What is the capital of France?", idx) == []


def test_a_concern_is_the_subject_and_a_general_topic_adds_to_it(client):
    f = reference.find("What should I avoid with acne?")
    assert f.resolved.article.topic == "acne" and f.secondary and f.secondary.article.topic == "habits" and f.secondary_role == "general"
    g = reference.find("Suggest a gentle skincare routine", concern="pigmentation")                  # a general question, tailored by the concern the conversation already established
    assert g.resolved.article.topic == "routine" and g.secondary.article.topic == "pigmentation" and g.secondary_role == "concern"
    h = reference.find("What is sunscreen?", concern="acne")
    assert h.resolved.article.topic == "sun-protection" and h.secondary_role in ("", "concern")                    # the question's own subject is never replaced by the established concern
    miss = reference.find("zxqv blorp")
    assert miss is None or miss.how in ("retrieval", "context")


def test_retrieval_thresholds_match_the_recorded_evaluation_and_lexical_matching_is_not_a_fallback():
    rec = json.loads(REPORT.read_text(encoding="utf-8"))
    assert rec["evidence_class"] == "synthetic_fixture" and rec["dataset"]["pack_sha256"]
    assert reference.REF_THRESHOLDS["dense"] == rec["results"]["dense"]["development"]["threshold"]
    assert reference.REF_THRESHOLDS["hybrid(0.5)"] == rec["results"]["hybrid0.5"]["development"]["threshold"]
    assert reference.REF_THRESHOLDS["lexical"] == rec["results"]["lexical"]["development"]["threshold"]
    held = rec["results"]["dense"]["held_out"]
    assert held["false_answer_rate"] <= 0.05 and held["correct_answer_rate"] >= 0.7                    # what the evaluation recorded for the default mode
    assert reference.RETRIEVAL_FALLBACK_MODES == ("dense", "hybrid(0.5)")
    assert reference.find("blorp zxqv wibble", retriever=HybridRetriever(None)) is None              # without the embedding model there is no retrieval fallback: topic keywords or nothing


def test_the_pack_changed_since_the_evaluation_is_noticed():
    """If an article changes, the recorded thresholds were measured on other text: the hash differs and this test says to run eval/derm_retrieval_eval.py again."""
    rec = json.loads(REPORT.read_text(encoding="utf-8"))
    assert rec["dataset"]["pack_sha256"] == pack.load().sha256, "the library changed: re-run backend/eval/derm_retrieval_eval.py and update REF_THRESHOLDS"


# ---------------------------------------------------------------- warning signs
LEVEL_CASES = [
    ("I have a few pimples on my cheeks", "routine"), ("what is a good sunscreen for oily skin", "routine"), ("I have no fever and a pimple on my chin", "routine"),
    ("pimple with no swelling and no pain", "routine"), ("I have cold sores on my lips", "routine"), ("mere chehre par muhase hain", "routine"),
    ("my lips and tongue are swollen after a tablet", "emergency"), ("my face is swollen and I can't breathe after a new cream", "emergency"),
    ("a rash that does not fade when I press a glass on it and I have a fever", "emergency"), ("blisters in my mouth and eyes with a rash spreading all over", "emergency"),
    ("skin infection and I feel confused and faint", "emergency"), ("mera chehra sooj gaya hai aur bukhar hai", "emergency"),
    ("red area on my leg is spreading quickly and hot and painful", "urgent"), ("I have a fever and a rash since yesterday", "urgent"), ("eye pain and blurred vision with a rash on my forehead", "urgent"),
    ("painful burning blisters on one side of my face", "urgent"), ("my eczema is weeping and crusty and painful", "urgent"), ("a rash appeared after starting a new antibiotic", "urgent"),
    ("my baby has a rash and fever", "urgent"), ("I got a chemical burn on my arm", "urgent"),
    ("a mole on my back is getting bigger and itchy", "prompt"), ("a sore on my nose that does not heal", "prompt"), ("my acne is deep and painful and leaving scars", "prompt"),
    ("I have a dark line under my nail", "prompt"), ("my acne is making me depressed", "prompt"), ("sudden hair loss in patches", "prompt"), ("I am worried this is skin cancer", "prompt"),
]


@pytest.mark.parametrize("text,level", LEVEL_CASES)
def test_warning_signs_are_raised_at_the_right_level_and_with_a_reason(text, level):
    t = triage.assess(text)
    assert t.level == level, (text, t.level, t.rules)
    if level != "routine":
        assert t.reasons and t.rules
    else:
        assert not t.reasons and not t.escalated()


def test_a_negated_sign_does_not_raise_anything_and_an_old_alarm_is_not_raised_again():
    assert triage.assess("no fever and no swelling, just a pimple").level == "routine"
    assert triage.assess("without any fever, I have a rash").level in ("routine", "prompt")
    first = triage.assess("my lips and tongue are swollen")
    assert first.level == "emergency"
    followup = triage.assess("who won the cricket world cup", prior="my lips and tongue are swollen")
    assert followup.level == "routine"                                                              # the previous message's alarm does not follow every later message
    combined = triage.assess("yes and I can't breathe", prior="my lips are swollen after eating")
    assert combined.level == "emergency"                                                            # but a sign completed by the next message still counts


def test_a_rash_or_acne_that_has_lasted_long_enough_is_worth_a_clinician():
    assert triage.assess("I have an itchy rash on my arm", duration_days=28).level == "prompt"
    assert triage.assess("I have an itchy rash on my arm", duration_days=5).level == "routine"
    assert triage.assess("I have acne", duration_days=70).level == "prompt" and triage.assess("I have acne", duration_days=20).level == "routine"
    assert triage.assess("my rash is spreading fast, hot and painful", duration_days=40).level == "urgent"      # a higher level is never lowered by a duration rule


def test_the_emergency_wording_is_always_the_clinics_policy_message_and_the_reason_adds_no_new_instructions():
    t = triage.assess("my lips and tongue are swollen")
    msg = triage.message(t)
    assert msg == safety.urgent_message()                                                           # exactly the policy's words; the reason is a separate field the app shows beneath it
    rev = safety.review_text(msg, kind="skincare_guidance")
    assert rev.allowed, rev.codes()                                                                 # the only emergency instructions in it are the policy's own sentences
    assert t.reasons and safety.review_text(" ".join(t.reasons), kind="skincare_guidance").allowed
    for r in triage.RULES:
        assert safety.review_text(r.reason, kind="skincare_guidance").allowed, r.id
        assert not safety.EMERGENCY_GUIDANCE.search(r.reason), r.id


def test_every_rule_is_described_for_the_reviewers_and_points_at_real_sources_and_articles():
    sheet = triage.review_sheet()
    assert len(sheet) == len(triage.RULES) and len({r["id"] for r in sheet}) == len(sheet)
    p = pack.load()
    for r in sheet:
        assert r["trigger"] and r["reason_shown"] and r["status"] == "pending_clinician_review" and r["level"] in ("emergency", "urgent", "prompt")
        assert set(r["sources"]) <= set(p.sources), r["id"]
        assert r["article"] is None or r["article"] in p.articles, r["id"]
    assert triage.policy_status()["emergency_message_approved"] in (True, False)


def test_slots_are_read_from_the_persons_own_words_and_a_negated_symptom_is_not_one():
    s = lexicon.extract_slots(["it has been there for 3 weeks and it is itchy but there is no pain", "I use a vitamin c serum and I am pregnant"])
    assert s.duration_days == 21 and s.symptoms == {"itch"} and s.products and s.pregnant and s.child is False
    assert lexicon.extract_slots(["Should I use sunscreen?"]).products is False                      # a question about a product is not a statement that it is used
    assert lexicon.extract_slots(["None of these symptoms."]).none_of_symptoms is True
    assert lexicon.extract_slots(["I have never reacted to skincare products."]).prior_reaction is False
    assert lexicon.extract_slots(["mere beta ko khujli hai"]).symptoms == {"itch"} and lexicon.extract_slots(["my baby has dry skin"]).child is True
    assert lexicon.duration_days("4 hafte se") == 28 and lexicon.duration_days("for a few months") == 90 and lexicon.duration_days("hello") is None

"""Original, normalized and translated text are kept apart; numbers, dates, units, printed text and names survive every rendering; unreviewed text says so; nothing claims Hindi or Hinglish quality
that was not measured (ai/multilingual.py)."""
import pytest

from app.ai import multilingual as ml
from app.documents import explain

DOC = {"id": "doc1"}
PAGES = [{"method": "embedded", "ocr_confidence": None}]


def test_protected_tokens_are_the_numbers_dates_units_printed_text_and_names():
    t = 'Ferritin 8 ng/mL on 12/03/2025, range 15 - 150. The document says "Tab Metformin 500 mg BD" - page 2.'
    toks = ml.protected_tokens(t, names=["Ferritin", "Metformin"])
    for want in ("8", "ng/mL", "12/03/2025", "15", "150", "Tab Metformin 500 mg BD", "500", "mg", "2", "Ferritin", "Metformin"):
        assert want in toks, (want, toks)


@pytest.mark.parametrize("rendered,lost", [
    ("Ferritin 8 ng/mL", []),
    ("फेरिटिन 8 ng/mL", ["Ferritin"]) ,                       # the name is a protected token when it is given as one
    ("Ferritin 80 ng/mL", ["8"]),                              # 80 is not 8
    ("Ferritin 8", ["ng/mL"]),
    ("Ferritin ८ ng/mL", []),                                  # the same number in Devanagari digits is the same number
    ("Ferritin 8,5 ng/mL", ["8"]),
])
def test_a_rendering_that_drops_or_alters_a_protected_token_is_found_out(rendered, lost):
    assert ml.preserved("Ferritin 8 ng/mL", rendered, names=["Ferritin"]) == lost


def test_dates_and_printed_text_must_survive_unchanged():
    assert ml.preserved('Reported 12/03/2025: "Tab Aspirin 75 mg OD"', 'रिपोर्ट 12/03/2025: "Tab Aspirin 75 mg OD"') == []
    assert ml.preserved('Reported 12/03/2025: "Tab Aspirin 75 mg OD"', 'रिपोर्ट 12/03/2025: "Tab Aspirin 75 mg"') == ["Tab Aspirin 75 mg OD"]
    assert "12/03/2025" in ml.preserved("Reported 12/03/2025", "Reported 3 December 2025")


def test_a_bundle_keeps_the_original_the_normalized_copy_and_each_rendering_apart():
    b = ml.bundle("Ferritin ८ ng/mL", language="en", names=["Ferritin"], renderings=[
        ml.Rendering("hi", "फेरिटिन ८ ng/mL", "template", reviewed=False),
        ml.Rendering("hinglish", "Ferritin 8 ng/mL", "template", reviewed=True, reviewed_by="Dr X")])
    assert b["original"] == {"language": "en", "text": "Ferritin ८ ng/mL"} and b["normalized"]["text"] == "Ferritin 8 ng/mL" and b["normalized"]["changed"] is True
    hi, hg = b["renderings"]
    assert hi["faithful"] is False and hi["missing"] == ["Ferritin"] and "Not reviewed" in hi["notice"]
    assert hg["faithful"] is True and hg["notice"] is None and hg["reviewed_by"] == "Dr X"


def test_every_fixed_hindi_and_hinglish_sentence_carries_what_its_english_counterpart_does():
    fields = [dict(id=f"f{i}", name=n, value=v, unit=u, ref_range=r, source_page=p, source_text=f"{n}  {v}  {u}  {r}  {flag}", confidence=0.9, flag=fl, review_status="unreviewed", processing_version="x",
                   provenance="{}", ambiguity="[]")
              for i, (n, v, u, r, p, flag, fl) in enumerate([("Ferritin", "8", "ng/mL", "15 - 150", 1, "L", "printed_flag_low"), ("Hemoglobin", "13.5", "g/dL", "12.0 - 15.5", 2, "", "within_printed_range"),
                                                              ("TSH", "7.9", "mIU/L", "0.4 - 4.0", 3, "H", "printed_flag_high"), ("Platelet Count", "250", "10^3/uL", "150 - 410", 1, "", "within_printed_range")])]
    facts = [dict(id="fa1", kind="report_date", label="Report date", value="12/03/2025", source_page=1, source_text="Reported: 12/03/2025", confidence=0.8, provenance="{}", normalized="{}",
                  ambiguity='["day_month_order_ambiguous"]', review_status="unreviewed")]
    en = explain.explain_document(DOC, fields, PAGES, "en", facts=facts)
    for lang in ("hi", "hinglish"):
        other = explain.explain_document(DOC, fields, PAGES, lang, facts=facts)
        assert other["translation_reviewed"] is False and en["translation_reviewed"] is True
        by = lambda ex, i: next(s for s in ex["sections"] if s["id"] == i)           # noqa: E731
        pairs = [(a, b) for sid in ("found", "facts") for a, b in zip(by(en, sid)["items"], by(other, sid)["items"])]
        assert len(pairs) == 5
        for a, b in pairs:
            names = [f["name"] for f in fields if f["name"] in a["text"]]
            assert ml.preserved(a["text"], b["text"], names=names) == [], (lang, a["text"], b["text"])


def test_hindi_and_hinglish_explanations_carry_a_visible_unreviewed_notice():
    assert explain.explain_document(DOC, [], PAGES, "en")["translation_notice"] is None
    for lang in ("hi", "hinglish"):
        ex = explain.explain_document(DOC, [], PAGES, lang)
        assert ex["translation_reviewed"] is False and "no Hindi-speaking clinician has reviewed" in ex["translation_notice"]


def test_the_status_report_claims_only_what_was_measured():
    assert ml.language_status("en")["reviewed_translations"] is True and "synthetic English speech only" in ml.language_status("en")["speech_recognition"]
    for lang in ("hi", "hinglish"):
        s = ml.language_status(lang)
        assert s["speech_recognition"] == "not checked" and "UNREVIEWED" in s["explanations"] and s["reviewed_translations"] is False and "no claim" in s["claim"]
    assert ml.language_status("fr") == {"language": "fr", "supported": False, "note": "No part of this system has been checked for this language."}

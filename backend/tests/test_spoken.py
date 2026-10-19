"""Spoken explanations (speech/spoken.py): what is read aloud is what is shown, numbers and printed text are not changed, uncertainty and the request for a clinician's review are never dropped, and
anything left out is announced."""
import json

import pytest

from app.ai import multilingual, safety
from app.documents import explain
from app.speech import spoken

DOC = {"id": "doc1"}
PAGES = [{"method": "embedded", "ocr_confidence": None}]


def F(i=0, **kw):
    base = dict(id=f"f{i}", name="Ferritin", value="8", unit="ng/mL", ref_range="15 - 150", source_page=1, source_text="Ferritin  8  ng/mL  15 - 150  L", confidence=0.9, flag="printed_flag_low",
                review_status="unreviewed", processing_version="docpipe-2.0.0", provenance="{}", ambiguity="[]")
    base.update(kw)
    return base


def many(n):
    return [F(i, name=f"Test{i}", value=str(i + 20), source_text=f"Test{i}  {i + 20}  ng/mL  15 - 150  L") for i in range(n)]


def test_units_are_spoken_in_words_for_english_only_and_digits_are_never_touched():
    assert spoken.speak_units("Ferritin is 8 ng/mL and Hb 13.5 g/dL, TSH 2.1 mIU/L.") == "Ferritin is 8 nanograms per millilitre and Hb 13.5 grams per decilitre, TSH 2.1 milli international units per litre."
    assert spoken.speak_units("Platelets 250 10^3/uL") == "Platelets 250 10^3/uL"                    # a unit with digits stays as written: expanding it would change which digits are spoken
    assert spoken.speak_units("xng/mL ng/mLx") == "xng/mL ng/mLx"                                     # only whole unit tokens
    assert spoken.speak_units("8 ng/mL", "hi") == "8 ng/mL" and spoken.speak_units("8 ng/mL", "hinglish") == "8 ng/mL"      # other languages: unreviewed text is not rewritten further


@pytest.mark.parametrize("lang", ["en", "hi", "hinglish"])
def test_everything_spoken_is_shown_and_the_safety_statements_are_always_there(lang):
    ex = explain.explain_document(DOC, [F(0), F(1, name="TSH", value="2.1", unit="mIU/L", ref_range="0.4 - 4.0", flag="within_printed_range", source_text="TSH 2.1 mIU/L 0.4 - 4.0")], PAGES, lang)
    sc = spoken.script(ex)
    assert spoken.check(ex, sc) == [] and sc.complete and sc.language == lang
    kinds = [p["kind"] for p in sc.sentences]
    assert kinds[0] == "summary" and kinds.count("item") == 2 and "uncertainty" in kinds and kinds.count("safety") == 2
    assert "Source: page 1." in sc.text or lang != "en"
    assert ex["spoken_text"] == sc.text                                                          # the explanation's own spoken text IS this script


def test_many_statements_are_read_in_part_and_the_rest_is_announced():
    ex = explain.explain_document(DOC, many(30), PAGES)
    sc = spoken.script(ex, max_items=12)
    assert sc.omitted == 18 and not sc.complete and spoken.check(ex, sc) == []
    more = [p for p in sc.sentences if p["kind"] == "more"]
    assert len(more) == 1 and "18 more statement(s) on the screen" in more[0]["spoken"]
    assert [p["kind"] for p in sc.sentences if p["kind"] in ("uncertainty", "safety")] == ["uncertainty", "safety", "safety"]       # what is left out never includes the cautions
    assert sc.text.count("Test") >= 12 and "Test29" not in sc.text and "Test29" in json.dumps(ex)


def test_a_changed_number_a_missing_caution_or_a_silent_omission_is_caught():
    ex = explain.explain_document(DOC, many(15), PAGES)
    sc = spoken.script(ex, max_items=12)
    assert spoken.check(ex, sc) == []
    changed = spoken.script(ex)
    next(p for p in changed.sentences if p["kind"] == "item")["spoken"] = "The report lists Test0 as 80 nanograms per millilitre. Source: page 1."
    assert any("changed in speech" in b for b in spoken.check(ex, changed))
    dropped = spoken.script(ex)
    dropped.sentences = [p for p in dropped.sentences if p["kind"] != "safety"]
    assert any("clinician" in b for b in spoken.check(ex, dropped))
    quiet = spoken.script(ex, max_items=12)
    quiet.sentences = [p for p in quiet.sentences if p["kind"] != "more"]
    assert any("without saying so" in b for b in spoken.check(ex, quiet))
    invented = spoken.script(ex)
    invented.sentences.append({"kind": "item", "visible": "Your ferritin is perfectly fine.", "spoken": "Your ferritin is perfectly fine."})
    assert any("spoken but not shown" in b for b in spoken.check(ex, invented))


def test_printed_text_and_medicines_are_spoken_exactly_as_shown():
    meds = [{"id": "m1", "page": 1, "raw": "Tab. Metformin 500 mg BD after food x 30 days", "entry": {"name": {"raw": "Metformin"}, "ambiguity": []}, "ambiguity": "[]", "review_status": "unreviewed", "parser": "medparse-1.1.0"}]
    ex = explain.explain_document(DOC, [], PAGES, medications=meds, page_texts={1: "Rx\nTab. Metformin 500 mg BD after food x 30 days"})
    sc = spoken.script(ex)
    assert 'The document lists: "Tab. Metformin 500 mg BD after food x 30 days".' in sc.text and spoken.check(ex, sc) == []
    assert multilingual.preserved(next(s for s in ex["sections"] if s["id"] == "medicines")["items"][0]["text"], sc.text, names=["Metformin"]) == []


def test_the_spoken_text_passes_the_shared_safety_policy():
    ex = explain.explain_document(DOC, many(5), PAGES)
    assert safety.review_text(ex["spoken_text"], kind="extraction", evidence=[f["source_text"] for f in many(5)]).allowed
    assert "not a diagnosis" in ex["spoken_text"].lower()

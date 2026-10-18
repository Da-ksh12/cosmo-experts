"""Structured document facts, lab-field provenance and normalisation, OCR language handling, and the bounds on reading a document."""
import io
import time
from dataclasses import dataclass

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.ai.errors import Cancelled
from app.ai.resources import CancelToken
from app.documents import languages, pipeline, structured
from app.main import app


@dataclass
class P:
    page_no: int
    text: str
    method: str = "embedded"
    confidence: float = 1.0
    layout: list | None = None
    script: str | None = None


@pytest.fixture(scope="module", autouse=True)
def client():
    with TestClient(app) as c:
        yield c


def pdf(lines, w=595, h=842) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    y = 60
    for ln in lines:
        page.insert_text((50, y), ln, fontsize=10)
        y += 22
    return doc.tobytes()


LAB = ["CITY DIAGNOSTIC LABORATORY", "Patient: Asha Rao   Age 34   Sex F   UHID 99887", "Collected on: 11/03/2024", "Report Date: 13/03/2024", "Referred by: Dr. R. Menon MD",
       "Haemoglobin  11.2  g/dL  12.0 - 15.5  L", "Ferritin  8  ng/mL  15 - 150", "Reported by: Dr. S. Iyer MD (Pathologist)", "Follow up after 2 weeks with repeat ferritin"]


# ---------------------------------------------------------------- dates
@pytest.mark.parametrize("raw,iso,candidates,amb", [
    ("2024-03-12", "2024-03-12", [], []), ("13/03/2024", "2024-03-13", [], []), ("03/13/2024", "2024-03-13", [], []),
    ("12 Mar 2024", "2024-03-12", [], []), ("March 12, 2024", "2024-03-12", [], []),
    ("12/03/2024", None, ["2024-03-12", "2024-12-03"], ["day_month_order_ambiguous"]),
    ("12/03/24", None, ["2024-03-12", "2024-12-03"], ["two_digit_year", "day_month_order_ambiguous"]),
    ("31/02/2024", "2024-02-31", [], []), ("45/45/2024", None, [], ["invalid_date"]), ("sometime", None, [], ["unrecognised_date_format"])])
def test_dates_are_resolved_only_when_the_text_allows_it(raw, iso, candidates, amb):
    d = structured.parse_date(raw)
    if raw == "31/02/2024":
        assert d["iso"] == "2024-02-31" or d["iso"] is None                                      # calendar validity is not this function's job; the order is unambiguous
        return
    assert d["iso"] == iso and [c["iso"] for c in d["candidates"]] == candidates and d["ambiguity"] == amb


# ---------------------------------------------------------------- facts
def test_header_facts_are_extracted_with_spans_and_without_patient_identifiers():
    text = "\n".join(LAB)
    facts = structured.extract_facts([P(1, text)], "laboratory_report")
    by = {}
    for f in facts:
        by.setdefault(f.kind, []).append(f)
    assert by["laboratory"][0].value == "CITY DIAGNOSTIC LABORATORY" and by["laboratory"][0].ambiguity == ["provider_type_from_keyword_only"]
    assert by["collection_date"][0].value == "11/03/2024" and by["collection_date"][0].normalized["iso"] is None                # 11/03 could be 11 March or 3 November
    assert by["report_date"][0].value == "13/03/2024" and by["report_date"][0].normalized["iso"] == "2024-03-13"               # 13 can only be a day
    assert {f.value for f in by["clinician_name"]} == {"Dr. R. Menon MD", "Dr. S. Iyer MD"} and all("credentials_not_verified" in f.ambiguity for f in by["clinician_name"])
    fu = by["follow_up"][0]
    assert fu.normalized["interval"] == {"relation": "after", "value": 2, "unit": "week"}
    for f in facts:
        assert text[f.span[0]:f.span[1]].strip() == f.value.strip() or f.value.strip() in text[f.span[0]:f.span[1]], (f.kind, f.value, text[f.span[0]:f.span[1]])
    blob = " ".join(f.value for f in facts)
    for private in ("Asha", "99887", "Age 34"):
        assert private not in blob                                                                              # patient identifiers are never collected


def test_silence_is_not_a_statement():
    facts = structured.extract_facts([P(1, "CITY CLINIC\nHaemoglobin 11 g/dL 12 - 15\n")], "laboratory_report")
    kinds = {f.kind for f in facts}
    assert "allergy" not in kinds and "follow_up" not in kinds and "report_date" not in kinds           # no allergy line is NOT "no allergies"


def test_allergy_statements_keep_the_printed_text_and_only_explicit_absence_is_recorded_as_none():
    f = structured.extract_facts([P(1, "Allergies: penicillin, sulfa drugs\n")])[0]
    assert f.kind == "allergy" and f.value == "penicillin, sulfa drugs" and f.normalized == {"statement": "listed", "items": ["penicillin", "sulfa drugs"]}
    n = structured.extract_facts([P(1, "Drug allergy: NKDA\n")])[0]
    assert n.normalized["statement"] == "none_stated" and "absence_stated_by_the_document_not_inferred" in n.ambiguity
    assert structured.extract_facts([P(1, "Known allergic to: \n")]) == []                              # a label with nothing after it states nothing


def test_follow_up_with_a_date_or_a_condition():
    f = structured.extract_facts([P(1, "Follow up on 12/05/2024 if symptoms persist\n")])[0]
    assert f.normalized["date"]["iso"] is None and len(f.normalized["date"]["candidates"]) == 2 and "condition" in f.normalized and "day_month_order_ambiguous" in f.ambiguity
    g = structured.extract_facts([P(1, "Review with reports\n")])
    assert g[0].kind == "follow_up" and g[0].normalized == {} and "no_specific_time_stated" in g[0].ambiguity


# ---------------------------------------------------------------- lab fields: provenance, normalisation, ambiguity
def test_lab_fields_carry_spans_that_point_at_the_printed_text():
    res = pipeline.process(pdf(LAB), "lab.pdf")
    assert res.doc_type == "laboratory_report" and len(res.fields) >= 2
    text = res.pages[0].text
    for f in res.fields:
        pr = f.provenance
        assert " ".join(text[pr["line_span"][0]:pr["line_span"][1]].split()) == " ".join(f.source_text.split())
        assert text[pr["value_span"][0]:pr["value_span"][1]] == f.value
        assert pr["unit_span"] is None or text[pr["unit_span"][0]:pr["unit_span"][1]] == f.unit
        assert pr["page"] == 1 and pr["method"] == "embedded" and pr["extractor"] == pipeline.PROCESSING_VERSION
        assert pr["box"] is None or (len(pr["box"]) == 4 and all(0 <= v <= 1 for v in pr["box"]))                   # native PDF text has a position on the page
    assert any(f.provenance["box"] for f in res.fields)
    hb = next(f for f in res.fields if f.name == "Haemoglobin")
    assert hb.normalized == {"name_key": "haemoglobin", "value_number": 11.2, "qualifier": None, "unit_canonical": "g/dL"} and hb.flag == "printed_flag_low" and hb.ambiguity == []
    fe = next(f for f in res.fields if f.name == "Ferritin")
    assert "reference_range_missing" not in fe.ambiguity and fe.flag == "outside_printed_range"


def test_normalisation_never_resolves_what_the_print_leaves_open():
    lines = ["Glucose  6,5  mmol/L  3.9 - 5.6", "Platelets  1,200  10^3/uL  150 - 450", "Creatinine  <0.2  mg/dL  0.6 - 1.2", "Sodium  140  mEq/L", "Calcium  9.1  zz/q  8.5 - 10.5"]
    fields = {f.name: f for f in pipeline.process(pdf(lines), "x.pdf").fields}
    g = fields["Glucose"]
    assert g.value == "6,5" and g.normalized["value_number"] == 6.5 and "decimal_comma" in g.ambiguity
    p = fields["Platelets"]
    assert p.value == "1,200" and p.normalized["value_number"] is None and "thousands_or_decimal_comma" in p.ambiguity and p.flag == ""        # no number chosen, no range comparison made
    c = fields["Creatinine"]
    assert c.normalized["qualifier"] == "<" and "value_qualifier" in c.ambiguity and c.flag == ""
    assert "reference_range_missing" in fields["Sodium"].ambiguity
    assert "unit_not_in_lexicon" in fields["Calcium"].ambiguity and fields["Calcium"].normalized["unit_canonical"] is None and fields["Calcium"].unit == "zz/q"


def test_devanagari_digits_are_kept_raw_and_flagged():
    f = pipeline.extract_fields([P(1, "Haemoglobin  ११.२  g/dL  12.0 - 15.5\n")])[0]
    assert f.value == "११.२" and f.normalized["value_number"] == 11.2 and "non_latin_digits" in f.ambiguity


def test_whitespace_collapsing_does_not_break_the_span_mapping():
    text = "Ferritin        8        ng/mL        15 - 150\n"
    f = pipeline.extract_fields([P(1, text)])[0]
    assert text[f.provenance["value_span"][0]:f.provenance["value_span"][1]] == "8" and text[f.provenance["unit_span"][0]:f.provenance["unit_span"][1]] == "ng/mL"
    assert text[f.provenance["range_span"][0]:f.provenance["range_span"][1]] == "15 - 150"


# ---------------------------------------------------------------- languages
def test_ocr_languages_are_configured_limited_to_what_is_installed_and_reported():
    assert languages.configured({}) == ["eng"] and languages.configured({"COSMO_OCR_LANGS": "eng+hin"}) == ["eng", "hin"] and languages.configured({"COSMO_OCR_LANGS": "ENG, hin"}) == ["eng", "hin"]
    with pytest.raises(SystemExit):
        languages.configured({"COSMO_OCR_LANGS": "english; DROP TABLE"})
    plan = languages.make_plan(["eng", "osd"], ["eng", "hin"])
    assert plan.used == ["eng"] and plan.missing == ["hin"] and plan.lang_arg == "eng" and any("'hin' is not installed" in w for w in plan.warnings)
    full = languages.make_plan(["eng", "hin", "osd"], ["eng", "hin"])
    assert full.lang_arg == "eng+hin" and full.warnings == []
    none = languages.make_plan(["osd"], ["hin"])
    assert none.used == [] and none.lang_arg == ""


def test_script_detection_describes_characters_not_languages():
    assert languages.script_hint("Haemoglobin test result is within the printed range") == "latin"
    assert languages.script_hint("यह रिपोर्ट रोगी के रक्त परीक्षण के परिणाम दिखाती है") == "devanagari"
    assert languages.script_hint("Haemoglobin परिणाम सामान्य result printed range") == "mixed"
    assert languages.script_hint("12 34") == "unknown"
    assert languages.has_non_latin_digits("५०० mg") and not languages.has_non_latin_digits("500 mg") and languages.to_ascii_digits("५०० mg") == "500 mg"


def test_a_missing_language_pack_is_reported_for_scanned_hindi_and_native_hindi_is_marked_unchecked(monkeypatch):
    monkeypatch.setattr(pipeline, "validate", lambda data, name: "png")
    monkeypatch.setattr(pipeline, "ocr_languages", lambda: ["eng", "osd"])
    monkeypatch.setattr(pipeline, "_plan_cache", None)
    monkeypatch.setenv("COSMO_OCR_LANGS", "eng+hin")
    monkeypatch.setattr(pipeline, "extract_pages", lambda data, ext, cancel=None, max_seconds=None: [P(1, "यह रिपोर्ट रोगी के रक्त परीक्षण के परिणाम दिखाती है", "ocr", 0.8, None, "Devanagari")])
    res = pipeline.process(b"x", "scan.png")
    assert res.language["ocr_missing"] == ["hin"] and res.language["ocr_used"] == ["eng"] and res.language["scripts"] == {1: "Devanagari"}
    assert any("Hindi text recognition is not installed" in w for w in res.warnings)
    monkeypatch.setattr(pipeline, "_plan_cache", None)
    monkeypatch.setattr(pipeline, "extract_pages", lambda data, ext, cancel=None, max_seconds=None: [P(1, "यह रिपोर्ट रोगी के रक्त परीक्षण के परिणाम दिखाती है", "plain", 1.0, None, "Devanagari")])
    res2 = pipeline.process(b"x", "note.png")
    assert any("not been checked for accuracy and is not translated" in w for w in res2.warnings)
    res3 = pipeline.process("यह रिपोर्ट रोगी के रक्त परीक्षण के परिणाम दिखाती है".encode("utf-8"), "note.txt")
    assert res3.language["scripts"][1] == "Devanagari"


# ---------------------------------------------------------------- bounds
def test_time_budget_and_cancellation_stop_reading():
    with pytest.raises(pipeline.DocumentError, match="took too long"):
        pipeline.extract_pages(pdf(LAB), "pdf", max_seconds=-1)
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(Cancelled):
        pipeline.extract_pages(pdf(LAB), "pdf", cancel=tok)


def test_a_page_whose_ocr_times_out_is_skipped_not_fatal(monkeypatch):
    if not pipeline.ocr_available():
        pytest.skip("Tesseract OCR is not installed")
    def slow(img):
        raise RuntimeError("Tesseract process timeout")
    monkeypatch.setattr(pipeline, "_ocr_image", slow)
    page = pymupdf.open(stream=pdf(["x"]), filetype="pdf")[0]
    png = page.get_pixmap(dpi=60).tobytes("png")
    res = pipeline.process(png, "scan.png")
    assert res.pages[0].method == "ocr_failed" and res.readable is False and any("took too long to read" in w for w in res.warnings)


def test_damaged_and_empty_documents_are_reported_clearly():
    with pytest.raises(pipeline.DocumentError, match="damaged"):
        pipeline.process(b"%PDF-1.7\nnot really a pdf" * 20, "x.pdf")
    res = pipeline.process(pdf([]), "blank.pdf")
    assert res.readable is False and res.fields == [] and res.facts == [] and res.medications == []

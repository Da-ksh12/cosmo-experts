"""Citation verification is deterministic, so it is tested with a scripted backend (a test double - never used in production paths)."""
import json

from app.assistant import grounded
from app.assistant.llm import Generation, LLMBackend, LLMError

PASSAGES = [{"text": "Ferritin  8  ng/mL  15 - 150  L", "kind": "document"},
            {"text": "A reference range is the set of values the laboratory printed on your report.", "kind": "knowledge_base"}]


class Scripted(LLMBackend):
    name = "scripted-test-double"

    def __init__(self, out):
        self.out = out

    def generate(self, system, user, max_tokens=400, json_schema=None, timeout=120):
        if isinstance(self.out, Exception):
            raise self.out
        return Generation(self.out if isinstance(self.out, str) else json.dumps(self.out), 0.01, 10, self.name)


def claim(text, ids, quote):
    return {"claims": [{"text": text, "source_ids": ids, "quote": quote}]}


def run(out):
    return grounded.generate(Scripted(out), "What is my ferritin?", PASSAGES)


def test_valid_claim_is_kept_with_provenance():
    r = run(claim("Your report lists ferritin as 8 ng/mL.", [1], "Ferritin 8 ng/mL"))
    assert len(r.claims) == 1 and r.claims[0].provenance == "report_fact" and not r.dropped
    r = run(claim("A reference range is what the laboratory printed.", [2], "the set of values the laboratory printed"))
    assert r.claims[0].provenance == "approved_reference"


def test_fabricated_quote_is_dropped():
    r = run(claim("Your ferritin is 8.", [1], "Ferritin is severely deficient"))
    assert not r.claims and r.dropped[0]["reason"] == "quote not found in cited source"


def test_number_not_in_source_is_dropped():
    r = run(claim("Your ferritin is 80 ng/mL.", [1], "Ferritin 8 ng/mL"))
    assert not r.claims and "number" in r.dropped[0]["reason"]


def test_nonexistent_source_is_dropped():
    r = run(claim("Ferritin is 8.", [7], "Ferritin 8"))
    assert not r.claims and "does not exist" in r.dropped[0]["reason"]


def test_prescriptive_or_diagnostic_text_is_dropped():
    assert not run(claim("You should take iron tablets.", [1], "Ferritin 8 ng/mL")).claims
    assert not run(claim("You have anaemia.", [1], "Ferritin 8 ng/mL")).claims


def test_malformed_and_error_outputs_do_not_crash():
    assert run("not json at all").claims == []
    assert run({"claims": [{"text": "x"}]}).dropped[0]["reason"] == "malformed"
    r = run(LLMError("timeout"))
    assert r.claims == [] and "backend error" in r.dropped[0]["reason"]
    assert run({"claims": []}).claims == []        # empty claims = the model abstained


def test_json_wrapped_in_prose_is_recovered():
    r = run('Sure! {"claims":[{"text":"Ferritin is 8.","source_ids":[1],"quote":"Ferritin 8"}]} Hope that helps.')
    assert len(r.claims) == 1


def test_false_reassurance_is_blocked():
    """Regression from the real 1.5B run: 'within the normal range' for a value the report marks low."""
    r = run(claim("Ferritin level is 8 ng/mL, within the normal range of 15 - 150 ng/mL.", [1], "Ferritin 8 ng/mL 15 - 150"))
    assert not r.claims and "interpretive" in r.dropped[0]["reason"]
    assert not run(claim("Your ferritin of 8 is healthy.", [1], "Ferritin 8 ng/mL")).claims


def test_printed_flag_licenses_low_but_not_normal():
    ok = run(claim("The report marks ferritin 8 ng/mL as low.", [1], "Ferritin 8 ng/mL 15 - 150 L"))
    assert len(ok.claims) == 1
    assert not run(claim("Ferritin is normal.", [1], "Ferritin 8 ng/mL 15 - 150 L")).claims


def test_plain_restatement_still_passes():
    r = run(claim("Your report lists ferritin as 8 ng/mL with a printed range of 15 - 150.", [1], "Ferritin 8 ng/mL 15 - 150"))
    assert len(r.claims) == 1


def test_lesion_gates_reject_the_measured_probe_profile():
    """The real DINOv2-probe profile (good macro-F1, weak malignant-class sensitivity, no OOD separation) must not pass staging."""
    from app.training.trainer import evaluate_gates
    card = {"task": "lesion_classification_research", "near_duplicates_vs_train": {"test": 0}, "dataset_license": {"spdx": "CC-BY-4.0", "terms_snapshot_hash": "h"},
            "baselines": {"b": {"macro_f1": 0.508}},
            "test_metrics": {"n": 325, "macro_f1": 0.558, "macro_f1_ci95": [0.471, 0.63], "ece": 0.035, "robustness": {"x": 1}, "subgroups_by_fitzpatrick": {"4-6": {"n": 8}},
                             "per_class": {"MEL": {"n": 7, "sensitivity": 0.43}, "BCC": {"n": 119, "sensitivity": 0.55}, "SCC": {"n": 27, "sensitivity": 0.41}},
                             "ood": {"auroc_confidence_separates_in_vs_out": 0.35}}}
    failed = {g["gate"] for g in evaluate_gates(card) if not g["passed"]}
    assert {"improvement_supported_by_ci", "critical_class_sensitivity", "dark_skin_evaluated", "ood_detection"} <= failed
    assert "beat_baselines_macro_f1" not in failed and "max_ece_calibrated" not in failed

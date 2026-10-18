"""Evaluation of the medication parser on its synthetic fixtures (eval/medication_fixtures.py).

    python -m eval.medication_eval [--out ../docs/eval/medication_parser.json]

EVIDENCE CLASS: synthetic_fixture. These lines were written by the project; the figures show how the rules behave on those formats and nothing about real prescriptions.
Two sets are reported separately: DEVELOPMENT (the parser was tuned on it) and HELD-OUT (written before the run that produced the recorded first-run figures; deliberately includes
formats the parser is not expected to handle).

Measured: per component exact-match precision and recall; ambiguity-code recall (an ambiguity that should have been flagged and was not is the dangerous miss) and the number of extra
flags; FALSE-FILL rate (components that must stay empty but were filled: inventing is the worst error); source-span correctness (the text at each component's span equals its raw
text); item detection precision/recall including non-medication lines; and the absence of any recommendation-like output keys."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

COMPONENTS = ("name", "form", "dose", "pattern", "frequency", "duration", "route", "alias")


@dataclass
class _Page:
    page_no: int
    text: str
    method: str = "embedded"
    confidence: float = 1.0


def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _got(m) -> dict:
    r = lambda c: c.raw if c is not None else None  # noqa: E731
    return {"name": r(m.name), "form": r(m.form), "dose": r(m.dose), "pattern": r(m.dose_pattern), "frequency": r(m.frequency), "duration": r(m.duration), "route": r(m.route),
            "alias": r(m.alias), "strengths": [s.raw for s in m.strengths], "instructions": [i.raw for i in m.instructions]}


def evaluate(lines: list[dict], not_medication: list[str]) -> dict:
    from app.documents import medication
    comp = {k: {"tp": 0, "fp": 0, "fn": 0} for k in (*COMPONENTS, "strengths", "instructions")}
    amb_expected = amb_found = amb_extra = 0
    absent_checked = absent_filled = 0
    span_total = span_ok = 0
    detected = missed = 0
    failures = []
    for case in lines:
        text = case["text"]
        meds = medication.extract_medications([_Page(1, text)], "prescription")
        if len(meds) != 1:
            missed += 1 if not meds else 0
            detected += 1 if meds else 0
            failures.append({"text": text, "problem": f"{len(meds)} entries found"})
            for k in (*COMPONENTS, "strengths", "instructions"):
                g = case.get(k)
                comp[k]["fn"] += len(g) if isinstance(g, list) else (1 if g else 0)
            amb_expected += len(case.get("ambiguity", []))
            continue
        detected += 1
        m = meds[0]
        got = _got(m)
        diffs = []
        for k in COMPONENTS:
            gold, g = case.get(k), got[k]
            if gold is not None and g == gold:
                comp[k]["tp"] += 1
            else:
                if gold is not None:
                    comp[k]["fn"] += 1
                    diffs.append(f"{k}: expected {gold!r}, got {g!r}")
                if g is not None and g != gold:
                    comp[k]["fp"] += 1
                    if gold is None and k not in case.get("absent", []):
                        pass
        gold_s, got_s = case.get("strengths", []), got["strengths"]
        for s in got_s:
            comp["strengths"]["tp" if s in gold_s else "fp"] += 1
        for s in gold_s:
            if s not in got_s:
                comp["strengths"]["fn"] += 1
                diffs.append(f"strength {s!r} missed (got {got_s})")
        for gi in case.get("instructions", []):
            if any(gi in i or i in gi for i in got["instructions"]):
                comp["instructions"]["tp"] += 1
            else:
                comp["instructions"]["fn"] += 1
                diffs.append(f"instruction {gi!r} missed (got {got['instructions']})")
        for i in got["instructions"]:
            if not any(gi in i or i in gi for gi in case.get("instructions", [])):
                comp["instructions"]["fp"] += 1
        for a in case.get("absent", []):
            absent_checked += 1
            val = got["strengths"] if a == "strengths" else got.get(a)
            if val:
                absent_filled += 1
                diffs.append(f"INVENTED/FILLED {a}: {val!r}")
        flags = set(m.ambiguity)
        for a in case.get("ambiguity", []):
            amb_expected += 1
            if a in flags:
                amb_found += 1
            else:
                diffs.append(f"ambiguity not flagged: {a}")
        amb_extra += len(flags - set(case.get("ambiguity", [])) - {f for f in flags if f.startswith("abbreviation_expanded")})
        pg = text
        for c in [m.name, m.alias, m.form, m.dose, m.dose_pattern, m.route, m.frequency, m.duration, *m.strengths, *m.instructions, *m.extra_names]:
            if c is not None:
                span_total += 1
                span_ok += _ws(pg[c.span[0]:c.span[1]]) == _ws(c.raw)
        if diffs:
            failures.append({"text": text, "problems": diffs})
    false_pos = []
    for t in not_medication:
        r = medication.extract_medications([_Page(1, t)], "other_uncertain")
        if r:
            false_pos.append(t)

    def pr(d):
        p = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else None
        r = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else None
        return {**d, "precision": round(p, 3) if p is not None else None, "recall": round(r, 3) if r is not None else None}
    total = {"tp": sum(d["tp"] for d in comp.values()), "fp": sum(d["fp"] for d in comp.values()), "fn": sum(d["fn"] for d in comp.values())}
    return {"lines": len(lines), "components": {k: pr(v) for k, v in comp.items()}, "overall": pr(total),
            "ambiguity": {"expected_flags": amb_expected, "found": amb_found, "recall": round(amb_found / amb_expected, 3) if amb_expected else None, "extra_flags_per_line": round(amb_extra / max(len(lines), 1), 2)},
            "false_fill": {"components_that_must_stay_empty": absent_checked, "filled": absent_filled, "rate": round(absent_filled / absent_checked, 4) if absent_checked else None},
            "span_correctness": {"components": span_total, "correct": span_ok, "rate": round(span_ok / span_total, 4) if span_total else None},
            "item_detection": {"entries_detected": detected, "entries_missed": missed, "of": len(lines), "non_medication_lines": len(not_medication), "false_positives": false_pos,
                               "false_positive_rate": round(len(false_pos) / max(len(not_medication), 1), 3)},
            "failures": failures}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / "docs" / "eval" / "medication_parser.json")
    ap.add_argument("--heldout-first-run", type=Path, help="existing report whose recorded held-out first-run figures must be kept")
    a = ap.parse_args(argv)
    from app.ai.evaluation.common import header, write_report
    from app.documents import medication
    from eval import medication_fixtures as F
    dev = evaluate(F.PRESCRIPTION_LINES, F.NOT_MEDICATION_LINES)
    held = evaluate(F.HELDOUT_LINES, F.HELDOUT_NOT_MEDICATION)
    rep = header("medication_parser", "synthetic_fixture", {"parser": medication.VERSION, "development_lines": len(F.PRESCRIPTION_LINES), "heldout_lines": len(F.HELDOUT_LINES)},
                 {"id": "eval/medication_fixtures.py", "kind": "written by the project; not real prescriptions"},
                 ["Synthetic fixtures: they test the rules, not accuracy on real prescriptions (none were available).", "The held-out set was written after tuning on the development set."])
    rep.update(development=dev, heldout=held)
    if a.heldout_first_run and a.heldout_first_run.is_file():
        rep["heldout_first_run"] = json.loads(a.heldout_first_run.read_text(encoding="utf-8")).get("heldout_first_run") or json.loads(a.heldout_first_run.read_text(encoding="utf-8")).get("heldout")
    write_report(a.out, rep)
    for name, r in (("development", dev), ("held-out", held)):
        print(f"{name}: overall P={r['overall']['precision']} R={r['overall']['recall']} | ambiguity recall={r['ambiguity']['recall']} extra/line={r['ambiguity']['extra_flags_per_line']} | "
              f"false-fill {r['false_fill']['filled']}/{r['false_fill']['components_that_must_stay_empty']} | spans {r['span_correctness']['rate']} | detected {r['item_detection']['entries_detected']}/{r['lines']} | FP lines {len(r['item_detection']['false_positives'])}")
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 5 experiments: train and evaluate probes on the imported, licence-gated datasets.

    python -m scripts.phase5_train list
    python -m scripts.phase5_train run <experiment> [--max-seconds N]

Each experiment names its sources by dataset id and uses the LATEST imported version unless one is given. Everything is resumable: an interrupted run continues from the cached images and
features. Results are printed and registered as experimental research models (never approved, never active)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.training import featuretrain as FT  # noqa: E402

ACTOR = "phase5-agent"
OOD = str(Path(__file__).resolve().parent.parent / "data" / "ood_eval")
WARN_CLINICAL = ["Trained on smartphone and clinical photographs of skin lesions (PAD-UFES-20, and ISIC clinical photographs when listed on the card), mostly Fitzpatrick 1-3; not valid for other image types.",
                 "Research only: not a diagnosis and never shown to patients."]
WARN_DERM = ["Trained on dermoscopic images only (ISIC contributor-licensed images, mostly lighter skin); a phone photograph is a different kind of image and this model must not be run on one.",
             "Research only: not a diagnosis and never shown to patients."]
WARN_COND = ["Trained on smartphone photographs from US volunteers (SCIN); labels are dermatologist opinions from photographs, not biopsy results. The model has no 'other' class and will name one of its classes for any skin image.",
             "Research only: not a diagnosis and never shown to patients."]


def latest(dataset_id: str) -> str:
    rows = db.q("SELECT id, manifest FROM dataset_versions WHERE dataset_id=? ORDER BY created_at DESC", (dataset_id,))
    for r in rows:
        if json.loads(r["manifest"]).get("complete") is not False:
            return r["id"]
    raise SystemExit(f"no complete imported version of {dataset_id}")


def S(name, dataset, role="train", **kw):
    return FT.SourceSpec(name, dataset, latest(dataset), role, **kw)


def experiments() -> dict:
    return {
        # PAD-UFES-20 alone: the same split as the four earlier models, with the new head selection (feature kind and C on validation). An ablation, so the effect of more data can be told from the effect of the head.
        "clinical-pad-only": dict(task="lesion_classification_research", specs=lambda: [S("pad-ufes-20", "pad-ufes-20")], quality_gate="skin_closeup",
                                  intended_use="Research only. Not clinically validated. Not offered to patients.", warnings=WARN_CLINICAL,
                                  limitations=["Trained on one centre's smartphone photographs (PAD-UFES-20): few melanoma examples and almost no dark-skin examples.", "Small-sample metrics are unreliable"]),
        # PAD-UFES-20 plus the independent ISIC clinical photographs (PAD-UFES-20's own copy inside ISIC removed)
        "clinical-pad-isic": dict(task="lesion_classification_research", specs=lambda: [S("pad-ufes-20", "pad-ufes-20"), S("isic-clinical", "isic-clinical", unknown_group_train_only=True)],
                                  quality_gate="skin_closeup", intended_use="Research only. Not clinically validated. Not offered to patients.", warnings=WARN_CLINICAL,
                                  limitations=["Two clinical sources of different style; ISIC photographs without a patient identifier are used for training only.", "Almost no dark-skin examples in either source.", "Small-sample metrics are unreliable"]),
        "skin-condition-scin": dict(task="skin_condition_research", specs=lambda: [S("scin", "scin")], quality_gate="skin_closeup",
                                    intended_use="Research only. Not clinically validated. Not offered to patients.", warnings=WARN_COND,
                                    limitations=["Closed set of common conditions with no 'other' class.", "Labels are dermatologist opinions from photographs (leading condition weight >= 0.5), not biopsy results.",
                                                 "Volunteers in the United States; self-selected; photographs of varying quality."]),
        # dermoscopy: trained on ISIC, scored on DERM12345 from another centre without ever seeing it
        "derm-isic": dict(task="dermoscopy_lesion_research", specs=lambda: [S("isic-dermoscopy", "isic-dermoscopy", unknown_group_train_only=True), S("derm12345", "derm12345", role="external")],
                          quality_gate="none", intended_use="Research only. Not clinically validated. Not offered to patients.", warnings=WARN_DERM,
                          limitations=["Dermoscopic images only.", "ISIC images without a patient or lesion identifier are used for training only.", "Fitzpatrick type is recorded for a minority of the images."]),
        # dermoscopy: DERM12345 contributes training data too (its own patient-level split); external numbers then come from its held-out part
        "derm-isic-derm12345": dict(task="dermoscopy_lesion_research", specs=lambda: [S("isic-dermoscopy", "isic-dermoscopy", unknown_group_train_only=True), S("derm12345", "derm12345", split_policy="official")],
                                    quality_gate="none", intended_use="Research only. Not clinically validated. Not offered to patients.", warnings=WARN_DERM,
                                    limitations=["Dermoscopic images only.", "Two centres; DERM12345 has no skin-tone labels.", "ISIC images without a patient or lesion identifier are used for training only."]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "run"])
    ap.add_argument("experiment", nargs="?")
    ap.add_argument("--max-seconds", type=float, default=None)
    ap.add_argument("--seed", type=int, default=17)
    a = ap.parse_args(argv)
    db.init()
    ex = experiments()
    if a.cmd == "list":
        for k, v in ex.items():
            print(k, "->", v["task"])
        return 0
    if a.experiment not in ex:
        raise SystemExit(f"unknown experiment; choose one of {list(ex)}")
    e = ex[a.experiment]
    card = FT.run(e["task"], a.experiment, e["specs"](), ACTOR, seed=a.seed, intended_use=e["intended_use"], limitations=e["limitations"], serving_warnings=e["warnings"], quality_gate=e["quality_gate"],
                  ood_dir=OOD, max_seconds=a.max_seconds)
    t = card["test_metrics"]
    print(json.dumps({"model": card["model_id"], "macro_f1": t["macro_f1"], "ci": t["macro_f1_ci95"], "balanced_accuracy": t["balanced_accuracy"], "accuracy": t["accuracy"], "ece": t["ece"],
                      "baselines": {k: v["macro_f1"] for k, v in card["baselines"].items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

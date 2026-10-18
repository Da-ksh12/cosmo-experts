"""Evaluate REGISTERED image models on their own held-out test split and compare them.

The split is rebuilt exactly as it was at training time (same function, same seed, same dataset version) and checked against the hash recorded on the model card; if it cannot be
reproduced the evaluation refuses rather than evaluating on a different set. Artifacts are checksum-verified by the serving loader before they are used. Predictions are made on the
CPU in small batches with a cancellation check between batches, so the evaluation is safe on a low-memory host.

What is recomputed: accuracy, macro-F1, balanced accuracy, per-class sensitivity and specificity with intervals, the confusion matrix, calibration, selective prediction, out-of-distribution
behaviour (administrator-supplied non-lesion images), subgroups from the dataset's published metadata (Fitzpatrick type, sex, age band, body region), paired comparison between models, and the
UNCHANGED release gates (`trainer.GATES`). What is copied from the model card and labelled as such: the robustness table (it needs corrupted-image runs). Nothing is tuned and no threshold is changed."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from ... import db
from ..errors import Cancelled
from ..resources import CancelToken
from . import vision
from .common import header

AGE_BANDS = ((0, 30, "<30"), (30, 50, "30-49"), (50, 65, "50-64"), (65, 200, "65+"))


def _bucket_fitz(v) -> str:
    v = str(v or "")
    return "NA" if v in ("", "None", "nan", "NA") else "1-2" if v in ("1", "1.0", "2", "2.0") else "3" if v in ("3", "3.0") else "4-6"


def _age_band(v) -> str:
    try:
        a = float(v)
    except (TypeError, ValueError):
        return "NA"
    return next((lab for lo, hi, lab in AGE_BANDS if lo <= a < hi), "NA")


def load_metadata(csv_path: Path | None, root: str) -> dict[str, dict]:
    """The dataset's own published metadata keyed by image file name (PAD-UFES-20: raw/metadata.csv, column img_id). Empty if it is not present."""
    candidates = [csv_path] if csv_path else [Path(root).parent / "raw" / "metadata.csv"]
    for p in candidates:
        if p and Path(p).is_file():
            with open(p, newline="", encoding="utf-8") as f:
                return {r["img_id"]: r for r in csv.DictReader(f) if r.get("img_id")}
    return {}


def _split_and_cache(card: dict, split: str, limit: int | None):
    from ...training import trainer as T
    row = db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (card["dataset_version_id"],))
    if not row:
        raise ValueError("The dataset version recorded on the model card is no longer registered.")
    mf = json.loads(row["manifest"])
    entries = [e for e in mf["entries"] if e.get("label")]
    splits = T.stratified_group_split(entries, card["seed"])
    sizes = {k: len(v) for k, v in splits.items()}
    if sizes != card["split_sizes"]:
        raise ValueError(f"The split cannot be reproduced (card {card['split_sizes']}, rebuilt {sizes}); refusing to evaluate on a different set.")
    split_hash = hashlib.sha256(json.dumps({k: [e["sha256"] for e in v] for k, v in splits.items()}).encode()).hexdigest()
    if split_hash != card["split_hash"]:
        raise ValueError("The rebuilt split does not match the hash recorded on the model card; refusing to evaluate.")
    ents = splits[split][:limit] if limit else splits[split]
    X = T.build_cache(mf["root"], ents, card["image_size"])
    return mf, ents, X, split_hash


def predict_probabilities(model_id: str, X, *, batch: int = 16, cancel: CancelToken | None = None, progress=None) -> np.ndarray:
    from ...training import serving, trainer as T
    b = serving._load(model_id)                                           # checksum-verified; one model resident at a time
    out = []
    for i in range(0, len(X), batch):
        if cancel:
            cancel.raise_if_cancelled()
        arr = np.ascontiguousarray(X[i:i + batch])
        if b["kind"] == "probe":
            from ...training import probe
            feat = probe.extract(b["backbone"], arr, "cpu")
            logits = probe.head_logits((feat - b["mean"]) / b["scale"], b["coef"], b["intercept"])
        else:
            logits = T.predict_logits(b["model"], arr, "cpu", batch)
        out.append(logits)
        if progress:
            progress(min(i + batch, len(X)), len(X))
    return T.softmax(np.concatenate(out), b["temperature"])


def _ood_probabilities(model_id: str, card: dict, ood_dir: Path, batch: int) -> tuple[np.ndarray | None, str]:
    from PIL import Image
    from ...training import trainer as T
    files = sorted(p for p in ood_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")) if ood_dir.is_dir() else []
    if not files:
        return None, ""
    arr = np.stack([np.asarray(T._square_resize(Image.open(p), card["image_size"])) for p in files])
    return predict_probabilities(model_id, arr, batch=batch), f"{len(files)} non-lesion natural images supplied by the administrator ({ood_dir.name})"


def _gate_card(card: dict, rep: dict, ece10: float) -> dict:
    """The model card as the release gates read it, with recomputed figures in place of the stored ones (robustness is the stored table)."""
    fitz = rep.get("subgroups", {}).get("fitzpatrick", {}).get("levels", {})
    sub = {}
    for lv, r in fitz.items():
        sub[lv] = {"n": r["n"], "reliable": r["reliable"]}
    pc = {c: {"n": m["n"], "sensitivity": m["sensitivity"]} for c, m in rep["per_class"].items()}
    tm = {"n": rep["n"], "macro_f1": rep["macro_f1"], "macro_f1_ci95": rep["macro_f1_ci95"], "ece": ece10, "per_class": pc, "subgroups_by_fitzpatrick": sub,
          "robustness": card["test_metrics"].get("robustness"), "ood": {"auroc_confidence_separates_in_vs_out": rep.get("ood", {}).get("auroc_confidence_separates_in_vs_out")} if rep.get("ood") else {}}
    return dict(card, test_metrics=tm)


def evaluate_models(model_ids: list[str], *, split: str = "test", limit: int | None = None, batch: int = 16, ood_dir: Path | None = None, metadata_csv: Path | None = None,
                    n_boot: int = 1000, cancel: CancelToken | None = None, progress=None) -> dict:
    from ...training import serving, trainer as T
    if split not in ("test", "val"):
        raise ValueError("Only the validation and test splits may be evaluated (never train).")
    reports, probs, preds, hashes = {}, {}, {}, {}
    y_ref = None
    with db.context.as_system():
        for mid in model_ids:
            card, status = serving._card(mid)
            mf, ents, X, split_hash = _split_and_cache(card, split, limit)
            classes = card["classes"]
            c2i = {c: i for i, c in enumerate(classes)}
            y = np.array([c2i[e["label"]] for e in ents])
            P = predict_probabilities(mid, X, batch=batch, cancel=cancel, progress=progress)
            meta = load_metadata(metadata_csv, mf["root"])
            rows = [meta.get(Path(e["file"]).name, {}) for e in ents]
            groups = {"fitzpatrick": [_bucket_fitz(e.get("fitzpatrick")) for e in ents]}
            if meta:
                groups.update({"sex": [r.get("gender") or "NA" for r in rows], "age_band": [_age_band(r.get("age")) for r in rows], "body_region": [r.get("region") or "NA" for r in rows]})
            crit = T.TASK_SAFETY.get(card["task"], {}).get("critical_classes")
            P_ood, ood_src = (None, "")
            od = ood_dir if ood_dir is not None else (Path(T.DATA_DIR) / "ood_eval" if hasattr(T, "DATA_DIR") else None)
            if od is not None and od.is_dir():
                P_ood, ood_src = _ood_probabilities(mid, card, od, batch)
            rep = vision.evaluate(y, P, classes, groups=groups, critical=crit, P_ood=P_ood, ood_source=ood_src, n_boot=n_boot)
            ece10 = round(float(T.ece(P, y)), 3)
            gates_recomputed = T.evaluate_gates(_gate_card(card, rep, ece10))
            gates_stored = T.evaluate_gates(card)
            stored = card["test_metrics"]
            consistent = {"split": split, "accuracy_matches_card": abs(rep["accuracy"] - stored["accuracy"]) <= 0.002, "macro_f1_matches_card": abs(rep["macro_f1"] - stored["macro_f1"]) <= 0.002,
                          "confusion_matrix_matches_card": rep["confusion_matrix"] == stored.get("confusion_matrix")} if split == "test" and not limit else {"checked": False, "why": "only checked for the full test split"}
            base = max(card.get("baselines", {}).items(), key=lambda kv: kv[1].get("macro_f1", 0), default=None)
            lo = (rep["macro_f1_ci95"] or [0, 0])[0]
            rep.update({
                "model": {"model_id": mid, "task": card["task"], "architecture": card["architecture"], "artifact_sha256": card["artifact_sha256"], "registry_status": status,
                          "code_revision_at_training": card.get("code_revision"), "training_seed": card["seed"], "temperature": stored["temperature"]},
                "dataset": {"id": card["dataset_id"], "version": card["dataset_version"], "version_id": card["dataset_version_id"], "license": card.get("dataset_license", {}),
                            "split": split, "split_hash": split_hash, "images": len(ents), "patients": len({e["group_id"] for e in ents})},
                "ece_10_bin": ece10, "reproduces_model_card": consistent,
                "baseline_comparison": ({"baseline": base[0], "baseline_macro_f1": base[1].get("macro_f1"), "candidate_macro_f1": rep["macro_f1"], "candidate_macro_f1_ci95": rep["macro_f1_ci95"],
                                         "ci_lower_bound_exceeds_baseline": bool(lo > base[1].get("macro_f1", 0)), "baseline_source": "stored on the model card (per-image predictions of the baseline are not kept)"} if base else None),
                "release_gates": {"recomputed": gates_recomputed, "stored_card": gates_stored, "passed": all(g["passed"] for g in gates_recomputed),
                                  "agree": [g["passed"] for g in gates_recomputed] == [g["passed"] for g in gates_stored],
                                  "note": "Gates are read unchanged from trainer.GATES. A failed gate is a finding, not something to tune away. Approval additionally needs the five release requirements."},
                "robustness_from_card": card["test_metrics"].get("robustness"), "robustness_source": "model card (corrupted-image runs are not repeated here)"})
            reports[mid], probs[mid], hashes[mid] = rep, P, split_hash
            y_ref = y if y_ref is None else y_ref
            preds[mid] = (y, P.argmax(1), len(classes))
    paired = {}
    ids = list(reports)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if hashes[a] == hashes[b] and len(preds[a][0]) == len(preds[b][0]) and (preds[a][0] == preds[b][0]).all():
                y, pa, k = preds[a]
                paired[f"{a[:8]} minus {b[:8]}"] = {m: vision.paired_difference(y, pa, preds[b][1], k, m, n_boot=n_boot) for m in ("macro_f1", "balanced_accuracy")}
    first = reports[ids[0]] if ids else {}
    out = header("vision_model_evaluation", "public_research_dataset",
                 {"models": model_ids, "split": split, "limit": limit, "batch": batch, "n_boot": n_boot, "device": "cpu", "ood_dir": str(ood_dir) if ood_dir else None},
                 first.get("dataset"), ["Research models only; all of them are registered as research_only and none is offered to patients.",
                                        "Public research dataset of smartphone lesion photographs (mostly Fitzpatrick 1-3): not evidence of performance on any other population or image type."])
    out.update(reports=reports, paired_comparisons=paired)
    return out

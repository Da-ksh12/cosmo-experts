"""Does SERVING reproduce the EVALUATION? Runs a registered model through serving.predict on the raw held-out test images (PNG files, not the
training cache) and compares accuracy / macro-F1 with the numbers on the model card. Also reports how many real images the input-quality gate rejects.

Usage: python -m eval.serving_consistency <model_id> [--dataset-version <id>]"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.training import serving, trainer as T  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("model_id")
a = ap.parse_args()
card = json.loads(db.q1("SELECT data FROM model_versions WHERE id=?", (a.model_id,))["data"])
mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (card["dataset_version_id"],))["manifest"])
entries = [e for e in mf["entries"] if e.get("label")]
test = T.stratified_group_split(entries, card["seed"])["test"]
classes = card["classes"]
y, pred_all, pred_ok, y_ok, rejected = [], [], [], [], 0
for i, e in enumerate(test):
    data = (Path(mf["root"]) / e["file"]).read_bytes()
    forced = serving.predict(a.model_id, data, enforce_quality=False)          # model equivalence, no input gate
    gated = serving.predict(a.model_id, data, enforce_quality=True)            # what a user would actually get
    y.append(classes.index(e["label"])); pred_all.append(classes.index(forced["top"][0]["label"]))
    if gated["status"] == "rejected_input":
        rejected += 1
    if i % 50 == 0:
        print(i, "/", len(test), flush=True)
y, pred = np.array(y), np.array(pred_all)
k = len(classes)
got = {"n": len(y), "accuracy": round(float((pred == y).mean()), 3), "macro_f1": round(T.macro_f1(y, pred, k), 3), "balanced_accuracy": round(T.balanced_acc(y, pred, k), 3)}
exp = {k_: card["test_metrics"][k_] for k_ in ("n", "accuracy", "macro_f1", "balanced_accuracy")}
res = {"model": a.model_id, "served_from_raw_files": got, "model_card_evaluation": exp, "identical": got == exp,
       "images_rejected_by_input_quality_gate": f"{rejected}/{len(test)}"}
print(json.dumps(res, indent=1))
Path(__file__).resolve().parent.parent.parent.joinpath("docs/eval/serving_consistency.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

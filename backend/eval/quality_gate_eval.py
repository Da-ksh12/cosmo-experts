"""End-to-end evaluation of the FINAL input-quality gate (app.imaging.suitability.quality_issues, profile skin_closeup) on the VALIDATION split of PAD-UFES-20.

The validation split was not used to design or fit the v1.1 rules (features were selected with train/test numbers, thresholds fit on train), so this is the
independent check of the shipped gate. Usable = real dataset photos; unusable = simulated degradations of the same photos (limits as in quality_features.py).

    python -m eval.quality_gate_eval --out ../docs/eval
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.imaging import suitability as S  # noqa: E402
from app.training import trainer as T  # noqa: E402
from eval.quality_calibration import degrade as degrade_old  # noqa: E402
from eval.quality_features import KINDS, degrade as degrade_new  # noqa: E402


def issues(img, profile="skin_closeup"):
    m = S.quality_metrics(img)
    return S.quality_issues(m, profile), m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="124f81d4a9594958896d16e61944cd4d")
    ap.add_argument("--n", type=int, default=240)
    ap.add_argument("--out", default="../docs/eval")
    a = ap.parse_args()
    card = json.loads(db.q1("SELECT data FROM model_versions WHERE id=?", (a.model,))["data"])
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (card["dataset_version_id"],))["manifest"])
    sp = T.stratified_group_split([e for e in mf["entries"] if e.get("label")], card["seed"])
    rng = random.Random(7)
    pick = rng.sample(sp["val"], min(a.n, len(sp["val"])))
    exposure = ["blur_strong", "dark", "bright", "washed"]
    rows_good, rows_bad = [], {k: [] for k in KINDS + exposure}
    warn_good, warn_bad = [], {k: [] for k in KINDS}
    for e in pick:
        img = S.decode((Path(mf["root"]) / e["file"]).read_bytes())
        if min(img.shape[:2]) < 300:
            continue
        i, m = issues(img); rows_good.append(i); warn_good.append(m["blockiness"] >= S.BLOCKINESS_WARN)
        old_i, _ = issues(img, "skin_closeup")
        for k in KINDS:
            i, m = issues(degrade_new(img, k)); rows_bad[k].append(i); warn_bad[k].append(m["blockiness"] >= S.BLOCKINESS_WARN)
        for k in exposure:
            rows_bad[k].append(issues(degrade_old(img, k, rng))[0])
    n = len(rows_good)
    out = {"split": "validation (not used to design or fit the v1.1 rules)", "n_usable": n, "suitability_version": S.VERSION, "thresholds": S.QUALITY_PROFILES["skin_closeup"],
           "blockiness_warn": S.BLOCKINESS_WARN,
           "false_reject_usable": round(float(np.mean([bool(r) for r in rows_good])), 3),
           "false_reject_by_reason": {r: round(float(np.mean([r in x for x in rows_good])), 3) for r in sorted({r for x in rows_good for r in x})},
           "detection": {k: round(float(np.mean([bool(r) for r in v])), 3) for k, v in rows_bad.items()},
           "compression_warning": {"on_usable_photos": round(float(np.mean(warn_good)), 3), **{k: round(float(np.mean(v)), 3) for k, v in warn_bad.items() if k.startswith("jpeg")}},
           "limits": ["usable = accepted into a research dataset, not labelled for usability", "degradations are simulated", "lesion close-ups only: faces and hair/scalp are not validated"]}
    # hair/scalp profile keeps the v1.0 behaviour on direction: confirm it does not use the new rule
    out["hair_profile_uses_direction_rule"] = S.QUALITY_PROFILES["hair_closeup"]["min_direction_ratio"] is not None
    p = Path(a.out); p.mkdir(parents=True, exist_ok=True)
    (p / "quality_gate_eval.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

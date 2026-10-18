"""Calibrate the input-quality gate for skin close-up photographs using REAL images (PAD-UFES-20, licence-verified) as the 'usable' class and
synthetic degradations of those same images as the 'unusable' class. Thresholds are FIT on the training split and REPORTED on the test split.

Limits: the dataset images were accepted by clinicians for a research dataset, not labelled for 'photo usability'; degradations are simulated.
This calibrates false-reject behaviour on lesion close-ups; it does not validate face selfies or real-world bad photos."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.imaging import suitability as S  # noqa: E402
from app.training import trainer as T  # noqa: E402


def small(img, side=800):
    return S._resize(img, side)


def metrics(img) -> dict:
    g = cv2.cvtColor(small(img), cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = cv2.Laplacian(g, cv2.CV_32F)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    return {"lapvar": float(lap.var()), "tenengrad": float(np.mean(gx ** 2 + gy ** 2)), "lapvar_norm": float(lap.var() / (g.var() + 1.0)),
            "brightness": float(g.mean()), "contrast": float(g.std()), "over": float((g > 245).mean()), "under": float((g < 15).mean())}


def degrade(img, kind, rng):
    long = max(img.shape[:2])
    if kind == "blur_mild":
        return cv2.GaussianBlur(img, (0, 0), 0.004 * long)
    if kind == "blur_strong":
        return cv2.GaussianBlur(img, (0, 0), 0.010 * long)
    if kind == "motion":
        k = np.zeros((int(0.02 * long) | 1,) * 2, np.float32); k[k.shape[0] // 2, :] = 1 / k.shape[1]
        return cv2.filter2D(img, -1, k)
    if kind == "dark":
        return (img.astype(np.float32) * 0.35).astype(np.uint8)
    if kind == "bright":
        return np.clip(img.astype(np.float32) * 1.9 + 40, 0, 255).astype(np.uint8)
    if kind == "washed":
        m = img.mean()
        return np.clip((img.astype(np.float32) - m) * 0.25 + m + 25, 0, 255).astype(np.uint8)
    if kind == "jpeg10":
        return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 8])[1], 1)
    raise ValueError(kind)


KINDS = ["blur_mild", "blur_strong", "motion", "dark", "bright", "washed", "jpeg10"]


def collect(entries, root, n, seed):
    rng = random.Random(seed)
    pick = rng.sample(entries, min(n, len(entries)))
    good, bad = [], {k: [] for k in KINDS}
    for e in pick:
        img = S.decode((Path(root) / e["file"]).read_bytes())
        if min(img.shape[:2]) < 300:
            continue
        good.append(metrics(img))
        for k in KINDS:
            bad[k].append(metrics(degrade(img, k, rng)))
    return good, bad


def auroc(pos, neg):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(np.r_[np.zeros(len(pos)), np.ones(len(neg))], np.r_[pos, neg]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="124f81d4a9594958896d16e61944cd4d")
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--out", default="../docs/eval")
    a = ap.parse_args()
    card = json.loads(db.q1("SELECT data FROM model_versions WHERE id=?", (a.model,))["data"])
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (card["dataset_version_id"],))["manifest"])
    sp = T.stratified_group_split([e for e in mf["entries"] if e.get("label")], card["seed"])
    gtr, btr = collect(sp["train"], mf["root"], a.n, 1)
    gte, bte = collect(sp["test"], mf["root"], 325, 2)
    out = {"fit_on": f"train split ({len(gtr)} images)", "reported_on": f"test split ({len(gte)} images)", "current_rule": "sharpness<40 or contrast<18 or brightness outside 55-215 or small<300px", "focus_metric_auroc_train": {}}
    # --- choose the focus metric by how well it separates usable from blurred images (train)
    for m in ("lapvar", "tenengrad", "lapvar_norm"):
        out["focus_metric_auroc_train"][m] = {k: round(auroc([-x[m] for x in gtr], [-x[m] for x in btr[k]]), 3) for k in ("blur_mild", "blur_strong", "motion")}
    best = max(("lapvar", "tenengrad", "lapvar_norm"), key=lambda m: sum(out["focus_metric_auroc_train"][m].values()))
    out["chosen_focus_metric"] = best
    # --- thresholds: false-reject <= 3% of usable training images per check
    th = {"focus": float(np.percentile([x[best] for x in gtr], 3)), "contrast": float(np.percentile([x["contrast"] for x in gtr], 3)),
          "dark": float(np.percentile([x["brightness"] for x in gtr], 3)), "bright": float(np.percentile([x["brightness"] for x in gtr], 97))}
    th["dark"] = min(th["dark"], 85.0); th["bright"] = max(th["bright"], 200.0)
    out["thresholds"] = {k: round(v, 4) for k, v in th.items()}

    def rejects(x):
        return {"focus": x[best] < th["focus"], "contrast": x["contrast"] < th["contrast"], "dark": x["brightness"] < th["dark"] or x["under"] > 0.5,
                "bright": x["brightness"] > th["bright"] or x["over"] > 0.35}
    def old(x):
        return x["lapvar"] < 40 or x["contrast"] < 18 or x["brightness"] < 55 or x["brightness"] > 215 or x["under"] > 0.5 or x["over"] > 0.35
    def report(good, bad):
        r = {"false_reject_usable_new": round(float(np.mean([any(rejects(x).values()) for x in good])), 3), "false_reject_usable_old_rule": round(float(np.mean([old(x) for x in good])), 3), "detect": {}}
        for k, v in bad.items():
            r["detect"][k] = {"new": round(float(np.mean([any(rejects(x).values()) for x in v])), 3), "old_rule": round(float(np.mean([old(x) for x in v])), 3)}
        return r
    out["train"], out["test"] = report(gtr, btr), report(gte, bte)
    p = Path(a.out); p.mkdir(parents=True, exist_ok=True)
    (p / "quality_calibration.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

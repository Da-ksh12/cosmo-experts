"""Candidate features for motion-blur and heavy-compression detection, evaluated on REAL usable images (PAD-UFES-20, licence-verified) against simulated
degradations of the same images. Features are compared by AUROC and by detection at a fixed budget of false rejections of usable images (3 % on the TRAINING
split); thresholds are fit on train and every number reported here is measured on the held-out TEST split.

Limits (also written into the output): 'usable' means accepted into a research dataset, not labelled for photo usability; degradations are simulated
(horizontal/vertical/diagonal linear motion, JPEG re-encoding); face selfies and real-world bad photos are NOT covered.

    python -m eval.quality_features --out ../docs/eval
"""
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


# ---------------------------------------------------------------- features (higher = worse, i.e. more likely unusable, unless noted)
def gray800(img):
    return cv2.cvtColor(S._resize(img, 800), cv2.COLOR_BGR2GRAY).astype(np.float32)


def f_lapvar(g):
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def f_tile_sharp(g, q=90, grid=8):
    """High quantile of per-tile Laplacian variance: smooth skin has many dull tiles, but a focused photo still has some textured ones."""
    lap = cv2.Laplacian(g, cv2.CV_32F)
    h, w = lap.shape
    v = [float(lap[i * h // grid:(i + 1) * h // grid, j * w // grid:(j + 1) * w // grid].var()) for i in range(grid) for j in range(grid)]
    return float(np.percentile(v, q))


def f_coherence(g):
    """Structure-tensor coherence of the high-pass image. Linear motion blur removes gradients along one direction, so what is left is strongly oriented."""
    hp = g - cv2.GaussianBlur(g, (0, 0), 2.0)
    gx, gy = cv2.Sobel(hp, cv2.CV_32F, 1, 0), cv2.Sobel(hp, cv2.CV_32F, 0, 1)
    jxx, jyy, jxy = float((gx * gx).mean()), float((gy * gy).mean()), float((gx * gy).mean())
    tr, det = jxx + jyy, jxx * jyy - jxy * jxy
    disc = max(tr * tr / 4 - det, 0.0) ** 0.5
    l1, l2 = tr / 2 + disc, tr / 2 - disc
    return float((l1 - l2) / (l1 + l2 + 1e-6))


def f_dir_ratio(g):
    """Ratio of the weakest to the strongest directional second-derivative energy over 4 directions (lower = more directional loss of detail)."""
    e = []
    for k in (np.array([[0, 0, 0], [1, -2, 1], [0, 0, 0]], np.float32), np.array([[0, 1, 0], [0, -2, 0], [0, 1, 0]], np.float32),
              np.array([[1, 0, 0], [0, -2, 0], [0, 0, 1]], np.float32), np.array([[0, 0, 1], [0, -2, 0], [1, 0, 0]], np.float32)):
        e.append(float(np.mean(cv2.filter2D(g, cv2.CV_32F, k) ** 2)))
    return min(e) / (max(e) + 1e-6)


def f_blockiness(img, size=1024):
    """JPEG 8x8 grid artefact: mean absolute luminance step across block boundaries divided by the step inside blocks. Computed at NATIVE resolution
    (resizing destroys the grid) on a centre crop. About 1.0 for an image without block artefacts; clearly above 1 for heavily quantised JPEG."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape
    g = g[max(0, (h - size) // 2):max(0, (h - size) // 2) + size, max(0, (w - size) // 2):max(0, (w - size) // 2) + size]
    h, w = (g.shape[0] // 8) * 8, (g.shape[1] // 8) * 8
    g = g[:h, :w]
    dx, dy = np.abs(np.diff(g, axis=1)), np.abs(np.diff(g, axis=0))
    bx = dx[:, 7::8].mean() / (np.delete(dx, np.s_[7::8], axis=1).mean() + 1e-6)
    by = dy[7::8, :].mean() / (np.delete(dy, np.s_[7::8], axis=0).mean() + 1e-6)
    return float((bx + by) / 2)


FEATURES = {  # name -> (function, direction): +1 means "high value = bad", -1 means "low value = bad"
    "lapvar": (lambda img, g: f_lapvar(g), -1),
    "tile_sharp_p90": (lambda img, g: f_tile_sharp(g, 90), -1),
    "tile_sharp_p75": (lambda img, g: f_tile_sharp(g, 75), -1),
    "coherence": (lambda img, g: f_coherence(g), +1),
    "dir_ratio": (lambda img, g: f_dir_ratio(g), -1),
    "blockiness": (lambda img, g: f_blockiness(img), +1),
}


# ---------------------------------------------------------------- degradations
def degrade(img, kind):
    long = max(img.shape[:2])
    if kind.startswith("motion"):
        _, ang, ln = kind.split("_")
        n = max(3, int(float(ln) * long) | 1)
        k = np.zeros((n, n), np.float32); k[n // 2, :] = 1 / n
        M = cv2.getRotationMatrix2D((n / 2 - .5, n / 2 - .5), float(ang), 1)
        k = cv2.warpAffine(k, M, (n, n)); k /= k.sum()
        return cv2.filter2D(img, -1, k)
    if kind.startswith("jpeg"):
        q = int(kind[4:])
        return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1], 1)
    if kind == "blur_mild":
        return cv2.GaussianBlur(img, (0, 0), 0.004 * long)
    raise ValueError(kind)


KINDS = ["motion_0_0.01", "motion_0_0.02", "motion_45_0.02", "motion_90_0.02", "motion_0_0.04", "blur_mild", "jpeg8", "jpeg15", "jpeg30"]


def vec(img):
    g = gray800(img)
    return {n: f(img, g) for n, (f, _) in FEATURES.items()}


def collect(entries, root, n, seed):
    rng = random.Random(seed)
    pick = rng.sample(entries, min(n, len(entries)))
    good, bad = [], {k: [] for k in KINDS}
    for e in pick:
        img = S.decode((Path(root) / e["file"]).read_bytes())
        if min(img.shape[:2]) < 300:
            continue
        good.append(vec(img))
        for k in KINDS:
            bad[k].append(vec(degrade(img, k)))
    return good, bad


def auroc(pos, neg):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(np.r_[np.zeros(len(pos)), np.ones(len(neg))], np.r_[pos, neg]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="124f81d4a9594958896d16e61944cd4d")
    ap.add_argument("--n-train", type=int, default=250)
    ap.add_argument("--n-test", type=int, default=250)
    ap.add_argument("--fpr", type=float, default=0.03, help="share of usable TRAIN images each rule may reject")
    ap.add_argument("--out", default="../docs/eval")
    a = ap.parse_args()
    card = json.loads(db.q1("SELECT data FROM model_versions WHERE id=?", (a.model,))["data"])
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (card["dataset_version_id"],))["manifest"])
    sp = T.stratified_group_split([e for e in mf["entries"] if e.get("label")], card["seed"])
    gtr, btr = collect(sp["train"], mf["root"], a.n_train, 1)
    gte, bte = collect(sp["test"], mf["root"], a.n_test, 2)
    out = {"fit_on": f"train split ({len(gtr)} usable images x {len(KINDS)} degradations)", "reported_on": f"test split ({len(gte)} usable images)",
           "false_reject_budget_on_usable_train": a.fpr,
           "limits": ["'usable' = accepted into a research dataset, not labelled for photo usability", "degradations are simulated", "no face selfies or real-world bad photos"],
           "train_auroc": {}, "thresholds": {}, "test_detection_at_budget": {}, "test_false_reject": {}}
    for name, (_, sign) in FEATURES.items():
        # orient so that larger = worse
        sc = lambda x: sign * x[name]  # noqa: E731
        out["train_auroc"][name] = {k: round(auroc([sc(x) for x in gtr], [sc(x) for x in btr[k]]), 3) for k in KINDS}
        thr = float(np.percentile([sc(x) for x in gtr], 100 * (1 - a.fpr)))
        out["thresholds"][name] = {"flag_when": ("value <= " + str(round(-thr, 4))) if sign < 0 else ("value >= " + str(round(thr, 4))), "raw": round(sign * thr if sign < 0 else thr, 4)}
        out["test_false_reject"][name] = round(float(np.mean([sc(x) >= thr for x in gte])), 3)
        out["test_detection_at_budget"][name] = {k: round(float(np.mean([sc(x) >= thr for x in bte[k]])), 3) for k in KINDS}
    # union of the current focus rule and each candidate: what does adding it buy, and what does it cost?
    cur_thr = float(np.percentile([-x["lapvar"] for x in gtr], 100 * (1 - a.fpr)))
    cur = lambda x: -x["lapvar"] >= cur_thr  # noqa: E731
    out["union_with_current_focus_rule"] = {}
    for name, (_, sign) in FEATURES.items():
        if name == "lapvar":
            continue
        thr = float(np.percentile([sign * x[name] for x in gtr], 100 * (1 - a.fpr)))
        rule = lambda x, n=name, s=sign, t=thr: cur(x) or (s * x[n] >= t)  # noqa: E731
        out["union_with_current_focus_rule"][name] = {"false_reject_test": round(float(np.mean([rule(x) for x in gte])), 3),
                                                      "detection_test": {k: round(float(np.mean([rule(x) for x in bte[k]])), 3) for k in KINDS}}
    out["union_with_current_focus_rule"]["(current rule alone)"] = {"false_reject_test": round(float(np.mean([cur(x) for x in gte])), 3),
                                                                      "detection_test": {k: round(float(np.mean([cur(x) for x in bte[k]])), 3) for k in KINDS}}
    p = Path(a.out); p.mkdir(parents=True, exist_ok=True)
    (p / "quality_features.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

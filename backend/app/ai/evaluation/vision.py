"""Vision evaluation: per-class sensitivity and specificity with uncertainty, macro-F1 and balanced accuracy with bootstrap intervals, confusion matrix, calibration, selective
prediction, out-of-distribution behaviour, subgroup performance, and paired comparison of two candidates on the same test set.

Pure functions over arrays: no database, no model, no files (loading a registered model is in `models.py`). The numbers agree with `training/trainer.full_metrics` on the same
inputs (asserted in tests/test_ai_evaluation.py), so this module is a reusable runner, not a second definition of the metrics.

Reading the output honestly: intervals are wide for small classes and subgroups; `reliable` is False below the sample sizes noted; a gap between subgroups is reported with the
sizes behind it and is not, by itself, evidence of bias or of fairness."""
from __future__ import annotations

import numpy as np

from .common import bootstrap, wilson

MIN_RELIABLE_CLASS = 20          # positives needed before a per-class rate is called reliable
MIN_RELIABLE_GROUP = 50          # images needed before a subgroup figure is called reliable
MIN_REPORT_GROUP = 10            # below this a subgroup is listed with counts only


def confusion(y: np.ndarray, pred: np.ndarray, k: int) -> np.ndarray:
    cm = np.zeros((k, k), dtype=int)
    np.add.at(cm, (np.asarray(y), np.asarray(pred)), 1)
    return cm


def _macro_f1(cm: np.ndarray) -> float:
    tp = np.diag(cm).astype(float)
    p, r = tp / np.maximum(cm.sum(0), 1), tp / np.maximum(cm.sum(1), 1)
    f = np.where(p + r > 0, 2 * p * r / np.maximum(p + r, 1e-12), 0.0)
    return float(f.mean())


def _balanced_accuracy(cm: np.ndarray) -> float:
    support = cm.sum(1)
    present = support > 0
    return float((np.diag(cm)[present] / support[present]).mean()) if present.any() else 0.0


def macro_f1(y, pred, k) -> float:
    return _macro_f1(confusion(y, pred, k))


def balanced_accuracy(y, pred, k) -> float:
    return _balanced_accuracy(confusion(y, pred, k))


def auroc(pos_scores: np.ndarray, neg_scores: np.ndarray) -> float | None:
    """Mann-Whitney AUROC with ties counted half. None if either side is empty."""
    pos, neg = np.asarray(pos_scores, float), np.asarray(neg_scores, float)
    if len(pos) == 0 or len(neg) == 0:
        return None
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv))
    sorted_v = allv[order]
    i = 0
    while i < len(sorted_v):
        j = i
        while j + 1 < len(sorted_v) and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    u = ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2
    return float(u / (len(pos) * len(neg)))


def per_class(cm: np.ndarray, classes: list[str], y: np.ndarray, P: np.ndarray, critical: list[str] | None = None) -> dict:
    out = {}
    total = cm.sum()
    for i, c in enumerate(classes):
        tp = int(cm[i, i]); fn = int(cm[i].sum() - tp); fp = int(cm[:, i].sum() - tp); tn = int(total - tp - fn - fp)
        npos = tp + fn
        a = auroc(P[y == i, i], P[y != i, i]) if 0 < npos < len(y) else None
        prec = tp / (tp + fp) if (tp + fp) else None
        sens = tp / npos if npos else None
        out[c] = {"n": npos, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
                  "sensitivity": round(sens, 4) if sens is not None else None, "sensitivity_ci95": wilson(tp, npos),
                  "specificity": round(tn / (tn + fp), 4) if (tn + fp) else None, "specificity_ci95": wilson(tn, tn + fp),
                  "precision": round(prec, 4) if prec is not None else None, "precision_ci95": wilson(tp, tp + fp),
                  "f1": round(2 * prec * sens / (prec + sens), 4) if prec and sens else (0.0 if npos else None),
                  "auroc": round(a, 4) if a is not None else None, "reliable": npos >= MIN_RELIABLE_CLASS, "critical": bool(critical and c in critical)}
    return out


def calibration(P: np.ndarray, y: np.ndarray, bins: int = 15) -> dict:
    conf = P.max(1)
    pred = P.argmax(1)
    hit = (pred == y).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    table, ece, mce = [], 0.0, 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi) if lo > 0 else (conf >= lo) & (conf <= hi)
        if not m.any():
            continue
        gap = abs(hit[m].mean() - conf[m].mean())
        ece += m.mean() * gap
        mce = max(mce, gap)
        table.append({"bin": [round(lo, 3), round(hi, 3)], "n": int(m.sum()), "mean_confidence": round(float(conf[m].mean()), 4), "accuracy": round(float(hit[m].mean()), 4)})
    onehot = np.zeros_like(P)
    onehot[np.arange(len(y)), y] = 1
    return {"ece": round(float(ece), 4), "mce": round(float(mce), 4), "brier": round(float(((P - onehot) ** 2).sum(1).mean()), 4),
            "nll": round(float(-np.log(np.clip(P[np.arange(len(y)), y], 1e-12, 1)).mean()), 4), "mean_confidence": round(float(conf.mean()), 4),
            "accuracy": round(float(hit.mean()), 4), "bins": bins, "reliability": table,
            "reading": "ECE is the average gap between confidence and accuracy; a low value on a small test set is weak evidence of good calibration."}


def selective(P: np.ndarray, y: np.ndarray, thresholds=(0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)) -> list[dict]:
    conf, pred = P.max(1), P.argmax(1)
    out = []
    for t in thresholds:
        m = conf >= t
        out.append({"threshold": t, "coverage": round(float(m.mean()), 4), "accuracy_on_accepted": round(float((pred[m] == y[m]).mean()), 4) if m.any() else None})
    return out


def ood(P_in: np.ndarray, P_out: np.ndarray, source: str) -> dict:
    """Does the model's confidence separate in-distribution from out-of-distribution images? An AUROC near 0.5 (or below) means confidence cannot flag unsupported inputs."""
    ci, co = P_in.max(1), P_out.max(1)
    a = auroc(ci, co)
    thr = (0.5, 0.7, 0.9)
    return {"source": source, "n_out": int(len(co)), "n_in": int(len(ci)), "mean_confidence_out": round(float(co.mean()), 4), "mean_confidence_in": round(float(ci.mean()), 4),
            "fraction_out_above": {str(t): round(float((co >= t).mean()), 4) for t in thr}, "fraction_in_above": {str(t): round(float((ci >= t).mean()), 4) for t in thr},
            "auroc_confidence_separates_in_vs_out": round(a, 4) if a is not None else None,
            "reliable": len(co) >= 50, "reading": "Below 0.5 means unsupported images receive HIGHER confidence than supported ones." if a is not None and a < 0.5 else
            "Confidence is not a safe detector of unsupported inputs unless this is high and the out-of-distribution sample is large."}


def subgroups(y: np.ndarray, pred: np.ndarray, groups: dict[str, list], k: int, classes: list[str], critical: list[str] | None = None) -> dict:
    out = {}
    cidx = [classes.index(c) for c in (critical or []) if c in classes]
    for dim, labels in groups.items():
        labels = np.array(["NA" if (v is None or str(v) in ("", "nan", "None")) else str(v) for v in labels])
        levels = {}
        for lv in sorted(set(labels)):
            idx = np.where(labels == lv)[0]
            n = len(idx)
            row = {"n": n, "reliable": n >= MIN_RELIABLE_GROUP}
            if n >= MIN_REPORT_GROUP:
                yy, pp = y[idx], pred[idx]
                row["accuracy"] = round(float((yy == pp).mean()), 4)
                row["accuracy_ci95"] = wilson(int((yy == pp).sum()), n)
                if len(set(yy.tolist())) >= 2:
                    row["balanced_accuracy"] = round(balanced_accuracy(yy, pp, k), 4)
                    row["macro_f1"] = round(macro_f1(yy, pp, k), 4)
                sens = {}
                for ci in cidx:
                    pos = int((yy == ci).sum())
                    if pos >= 5:
                        sens[classes[ci]] = {"n": pos, "sensitivity": round(float(((pp == ci) & (yy == ci)).sum() / pos), 4), "ci95": wilson(int(((pp == ci) & (yy == ci)).sum()), pos)}
                if sens:
                    row["critical_class_sensitivity"] = sens
            levels[lv] = row
        reliable = {lv: r for lv, r in levels.items() if r["reliable"] and "accuracy" in r and lv != "NA"}
        gap = None
        if len(reliable) >= 2:
            lo = min(reliable, key=lambda l: reliable[l]["accuracy"]); hi = max(reliable, key=lambda l: reliable[l]["accuracy"])
            gap = {"lowest": lo, "highest": hi, "accuracy_gap": round(reliable[hi]["accuracy"] - reliable[lo]["accuracy"], 4),
                   "note": "A gap between groups of different size, case mix and difficulty is not by itself evidence of bias; the intervals above show how uncertain each figure is."}
        out[dim] = {"levels": levels, "largest_gap_among_reliable_groups": gap}
    return out


def paired_difference(y: np.ndarray, pred_a: np.ndarray, pred_b: np.ndarray, k: int, metric: str = "macro_f1", n_boot: int = 1000, seed: int = 0) -> dict:
    """Candidate A minus candidate B on the SAME test images (paired bootstrap over images). `p_a_better` is the share of resamples in which A beat B."""
    fn = macro_f1 if metric == "macro_f1" else balanced_accuracy
    rng = np.random.default_rng(seed)
    n = len(y)
    diffs = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        diffs.append(fn(y[i], pred_a[i], k) - fn(y[i], pred_b[i], k))
    d = fn(y, pred_a, k) - fn(y, pred_b, k)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"metric": metric, "difference_a_minus_b": round(float(d), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)], "p_a_better": round(float(np.mean(np.array(diffs) > 0)), 4),
            "interval_excludes_zero": bool(lo > 0 or hi < 0), "n": n}


def evaluate(y, P, classes: list[str], *, groups: dict[str, list] | None = None, critical: list[str] | None = None, P_ood: np.ndarray | None = None,
             ood_source: str = "", n_boot: int = 1000, seed: int = 0) -> dict:
    """The full report for one model on one test set. `P` are the probabilities that would be shown (already temperature-scaled by the model's own card)."""
    y = np.asarray(y)
    P = np.asarray(P, float)
    k = len(classes)
    pred = P.argmax(1)
    cm = confusion(y, pred, k)
    rep = {"n": int(len(y)), "classes": classes, "class_counts": {c: int((y == i).sum()) for i, c in enumerate(classes)},
           "accuracy": round(float((pred == y).mean()), 4), "accuracy_ci95": wilson(int((pred == y).sum()), len(y)),
           "macro_f1": round(_macro_f1(cm), 4), "macro_f1_ci95": bootstrap(lambda i: macro_f1(y[i], pred[i], k), len(y), n_boot, seed, 3),
           "balanced_accuracy": round(_balanced_accuracy(cm), 4), "balanced_accuracy_ci95": bootstrap(lambda i: balanced_accuracy(y[i], pred[i], k), len(y), n_boot, seed, 3),
           "confusion_matrix": cm.tolist(), "per_class": per_class(cm, classes, y, P, critical), "calibration": calibration(P, y), "selective_prediction": selective(P, y),
           "warnings": ([f"Only {len(y)} test images: figures are statistically unreliable."] if len(y) < 100 else []) +
                       [f"Class '{c}' has only {m['n']} test images: its rates are unreliable." for c, m in per_class(cm, classes, y, P).items() if m["n"] < MIN_RELIABLE_CLASS]}
    if groups:
        rep["subgroups"] = subgroups(y, pred, groups, k, classes, critical)
    if P_ood is not None and len(P_ood):
        rep["ood"] = ood(P, np.asarray(P_ood, float), ood_source)
    return rep

"""Phase 5 training: a linear probe on frozen backbone features, trained on one or more licensed sources and evaluated across sources and skin tones.

Rules this module enforces (they are what makes its numbers believable):
* a source is used only if the registry's licence gate allows model training on it; the licence evidence of every source goes on the model card;
* subjects (patients, or whole cases) never straddle train/validation/test. Images with no patient or lesion identifier may be trained on but are kept out of validation and test, so the
  headline numbers do not depend on identifiers that are not there. Each source is split on its own with the same deterministic splitter the older models used, so PAD-UFES-20's test set
  is exactly the one the four earlier models were scored on;
* an EXTERNAL source (another centre, another camera) is never used for training or model selection: it is scored once, after the model is fixed;
* validation and test images that are near-duplicates of a training image (32x32 thumbnail correlation) are removed BEFORE any metric is computed, and the numbers removed are recorded;
* the feature kind and the regularisation strength are chosen on validation only; the test set is scored once; the temperature is fitted on validation;
* every candidate is shown beside baselines: the majority class, a k-nearest-neighbour classifier on the same features, and a ResNet18-ImageNet frozen-feature classifier;
* thresholds are the ones in `trainer.GATES` and are never changed here.

The artifact is the head only (scaler + weights + temperature, a `.npz` that `serving` already knows how to load); the backbone is referenced by file hash."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from dataclasses import dataclass, field

import numpy as np

from .. import db
from ..ai.evaluation.common import sha256_json
from ..ai.resources import available_mb, select_device
from ..config import DATA_DIR, MODEL_DIR
from ..datasets import registry
from . import cards, features as FE, probe, task_specs, tasks, trainer as T

NEAR_DUP = 0.985


class Blocked(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class SourceSpec:
    name: str                              # how reports refer to it
    dataset_id: str
    version_id: str
    role: str = "train"                    # "train": split into train/val/test | "external": scored once, never trained on or selected with
    unknown_group_train_only: bool = False  # images with no patient/lesion id: train only
    split_policy: str = "random-group"     # "random-group" (StratifiedGroupKFold, seed) | "official" (the source's own train/test, patients that straddle it go to train)
    keep_labels: tuple[str, ...] | None = None


# ---------------------------------------------------------------- data assembly
def load_source(spec: SourceSpec, classes_hint: set[str] | None = None) -> dict:
    rec = registry.get(spec.dataset_id)
    row = db.q1("SELECT manifest FROM dataset_versions WHERE id=? AND dataset_id=?", (spec.version_id, spec.dataset_id))
    if rec is None or row is None:
        raise Blocked([f"{spec.name}: unknown dataset or version."])
    mf = json.loads(row["manifest"])
    if mf.get("complete") is False:
        raise Blocked([f"{spec.name}: this dataset version is incomplete or was superseded; import it again."])
    gate = registry.license_gate(rec, "model_training", commercial=True)
    if not gate["allowed"]:
        raise Blocked([f"{spec.name}: {r}" for r in gate["reasons"]])
    entries = [e for e in mf["entries"] if e.get("label")]
    for i, e in enumerate(entries):
        e["_src"], e["_row"], e["_root"] = spec.name, i, mf["root"]
        e["_gkey"] = f"{spec.name}:{e['group_id']}"
        e["_nogroup"] = "nogroup" in str(e.get("label_provenance", ""))
    return {"spec": spec, "rec": rec, "mf": mf, "entries": entries, "manifest_sha256": cards.manifest_sha256(mf),
            "license": {"dataset": spec.dataset_id, "version": mf["version"], "spdx": rec.get("verified_spdx"), "official_license_name": rec.get("official_license_name") or rec.get("claimed_license"),
                        "terms_snapshot_hash": rec.get("terms_snapshot_hash"), "source": rec.get("url"), "attribution": rec.get("attribution")}}


def _val_split(entries: list[dict], seed: int) -> tuple[list[dict], list[dict]]:
    from sklearn.model_selection import StratifiedGroupKFold
    y, g = [e["label"] for e in entries], [e["group_id"] for e in entries]
    tr, va = next(iter(StratifiedGroupKFold(n_splits=7, shuffle=True, random_state=seed).split(entries, y, g)))
    return [entries[i] for i in tr], [entries[i] for i in va]


def split_source(src: dict, seed: int) -> dict[str, list[dict]]:
    spec, entries = src["spec"], src["entries"]
    if spec.keep_labels:
        entries = [e for e in entries if e["label"] in spec.keep_labels]
    if spec.role == "external":
        return {"external": entries}
    unknown = [e for e in entries if spec.unknown_group_train_only and e["_nogroup"]]
    known = [e for e in entries if not (spec.unknown_group_train_only and e["_nogroup"])]
    if spec.split_policy == "official":
        test = [e for e in known if ":test:" in str(e.get("label_provenance"))]
        train_g = {e["group_id"] for e in known if ":train:" in str(e.get("label_provenance"))}
        straddle = {e["group_id"] for e in test} & train_g
        test = [e for e in test if e["group_id"] not in straddle]
        rest = [e for e in known if e not in test]
        tr, va = _val_split(rest, seed)
        out = {"train": tr + unknown, "val": va, "test": test}
        out["_note"] = {"patients_in_both_official_splits_moved_to_train": len(straddle)}
    else:
        out = T.stratified_group_split(known, seed)
        out["train"] = out["train"] + unknown
    return out


def assemble(sources: list[dict], seed: int) -> tuple[dict, dict, dict]:
    splits = {"train": [], "val": [], "test": []}
    external: dict[str, list[dict]] = {}
    notes = {}
    for s in sources:
        sp = split_source(s, seed)
        notes[s["spec"].name] = sp.pop("_note", {})
        if "external" in sp:
            external[s["spec"].name] = sp["external"]
        else:
            for k in splits:
                splits[k] += sp[k]
    groups = {k: {e["_gkey"] for e in v} for k, v in splits.items()}
    assert not (groups["train"] & groups["val"] or groups["train"] & groups["test"] or groups["val"] & groups["test"]), "split leakage"
    return splits, external, notes


# ---------------------------------------------------------------- leakage audit
def thumbs(X: np.ndarray, rows: list[int]) -> np.ndarray:
    import cv2
    out = np.empty((len(rows), 1024), np.float32)
    for j, i in enumerate(rows):
        g = cv2.resize(cv2.cvtColor(np.ascontiguousarray(X[i]), cv2.COLOR_RGB2GRAY), (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
        g -= g.mean()
        out[j] = g / (np.linalg.norm(g) + 1e-6)
    return out


def near_duplicate_rows(A: np.ndarray, B: np.ndarray, thr: float = NEAR_DUP, chunk: int = 1024) -> np.ndarray:
    """Indices of rows of A whose thumbnail correlates >= thr with some row of B (chunked so memory stays small)."""
    hit = np.zeros(len(A), bool)
    for s in range(0, len(A), chunk):
        hit[s:s + chunk] = ((A[s:s + chunk] @ B.T) >= thr).any(1)
    return np.nonzero(hit)[0]


# ---------------------------------------------------------------- heads
def _scaled_lr(Ftr, ytr, C):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(Ftr)
    return sc, LogisticRegression(max_iter=3000, class_weight="balanced", C=C).fit(sc.transform(Ftr), ytr)


def select_head(Ftr, ytr, Fva, yva, k: int, log=lambda m: None) -> dict:
    """Feature kind (CLS token alone, or CLS + mean patch token) at C=0.1, then C over a grid for the winner: validation macro-F1 only."""
    table = []
    sl = {"cls": slice(0, 384), "cls+mean": slice(0, 768)}
    best_kind, best = None, -1.0
    for kind, s in sl.items():
        sc, clf = _scaled_lr(Ftr[:, s], ytr, 0.1)
        f1 = T.macro_f1(yva, clf.predict(sc.transform(Fva[:, s])), k)
        table.append({"feature_kind": kind, "C": 0.1, "val_macro_f1": round(f1, 4)})
        log(f"feature kind {kind}, C=0.1: val macro-F1 {f1:.3f}")
        if f1 > best:
            best_kind, best = kind, f1
    s = sl[best_kind]
    chosen = (best, 0.1)
    for C in (0.003, 0.01, 0.03, 0.3, 1.0):
        sc, clf = _scaled_lr(Ftr[:, s], ytr, C)
        f1 = T.macro_f1(yva, clf.predict(sc.transform(Fva[:, s])), k)
        table.append({"feature_kind": best_kind, "C": C, "val_macro_f1": round(f1, 4)})
        log(f"feature kind {best_kind}, C={C}: val macro-F1 {f1:.3f}")
        if f1 > chosen[0]:
            chosen = (f1, C)
    return {"feature_kind": best_kind, "slice": s, "C": chosen[1], "val_macro_f1": round(chosen[0], 4), "candidates": table}


def knn_baseline(Ftr, ytr, Fva, yva, Fte, k: int) -> tuple[np.ndarray, int]:
    from sklearn.neighbors import KNeighborsClassifier
    best = (-1.0, 5)
    for n in (5, 15, 31):
        m = KNeighborsClassifier(n_neighbors=n, metric="cosine", weights="distance", algorithm="brute").fit(Ftr[:, :384], ytr)
        f1 = T.macro_f1(yva, m.predict(Fva[:, :384]), k)
        if f1 > best[0]:
            best = (f1, n)
    m = KNeighborsClassifier(n_neighbors=best[1], metric="cosine", weights="distance", algorithm="brute").fit(Ftr[:, :384], ytr)
    P = np.zeros((len(Fte), k))
    P[:, m.classes_] = m.predict_proba(Fte[:, :384])
    return P, best[1]


def _brief(m: dict) -> dict:
    return {k: v for k, v in m.items() if k in ("n", "accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95", "ece")}


def subgroup_table(entries: list[dict], y: np.ndarray, pred: np.ndarray, key, k: int, classes: list[str], critical: list[str]) -> dict:
    out = {}
    for g in sorted({key(e) for e in entries}, key=str):
        idx = [i for i, e in enumerate(entries) if key(e) == g]
        yy, pp = y[idx], pred[idx]
        row = {"n": len(idx), "accuracy": round(float((yy == pp).mean()), 3), "balanced_accuracy": round(T.balanced_acc(yy, pp, k), 3) if len(idx) >= 10 else None, "reliable": len(idx) >= 50}
        for c in critical:
            ci = classes.index(c) if c in classes else None
            if ci is not None and (yy == ci).sum():
                row[f"sensitivity_{c}"] = {"n": int((yy == ci).sum()), "value": round(float((pp[yy == ci] == ci).mean()), 3)}
        out[str(g)] = row
    return out


# ---------------------------------------------------------------- the run
def plan_run(n_images: int, sources: list[SourceSpec]) -> dict:
    free = available_mb()
    return {"images": n_images, "free_ram_mb": round(free), "free_disk_gb": round(__import__("shutil").disk_usage(DATA_DIR).free / 1e9, 1), "cpu_logical": os.cpu_count(),
            "device": select_device(None).kind, "sources": [s.name for s in sources]}


def run(task_id: str, name: str, specs: list[SourceSpec], actor: str, *, seed: int = 17, intended_use: str, limitations: list[str], serving_warnings: list[str], quality_gate: str = "skin_closeup",
        robustness_n: int = 300, ood_dir: str | None = None, max_seconds: float | None = None, progress=None, should_stop=None, train_labels: tuple[str, ...] | None = None) -> dict:
    spec_task = task_specs.get(task_id)
    if spec_task is None or tasks.get_task(task_id) is None:
        raise Blocked([f"unknown task {task_id}"])
    stages: dict[str, float] = {}
    log_lines: list[str] = []

    def log(m):
        log_lines.append(f"{time.strftime('%H:%M:%S')} {m}")
        print(log_lines[-1], flush=True)
        if progress:
            progress({"log": log_lines[-1]})
    t_start = time.time()

    def stage(label):
        stages[label] = round(time.time() - t_start, 1)

    srcs = [load_source(s) for s in specs]
    splits, external, split_notes = assemble(srcs, seed)
    if train_labels:
        splits = {k: [e for e in v if e["label"] in train_labels] for k, v in splits.items()}
    classes = sorted({e["label"] for e in splits["train"]})
    c2i = {c: i for i, c in enumerate(classes)}
    k = len(classes)
    excluded_labels = {}
    for k_, v in list(splits.items()) + list(external.items()):
        drop = [e for e in v if e["label"] not in c2i]
        if drop:
            excluded_labels[k_] = dict(sorted({lab: sum(1 for e in drop if e["label"] == lab) for lab in {e["label"] for e in drop}}.items()))
    splits = {k_: [e for e in v if e["label"] in c2i] for k_, v in splits.items()}
    external = {k_: [e for e in v if e["label"] in c2i] for k_, v in external.items()}
    all_entries = [e for s in srcs for e in s["entries"]]
    log(f"{name}: task {task_id}; classes {classes}; images train/val/test = {[len(splits[s]) for s in ('train', 'val', 'test')]}; external { {n: len(v) for n, v in external.items()} }")
    if min(len(splits["train"]), len(splits["val"]), len(splits["test"])) == 0:
        raise Blocked(["an empty train, validation or test split"])

    plan = plan_run(sum(len(s["entries"]) for s in srcs), specs)
    run_id = db.uid()
    cfg = {"experiment": name, "task": task_id, "sources": [s.__dict__ for s in specs], "seed": seed, "backbone": "dinov2_s14", "heads": "logistic regression (balanced), feature kind and C chosen on validation",
           "baselines": ["majority", "dinov2 kNN", "resnet18_in1k + logistic regression"], "near_duplicate_threshold": NEAR_DUP, "robustness_n": robustness_n}
    db.x("INSERT INTO training_runs(id,task,dataset_id,version_id,config,config_hash,plan,status,started_at,actor,device) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (run_id, task_id, specs[0].dataset_id, specs[0].version_id, json.dumps(cfg, default=str), sha256_json(cfg), json.dumps(plan), "running", db.now(), actor, plan["device"]))
    db.audit(actor, "training.start", run_id, {"task": task_id, "experiment": name, "config_sha256": sha256_json(cfg)})
    device = plan["device"]
    try:
        # ---- images and features, per source (cached on disk, resumable)
        Xs, Fd, Fr = {}, {}, {}
        tune = None
        for s in srcs:
            n = s["spec"].name
            Xs[n] = FE.image_cache(s["mf"]["root"], s["entries"], progress=log, should_stop=should_stop)
        stage("image_cache")
        if tune is None:
            first = next(iter(Xs.values()))
            tune = FE.autotune("dinov2_s14", first, device)
            log(f"auto-tuned: {tune['chosen']} (free RAM {tune['free_ram_mb_at_start']} MB); measured {tune['measured']}")
        ch = tune["chosen"]
        for s in srcs:
            n = s["spec"].name
            Fd[n] = FE.extract_features("dinov2_s14", Xs[n], s["entries"], device, batch=ch["batch"], threads=ch["threads"] or None, progress=log, should_stop=should_stop, max_seconds=max_seconds)
        stage("dinov2_features")
        for s in srcs:
            n = s["spec"].name
            Fr[n] = FE.extract_features("resnet18_in1k", Xs[n], s["entries"], device, batch=32, threads=ch["threads"] or None, progress=log, should_stop=should_stop, max_seconds=max_seconds)
        stage("resnet18_features")

        def gather(F, entries):
            return np.stack([F[e["_src"]][e["_row"]] for e in entries]).astype(np.float32) if entries else np.zeros((0, 1), np.float32)

        # ---- leakage audit BEFORE any metric: drop val/test/external images that duplicate a training image
        tr_th = np.concatenate([thumbs(Xs[n], [e["_row"] for e in splits["train"] if e["_src"] == n]) for n in Xs if any(e["_src"] == n for e in splits["train"])])
        removed = {}
        for part in ("val", "test"):
            th = np.concatenate([thumbs(Xs[n], [e["_row"] for e in splits[part] if e["_src"] == n]) for n in Xs if any(e["_src"] == n for e in splits[part])])
            order = [e for n in Xs for e in splits[part] if e["_src"] == n]
            bad = set(near_duplicate_rows(th, tr_th).tolist())
            removed[part] = len(bad)
            splits[part] = [e for i, e in enumerate(order) if i not in bad]
        for n, v in list(external.items()):
            th = thumbs(Xs[n], [e["_row"] for e in v])
            bad = set(near_duplicate_rows(th, tr_th).tolist())
            removed[f"external:{n}"] = len(bad)
            external[n] = [e for i, e in enumerate(v) if i not in bad]
        log(f"near-duplicates of a training image removed before evaluation: {removed}")
        stage("leakage_audit")

        y = {p: np.array([c2i[e["label"]] for e in v]) for p, v in splits.items()}
        Ftr, Fva, Fte = (gather(Fd, splits[p]) for p in ("train", "val", "test"))
        # ---- head selection on validation only
        sel = select_head(Ftr, y["train"], Fva, y["val"], k, log)
        s_ = sel["slice"]
        sc, clf = _scaled_lr(Ftr[:, s_], y["train"], sel["C"])
        Zv = probe.head_logits(sc.transform(Fva[:, s_]), clf.coef_, clf.intercept_)
        Tt = T.fit_temperature(Zv, y["val"])
        Zt = probe.head_logits(sc.transform(Fte[:, s_]), clf.coef_, clf.intercept_)
        P_raw, P = T.softmax(Zt), T.softmax(Zt, Tt)
        stage("head")

        # ---- baselines on the same test set
        counts = np.bincount(y["train"], minlength=k)
        Pm = np.zeros((len(y["test"]), k)); Pm[:, int(counts.argmax())] = 1
        baselines = {"majority_class": {"class": classes[int(counts.argmax())], **_brief(T.full_metrics(Pm, y["test"], classes))}}
        Pk, kk = knn_baseline(Ftr, y["train"], Fva, y["val"], Fte, k)
        baselines["dinov2_knn_cosine"] = {"k": kk, **_brief(T.full_metrics(Pk, y["test"], classes))}
        Rtr, Rva, Rte = (gather(Fr, splits[p]) for p in ("train", "val", "test"))
        scr, lrr = _scaled_lr(Rtr, y["train"], 0.1)
        Pr = np.zeros((len(Rte), k)); Pr[:, lrr.classes_] = lrr.predict_proba(scr.transform(Rte))
        baselines["resnet18_frozen_features_logreg"] = {**_brief(T.full_metrics(Pr, y["test"], classes)), "note": "ImageNet-1k weights: comparison only; their licence status for commercial use is unresolved"}
        for b, v in baselines.items():
            log(f"baseline {b}: macro-F1 {v['macro_f1']}")
        stage("baselines")

        # ---- test metrics
        test = T.full_metrics(P, y["test"], classes, splits["test"])
        test["ece_uncalibrated"] = round(T.ece(P_raw, y["test"]), 3)
        test["temperature"] = round(Tt, 3)
        pred, conf = P.argmax(1), P.max(1)
        test["selective_prediction"] = [{"threshold": t, "coverage": round(float((conf >= t).mean()), 3),
                                        "accuracy_on_accepted": round(float((pred[conf >= t] == y["test"][conf >= t]).mean()), 3) if (conf >= t).any() else None}
                                       for t in (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)]
        crit = list(spec_task.critical_classes)
        test["by_source"] = subgroup_table(splits["test"], y["test"], pred, lambda e: e["_src"], k, classes, crit)
        test["by_identifier_known"] = subgroup_table(splits["test"], y["test"], pred, lambda e: "patient/lesion id known" if not e["_nogroup"] else "no identifier", k, classes, crit)
        if any(e.get("label_provenance", "").startswith("scin") for e in splits["test"]):
            test["case_level"] = _case_level(splits["test"], Zt, Tt, y["test"], classes)
        if crit:
            test["critical_class_sensitivity_by_fitzpatrick"] = subgroup_table(
                splits["test"], y["test"], pred, lambda e: ("not recorded" if str(e.get("fitzpatrick") or "") in ("", "None", "nan") else "I-II" if str(e["fitzpatrick"]) in ("1", "2", "1.0", "2.0") else "III" if str(e["fitzpatrick"]) in ("3", "3.0") else "IV-VI"),
                k, classes, crit)
        stage("test_metrics")

        # ---- robustness on a fixed stratified subset of the test set, and out-of-distribution behaviour
        rng = np.random.default_rng(seed)
        sub = np.sort(rng.choice(len(splits["test"]), size=min(robustness_n, len(splits["test"])), replace=False))
        bb = FE.load_backbone("dinov2_s14", device)
        import cv2

        def corrupt(im, kind):
            if kind == "blur":
                return cv2.GaussianBlur(im, (0, 0), 3)
            if kind == "dark":
                return (im * 0.45).astype(np.uint8)
            if kind == "jpeg20":
                return cv2.imdecode(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 20])[1], 1)
            return np.rot90(im)

        def head_probs(F):
            return T.softmax(probe.head_logits(sc.transform(F[:, s_]), clf.coef_, clf.intercept_), Tt)
        ys = y["test"][sub]
        base_sub = head_probs(Fte[sub]).argmax(1)
        test["robustness_reference"] = {"subset_n": int(len(sub)), "clean": {"macro_f1": round(T.macro_f1(ys, base_sub, k), 3), "balanced_accuracy": round(T.balanced_acc(ys, base_sub, k), 3)},
                                        "note": "the perturbations were applied to a fixed random subset of the test set; compare them with these clean figures on the same images, not with the whole-test-set figures"}
        rob = {}                                                      # same layout as the earlier models: one entry per perturbation
        for kind in ("blur", "dark", "jpeg20", "rotate90"):
            arr = np.stack([corrupt(np.ascontiguousarray(Xs[splits["test"][i]["_src"]][splits["test"][i]["_row"]]), kind) for i in sub])
            Fc = np.concatenate([FE.extract_batch("dinov2_s14", bb, arr[s:s + ch["batch"]], device) for s in range(0, len(arr), ch["batch"])])
            Pc = head_probs(Fc)
            rob[kind] = {"macro_f1": round(T.macro_f1(ys, Pc.argmax(1), k), 3), "balanced_accuracy": round(T.balanced_acc(ys, Pc.argmax(1), k), 3), "mean_confidence": round(float(Pc.max(1).mean()), 3)}
        test["robustness"] = rob
        if ood_dir and os.path.isdir(ood_dir):
            from PIL import Image
            from sklearn.metrics import roc_auc_score
            files = sorted(p for p in os.listdir(ood_dir) if p.lower().endswith((".png", ".jpg", ".jpeg")))
            if files:
                O = np.stack([np.asarray(T._square_resize(Image.open(os.path.join(ood_dir, f)), FE.SIZE)) for f in files])
                Fo = np.concatenate([FE.extract_batch("dinov2_s14", bb, O[s:s + 8], device) for s in range(0, len(O), 8)])
                co, ci_ = head_probs(Fo).max(1), P.max(1)
                test["ood"] = {"n": len(files), "source": "non-skin natural images (scikit-image sample set)", "mean_confidence": round(float(co.mean()), 3), "in_distribution_mean_confidence": round(float(ci_.mean()), 3),
                               "fraction_above_threshold": {str(t): round(float((co >= t).mean()), 3) for t in (0.5, 0.7, 0.9)},
                               "in_distribution_fraction_above_threshold": {str(t): round(float((ci_ >= t).mean()), 3) for t in (0.5, 0.7, 0.9)},
                               "auroc_confidence_separates_in_vs_out": round(float(roc_auc_score(np.r_[np.ones(len(ci_)), np.zeros(len(co))], np.r_[ci_, co])), 3)}
        del bb
        stage("robustness_ood")

        # ---- external sources: scored once, after the model is fixed
        ext = {}
        for n, v in external.items():
            if not v:
                continue
            yv = np.array([c2i[e["label"]] for e in v])
            Pe = head_probs(gather(Fd, v))
            m = T.full_metrics(Pe, yv, classes, v)
            m["per_class_support"] = {c: int((yv == i).sum()) for i, c in enumerate(classes)}
            ext[n] = {**{kk_: m[kk_] for kk_ in ("n", "accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95", "ece", "per_class", "confusion_matrix", "classes", "warnings")},
                      "removed_as_near_duplicate_of_training": removed.get(f"external:{n}", 0), "note": "never used for training, selection or calibration"}
            log(f"external {n}: n={m['n']} macro-F1 {m['macro_f1']} balanced accuracy {m['balanced_accuracy']}")
        if ext:
            test["external"] = ext
        stage("external")

        # ---- artifact, card, registration
        mid = db.uid()
        out_dir = MODEL_DIR / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        apath = out_dir / f"{mid}.npz"
        np.savez(apath, mean=sc.mean_, scale=sc.scale_, coef=clf.coef_, intercept=clf.intercept_, classes=np.array(classes), temperature=Tt)
        spec_bb = FE.BACKBONES["dinov2_s14"]
        prov = json.loads(spec_bb["provenance"].read_text()) if spec_bb["provenance"].exists() else {}
        import psutil
        proc = psutil.Process()
        peak = getattr(proc.memory_info(), "peak_wset", proc.memory_info().rss)
        sizes = {p: len(v) for p, v in splits.items()}
        card = {"model_id": mid, "task": task_id, "experiment": name, "architecture": "dinov2_vit_s14_frozen + logistic_regression_probe", "feature_kind": sel["feature_kind"], "backbone": FE.backbone_id("dinov2_s14"),
                "classes": classes, "dataset_id": specs[0].dataset_id, "dataset_version_id": specs[0].version_id, "dataset_version": srcs[0]["mf"]["version"],
                "sources": [{"name": s["spec"].name, "role": s["spec"].role, "dataset": s["spec"].dataset_id, "version_id": s["spec"].version_id, "version": s["mf"]["version"], "manifest_sha256": s["manifest_sha256"],
                             "images": len(s["entries"]), "split_policy": s["spec"].split_policy, "unknown_group_train_only": s["spec"].unknown_group_train_only, "license": s["license"]} for s in srcs],
                "seed": seed, "device": device, "image_size": FE.SIZE, "finetune": "none (frozen backbone)", "epochs": 0, "lr": None, "batch": None, "selected_C": sel["C"], "best_val_macro_f1": sel["val_macro_f1"],
                "model_selection": sel["candidates"], "split_sizes": sizes, "patients_per_split": {p: len({e["_gkey"] for e in v}) for p, v in splits.items()},
                "class_counts_per_split": {p: dict(sorted({c: int(sum(1 for e in v if e["label"] == c)) for c in classes}.items())) for p, v in splits.items()},
                "split_notes": split_notes, "excluded_labels_not_in_class_set": excluded_labels,
                "near_duplicates_vs_train": {"val": 0, "test": 0}, "near_duplicates_removed_before_evaluation": removed,
                "split_hash": hashlib.sha256(json.dumps({p: [e["sha256"] for e in v] for p, v in splits.items()}).encode()).hexdigest(),
                "pretrained_weights": str(spec_bb["weights"]), "pretrained_sha256": T.sha256_file(spec_bb["weights"]), "pretrained_provenance": json.dumps(prov), "baselines": baselines, "history": [],
                "test_metrics": test, "training_seconds": round(time.time() - t_start), "artifact_path": str(apath), "artifact_sha256": T.sha256_file(apath), "code_revision": T.code_revision(),
                "dataset_license": next((s["license"] for s in srcs if s["spec"].role == "train"), {}), "dataset_licenses": [s["license"] for s in srcs],
                "intended_use": intended_use, "approval": None, "evidence_class": "public_research_dataset", "log": log_lines[-60:], "limitations": limitations,
                "serving": {"quality_gate": quality_gate, "warnings": serving_warnings},
                "resources": {"hardware": {"cpu": platform.processor(), "logical_cpus": os.cpu_count(), "ram_total_mb": round(psutil.virtual_memory().total / 1e6), "gpu": "none"}, "device": device, "autotune": tune,
                              "stage_seconds": stages, "peak_working_set_mb": round(peak / 1e6), "artifact_bytes": os.path.getsize(apath), "backbone_bytes": os.path.getsize(spec_bb["weights"]),
                              "precision": tune["precision"], "feature_store": "data/features (not in Git)"},
                "pipeline": {"version": "featuretrain-1.0.0", "run_id": run_id, "config_sha256": sha256_json(cfg)}}
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (mid, task_id, json.dumps(card, default=str), "research_only", db.now()))
        from ..ai import lifecycle
        lifecycle.registered(mid, task_id, actor, card)
        gates = T.evaluate_gates(card)
        (out_dir / f"{mid}.model-card.md").write_text(cards.model_card_markdown(card, gates, "experimental", "public_research_dataset"), encoding="utf-8")
        db.x("UPDATE training_runs SET status='done', finished_at=?, model_id=? WHERE id=?", (db.now(), mid, run_id))
        db.audit(actor, "model.train", mid, {"task": task_id, "method": "feature_probe", "experiment": name, "test_macro_f1": test["macro_f1"], "code": card["code_revision"]})
        log(f"{name}: model {mid} macro-F1 {test['macro_f1']} (95% CI {test['macro_f1_ci95']}), balanced accuracy {test['balanced_accuracy']}, ECE {test['ece']}; gates passed {sum(g['passed'] for g in gates)}/{len(gates)}")
        return card
    except FE.Paused as e:
        db.x("UPDATE training_runs SET status='stopped', finished_at=?, error=? WHERE id=?", (db.now(), f"paused: {e}. The cached progress is kept; run the same experiment again to continue.", run_id))
        raise
    except BaseException as e:
        db.x("UPDATE training_runs SET status='failed', finished_at=?, error=? WHERE id=?", (db.now(), f"{type(e).__name__}: {str(e)[:300]}", run_id))
        raise


def _case_level(entries: list[dict], Z: np.ndarray, Tt: float, y: np.ndarray, classes: list[str]) -> dict:
    """Average the logits of a case's images (a SCIN case has up to three photographs) and score the case once."""
    by: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        by.setdefault(e["_gkey"], []).append(i)
    keys = sorted(by)
    Zc = np.stack([Z[by[g]].mean(0) for g in keys])
    yc = np.array([y[by[g][0]] for g in keys])
    Pc = T.softmax(Zc, Tt)
    ent = [entries[by[g][0]] for g in keys]
    m = T.full_metrics(Pc, yc, classes, ent)
    return {k_: m[k_] for k_ in ("n", "accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95", "ece", "subgroups_by_fitzpatrick")} | {"unit": "case (logits of its photographs averaged)"}

"""Linear-probe training on frozen self-supervised features (DINOv2 ViT-S/14 via timm, loaded from a LOCAL file).

Same protocol as trainer.train: patient-level stratified splits, baselines, validation-based model selection (regularisation strength),
temperature scaling on validation, a single test evaluation with CIs, subgroups, robustness, out-of-distribution separation. The artifact is
the head only (scaler + logistic-regression weights); the backbone is referenced by file hash."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from .. import db
from ..config import MODEL_DIR
from . import trainer as T


def load_backbone(weights_file: str, name: str = "vit_small_patch14_dinov2.lvd142m", size: int = 224):
    import timm
    m = timm.create_model(name, pretrained=True, pretrained_cfg_overlay=dict(file=os.path.abspath(weights_file)), num_classes=0, img_size=size)
    return m.eval()


def head_logits(X, coef, intercept):
    """Linear-head logits as [n, n_classes]. A binary scikit-learn model stores ONE weight row (score for class 1), so expand it to two columns."""
    z = X @ np.asarray(coef).T + np.asarray(intercept)
    return np.concatenate([-z / 2, z / 2], axis=1) if z.shape[1] == 1 else z


def extract(model, X, device, batch=32):
    import torch
    model.to(device)
    out = []
    with torch.no_grad():
        for s in range(0, len(X), batch):
            out.append(model(T._to_tensor(np.asarray(X[s:s + batch])).to(device)).cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, model.num_features))


def train_probe(task_id: str, dataset_id: str, version_id: str, actor: str, weights_file: str, provenance: dict, seed: int = 17, image_size: int = 224,
                progress=None, ood_dir: str | None = None, license_info: dict | None = None, resnet_baseline: str | None = None) -> dict:
    import torch
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    log_lines: list[str] = []

    def log(m):
        log_lines.append(f"{time.strftime('%H:%M:%S')} {m}")
        if progress:
            progress({"log": log_lines[-1]})
    np.random.seed(seed)
    row = db.q1("SELECT manifest FROM dataset_versions WHERE id=? AND dataset_id=?", (version_id, dataset_id))
    mf = json.loads(row["manifest"])
    entries = [e for e in mf["entries"] if e.get("label")]
    splits = T.stratified_group_split(entries, seed)
    classes = sorted({e["label"] for e in entries})
    c2i = {c: i for i, c in enumerate(classes)}
    y = {k: np.array([c2i[e["label"]] for e in v]) for k, v in splits.items()}
    X = {k: T.build_cache(mf["root"], v, image_size, log) for k, v in splits.items()}
    pairs = {s: T.near_duplicate_pairs(X[s], X["train"]) for s in ("val", "test")}
    leak = {s: len({i for i, _, _ in pairs[s]}) for s in pairs}
    log(f"splits train/val/test = {[len(v) for v in splits.values()]}; near-duplicates vs train (thumbnail corr): {leak}")

    device = T.pick_device()
    t0 = time.time()
    bb = load_backbone(weights_file, size=image_size)
    F = {}
    for k in ("train", "val", "test"):
        F[k] = extract(bb, X[k], device)
        log(f"features {k}: {F[k].shape} ({time.time() - t0:.0f}s)")
    counts = np.bincount(y["train"], minlength=len(classes))

    baselines = {}
    maj = int(counts.argmax()); Pm = np.zeros((len(y["test"]), len(classes))); Pm[:, maj] = 1
    baselines["majority_class"] = {k: v for k, v in T.full_metrics(Pm, y["test"], classes).items() if k in ("accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95")}
    if resnet_baseline:
        import torchvision.models as tvm
        import torch.nn as nn
        r = tvm.resnet18(weights=None); r.load_state_dict(torch.load(resnet_baseline, map_location="cpu", weights_only=True)); r.fc = nn.Identity()
        R = {k: T.predict_logits(r, X[k], device) for k in ("train", "val", "test")}
        sc = StandardScaler().fit(R["train"])
        lr_ = LogisticRegression(max_iter=3000, class_weight="balanced", C=0.1).fit(sc.transform(R["train"]), y["train"])
        Pb = lr_.predict_proba(sc.transform(R["test"]))
        baselines["resnet18_frozen_features_logreg"] = {k: v for k, v in T.full_metrics(Pb, y["test"], classes).items() if k in ("accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95", "ece")}
        log(f"baseline resnet18 frozen+logreg macro-F1 {baselines['resnet18_frozen_features_logreg']['macro_f1']}")

    # model selection on VALIDATION only
    scaler = StandardScaler().fit(F["train"])
    best = (-1.0, None, None)
    for C in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0):
        clf = LogisticRegression(max_iter=4000, class_weight="balanced", C=C).fit(scaler.transform(F["train"]), y["train"])
        vf1 = T.macro_f1(y["val"], clf.predict(scaler.transform(F["val"])), len(classes))
        log(f"C={C}: val macro-F1 {vf1:.3f}")
        if vf1 > best[0]:
            best = (vf1, C, clf)
    vf1, C, clf = best
    Zv = head_logits(scaler.transform(F["val"]), clf.coef_, clf.intercept_)
    Tt = T.fit_temperature(Zv, y["val"])
    Zt = head_logits(scaler.transform(F["test"]), clf.coef_, clf.intercept_)
    P_raw, P = T.softmax(Zt), T.softmax(Zt, Tt)
    test = T.full_metrics(P, y["test"], classes, splits["test"])
    test["ece_uncalibrated"] = round(T.ece(P_raw, y["test"]), 3); test["temperature"] = round(Tt, 3)
    conf, pred = P.max(1), P.argmax(1)
    test["selective_prediction"] = [{"threshold": t, "coverage": round(float((conf >= t).mean()), 3),
                                     "accuracy_on_accepted": round(float((pred[conf >= t] == y["test"][conf >= t]).mean()), 3) if (conf >= t).any() else None}
                                    for t in (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)]
    import cv2

    def corrupt(kind):
        out = np.empty((len(X["test"]), image_size, image_size, 3), np.uint8)
        for i, im in enumerate(np.asarray(X["test"])):
            if kind == "blur":
                im = cv2.GaussianBlur(im, (0, 0), 3)
            elif kind == "dark":
                im = (im * 0.45).astype(np.uint8)
            elif kind == "jpeg20":
                im = cv2.imdecode(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 20])[1], 1)
            elif kind == "rotate90":
                im = np.rot90(im)
            out[i] = im
        return out
    rob = {}
    for kind in ("blur", "dark", "jpeg20", "rotate90"):
        Pc = T.softmax(head_logits(scaler.transform(extract(bb, corrupt(kind), device)), clf.coef_, clf.intercept_), Tt)
        rob[kind] = {"macro_f1": round(T.macro_f1(y["test"], Pc.argmax(1), len(classes)), 3), "balanced_accuracy": round(T.balanced_acc(y["test"], Pc.argmax(1), len(classes)), 3),
                     "mean_confidence": round(float(Pc.max(1).mean()), 3)}
    test["robustness"] = rob
    if ood_dir and Path(ood_dir).is_dir():
        from PIL import Image
        from sklearn.metrics import roc_auc_score
        files = sorted(p for p in Path(ood_dir).iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        O = np.stack([np.asarray(T._square_resize(Image.open(p), image_size)) for p in files])
        Po = T.softmax(head_logits(scaler.transform(extract(bb, O, device)), clf.coef_, clf.intercept_), Tt)
        co, ci_ = Po.max(1), P.max(1)
        test["ood"] = {"n": len(files), "source": "non-lesion natural images supplied by the administrator", "mean_confidence": round(float(co.mean()), 3),
                       "in_distribution_mean_confidence": round(float(ci_.mean()), 3),
                       "fraction_above_threshold": {str(t): round(float((co >= t).mean()), 3) for t in (0.5, 0.7, 0.9)},
                       "in_distribution_fraction_above_threshold": {str(t): round(float((ci_ >= t).mean()), 3) for t in (0.5, 0.7, 0.9)},
                       "auroc_confidence_separates_in_vs_out": round(float(roc_auc_score(np.r_[np.ones(len(ci_)), np.zeros(len(co))], np.r_[ci_, co])), 3)}

    mid = db.uid()
    out_dir = MODEL_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    apath = out_dir / f"{mid}.npz"
    np.savez(apath, mean=scaler.mean_, scale=scaler.scale_, coef=clf.coef_, intercept=clf.intercept_, classes=np.array(classes), temperature=Tt)
    card = {"model_id": mid, "task": task_id, "architecture": "dinov2_vit_s14_frozen + logistic_regression_probe", "classes": classes, "dataset_id": dataset_id,
            "dataset_version_id": version_id, "dataset_version": mf["version"], "seed": seed, "device": device, "image_size": image_size, "finetune": "none (frozen backbone)",
            "epochs": 0, "lr": None, "batch": None, "selected_C": C, "best_val_macro_f1": round(vf1, 4),
            "split_sizes": {k: len(v) for k, v in splits.items()}, "patients_per_split": {k: len({e["group_id"] for e in v}) for k, v in splits.items()},
            "near_duplicates_vs_train": leak, "split_hash": hashlib.sha256(json.dumps({k: [e["sha256"] for e in v] for k, v in splits.items()}).encode()).hexdigest(),
            "pretrained_weights": weights_file, "pretrained_sha256": T.sha256_file(weights_file), "pretrained_provenance": json.dumps(provenance),
            "baselines": baselines, "history": [], "test_metrics": test, "training_seconds": round(time.time() - t0), "artifact_path": str(apath),
            "artifact_sha256": T.sha256_file(apath), "code_revision": T.code_revision(), "dataset_license": license_info or {},
            "intended_use": "Research only. Not clinically validated. Not offered to patients.", "approval": None, "log": log_lines[-60:],
            "limitations": ["Small-sample metrics are unreliable", "Dataset demographics may not represent target patients"]}
    db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (mid, task_id, json.dumps(card), "research_only", db.now()))
    db.audit(actor, "model.train", mid, {"task": task_id, "method": "linear_probe", "test_macro_f1": test["macro_f1"], "code": card["code_revision"]})
    return card

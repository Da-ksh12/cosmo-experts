"""Phase 5 comparison arm: a ResNet18 (ImageNet-1k) partial fine-tune, trained and scored on EXACTLY the sources, subject-level splits and leakage audit of a feature-probe experiment.

Why it exists: on this CPU-only machine the frozen-feature probes (`featuretrain.py`) are the primary method. Whether partially fine-tuning a CNN would do better is an empirical question that
has to be answered on the same images, so the choice of method rests on a measurement and not on a belief. Everything that makes the probe numbers believable applies here too: the same
licence-gated sources, the same deterministic splits (so the test set is the same images), the same near-duplicate removal before any metric, epoch and temperature chosen on validation only,
the test set and the external sources scored once, thresholds from `trainer.GATES` untouched. The DINOv2 probe, the frozen-ResNet18 classifier, a k-nearest-neighbour classifier and the majority
class are re-computed from the cached features on the same test set and stored as baselines, so one model card holds the whole comparison.

Resources: layer4 and the new classifier are trained (the earlier fine-tune recipe, `trainer.train`), the rest is frozen; training is resumable from a checkpoint written after every epoch, stops
safely when the time limit is reached, and runs on the CPU unless a verified GPU is requested (mixed precision is NOT used for training here; it is used only for feature extraction on CUDA).
The ImageNet-1k weights have an unresolved licence status for commercial use (docs/LICENSES.md): a model made here is a COMPARISON and carries that statement."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import time

import numpy as np

from .. import db
from ..ai.evaluation.common import sha256_json
from ..ai.resources import available_mb
from ..config import MODEL_DIR
from . import cards, features as FE, featuretrain as FT, probe, task_specs, tasks, trainer as T

IMAGENET_NOTE = ("Fine-tuned from ImageNet-1k weights: their licence for commercial use is unresolved, so this model is a comparison and must not be approved for any use "
                 "before a legal review (docs/LICENSES.md).")


class View:
    """Indexable view of the cached images of a list of entries (view[a:b] -> uint8 [n,224,224,3]); what `trainer.predict_logits` needs, without copying every image up front."""

    def __init__(self, Xs: dict, entries: list[dict]):
        self.Xs, self.entries = Xs, entries

    def __len__(self):
        return len(self.entries)

    def _img(self, e):
        return np.asarray(self.Xs[e["_src"]][e["_row"]])

    def take(self, idx) -> np.ndarray:
        return np.stack([self._img(self.entries[int(i)]) for i in idx])

    def __getitem__(self, i):
        if isinstance(i, slice):
            sel = self.entries[i]
            return np.stack([self._img(e) for e in sel]) if sel else np.zeros((0, FE.SIZE, FE.SIZE, 3), np.uint8)
        return self._img(self.entries[i])


def run(task_id: str, name: str, specs: list[FT.SourceSpec], actor: str, *, seed: int = 17, epochs: int = 4, lr: float = 1e-3, batch: int = 16, intended_use: str, limitations: list[str],
        serving_warnings: list[str], quality_gate: str = "none", robustness_n: int = 300, ood_dir: str | None = None, max_seconds: float | None = None, progress=None, should_stop=None) -> dict:
    import torch
    import torch.nn as nn
    spec_task = task_specs.get(task_id)
    if spec_task is None or tasks.get_task(task_id) is None:
        raise FT.Blocked([f"unknown task {task_id}"])
    wspec = FE.BACKBONES["resnet18_in1k"]
    if not wspec["weights"].is_file():
        raise FT.Blocked(["the ResNet18 ImageNet weights are not present on this machine"])
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

    srcs = [FT.load_source(s) for s in specs]
    splits, external, split_notes = FT.assemble(srcs, seed)
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
    log(f"{name}: task {task_id}; classes {classes}; images train/val/test = {[len(splits[s]) for s in ('train', 'val', 'test')]}; external { {n: len(v) for n, v in external.items()} }")
    if min(len(splits["train"]), len(splits["val"]), len(splits["test"])) == 0:
        raise FT.Blocked(["an empty train, validation or test split"])

    plan = FT.plan_run(sum(len(s["entries"]) for s in srcs), specs)
    run_id = db.uid()
    threads = max(1, min(4, os.cpu_count() or 2)) if available_mb() > 1000 else 2
    cfg = {"experiment": name, "method": "resnet18_layer4_finetune", "task": task_id, "sources": [s.__dict__ for s in specs], "seed": seed, "epochs": epochs, "lr": lr, "batch": batch,
           "backbone": "resnet18_in1k", "trainable": "layer4 + classifier", "loss": "cross-entropy, sqrt-inverse-frequency class weights, label smoothing 0.1", "optimiser": "AdamW, one-cycle schedule",
           "augmentation": "trainer._augment (flips, rotation, scale, brightness)", "baselines": ["majority", "dinov2 probe", "dinov2 kNN", "resnet18_in1k frozen + logistic regression"],
           "near_duplicate_threshold": FT.NEAR_DUP, "robustness_n": robustness_n, "threads": threads}
    db.x("INSERT INTO training_runs(id,task,dataset_id,version_id,config,config_hash,plan,status,started_at,actor,device) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (run_id, task_id, specs[0].dataset_id, specs[0].version_id, json.dumps(cfg, default=str), sha256_json(cfg), json.dumps(plan), "running", db.now(), actor, plan["device"]))
    db.audit(actor, "training.start", run_id, {"task": task_id, "experiment": name, "method": "resnet18_layer4_finetune", "config_sha256": sha256_json(cfg)})
    device = plan["device"]
    try:
        torch.set_num_threads(threads)
        Xs = {s["spec"].name: FE.image_cache(s["mf"]["root"], s["entries"], progress=log, should_stop=should_stop) for s in srcs}
        stage("image_cache")

        # ---- leakage audit BEFORE any metric: identical to the probe's (same thumbnails, same threshold), so both methods are scored on the same images
        def thumbs_of(es):
            return np.concatenate([FT.thumbs(Xs[n], [e["_row"] for e in es if e["_src"] == n]) for n in Xs if any(e["_src"] == n for e in es)])
        tr_th = thumbs_of(splits["train"])
        removed = {}
        for part in ("val", "test"):
            order = [e for n in Xs for e in splits[part] if e["_src"] == n]
            bad = set(FT.near_duplicate_rows(thumbs_of(splits[part]), tr_th).tolist())
            removed[part] = len(bad)
            splits[part] = [e for i, e in enumerate(order) if i not in bad]
        for n, v in list(external.items()):
            bad = set(FT.near_duplicate_rows(FT.thumbs(Xs[n], [e["_row"] for e in v]), tr_th).tolist())
            removed[f"external:{n}"] = len(bad)
            external[n] = [e for i, e in enumerate(v) if i not in bad]
        log(f"near-duplicates of a training image removed before evaluation: {removed}")
        stage("leakage_audit")
        y = {p: np.array([c2i[e["label"]] for e in v]) for p, v in splits.items()}
        views = {p: View(Xs, v) for p, v in splits.items()}
        split_hash = hashlib.sha256(json.dumps({p: [e["sha256"] for e in v] for p, v in splits.items()}).encode()).hexdigest()
        wsha = T.sha256_file(wspec["weights"])

        # ---- training (resumable: a checkpoint after every epoch, verified before it is loaded)
        model = T.build_model("resnet18", k, str(wspec["weights"]), "layer4").to(device)
        counts = np.bincount(y["train"], minlength=k)
        cw = torch.tensor((counts.sum() / np.maximum(counts, 1)) ** 0.5, dtype=torch.float32)
        lossf = nn.CrossEntropyLoss(weight=(cw / cw.mean()).to(device), label_smoothing=0.1)
        opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr, weight_decay=1e-4)
        steps_per_epoch = max(1, (len(y["train"]) + batch - 1) // batch)
        sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=epochs * steps_per_epoch)
        rng = np.random.default_rng(seed)
        torch.manual_seed(seed)
        stable = {kk: v for kk, v in cfg.items() if kk != "threads"}                 # the thread count follows the free memory of the moment and must not change the identity of a run
        ckpt = T.Checkpoint(T.CHECKPOINT_DIR, T.fingerprint({"experiment": name, "task": task_id, "cfg": sha256_json(stable), "split_hash": split_hash, "weights": wsha}))
        history, best, best_state, step, start_ep, trained_s, resumed = [], -1.0, None, 0, 0, 0.0, None
        st = ckpt.load()
        if st is not None:
            model.load_state_dict(st["model"]); opt.load_state_dict(st["opt"]); sched.load_state_dict(st["sched"])
            rng.bit_generator.state = st["np_rng"]; torch.set_rng_state(st["torch_rng"])
            history, best, best_state, step, start_ep, trained_s = st["history"], st["best"], st["best_state"], st["step"], st["epoch"], st["trained_s"]
            resumed = {"from_epoch": start_ep, "checkpoint_saved_at": st["saved_at"]}
            log(f"resumed from the checkpoint after epoch {start_ep}/{epochs}")
        t0 = time.time()
        for ep in range(start_ep, epochs):
            T._set_train(model)
            order = rng.permutation(len(y["train"]))
            tot, n, te = 0.0, 0, time.time()
            for s0 in range(0, len(order), batch):
                if should_stop and should_stop():
                    raise FE.Paused("stop requested (progress inside the unfinished epoch is not kept)", ep, epochs)
                idx = np.sort(order[s0:s0 + batch])
                xb = T._to_tensor(T._augment(views["train"].take(idx), rng)).to(device)
                yb = torch.from_numpy(y["train"][idx]).to(device)
                opt.zero_grad()
                loss = lossf(model(xb), yb)
                loss.backward(); opt.step(); sched.step()
                tot += float(loss) * len(idx); n += len(idx); step += 1
                if step % 100 == 0:
                    log(f"epoch {ep + 1}/{epochs}: {n}/{len(order)} images, loss {tot / n:.3f}, {n / (time.time() - te):.1f} img/s")
            Lv = T.predict_logits(model, views["val"], device, batch=32)
            vf1 = T.macro_f1(y["val"], Lv.argmax(1), k)
            history.append({"epoch": ep + 1, "train_loss": round(tot / max(n, 1), 4), "val_macro_f1": round(vf1, 4), "val_accuracy": round(float((Lv.argmax(1) == y["val"]).mean()), 4),
                            "elapsed_s": round(trained_s + time.time() - t0)})
            log(f"epoch {ep + 1}/{epochs}: loss {history[-1]['train_loss']} validation macro-F1 {vf1:.3f} ({time.time() - te:.0f} s)")
            if vf1 > best:
                best, best_state = vf1, {kk: v.detach().cpu().clone() for kk, v in model.state_dict().items()}
            ckpt.save({"epoch": ep + 1, "model": model.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(), "np_rng": rng.bit_generator.state, "torch_rng": torch.get_rng_state(),
                       "history": history, "best": best, "best_state": best_state, "step": step, "trained_s": trained_s + time.time() - t0, "code_revision": T.code_revision(), "saved_at": time.time(),
                       "meta": {"task": task_id, "experiment": name, "epochs": epochs}})
            if max_seconds and time.time() - t0 > max_seconds and ep + 1 < epochs:
                raise FE.Paused(f"time limit of {max_seconds:.0f} s reached after a complete epoch (checkpoint kept)", ep + 1, epochs)
        trained_s += time.time() - t0
        model.load_state_dict(best_state)
        stage("training")

        # ---- calibration on validation, then the single test evaluation
        Lv = T.predict_logits(model, views["val"], device, batch=32)
        Tt = T.fit_temperature(Lv, y["val"])
        Lt = T.predict_logits(model, views["test"], device, batch=32)
        P_raw, P = T.softmax(Lt), T.softmax(Lt, Tt)
        stage("head")

        # ---- baselines on the same test set, from the cached features (computed once per dataset version, shared with the probe experiments)
        Fd = {s["spec"].name: FE.extract_features("dinov2_s14", Xs[s["spec"].name], s["entries"], device, progress=log, should_stop=should_stop) for s in srcs}
        Fr = {s["spec"].name: FE.extract_features("resnet18_in1k", Xs[s["spec"].name], s["entries"], device, batch=32, progress=log, should_stop=should_stop) for s in srcs}

        def gather(F, entries):
            return np.stack([F[e["_src"]][e["_row"]] for e in entries]).astype(np.float32)
        Ftr, Fva, Fte = (gather(Fd, splits[p]) for p in ("train", "val", "test"))
        pm = np.zeros((len(y["test"]), k)); pm[:, int(counts.argmax())] = 1
        baselines = {"majority_class": {"class": classes[int(counts.argmax())], **FT._brief(T.full_metrics(pm, y["test"], classes))}}
        sel = FT.select_head(Ftr, y["train"], Fva, y["val"], k)
        sc, clf = FT._scaled_lr(Ftr[:, sel["slice"]], y["train"], sel["C"])
        Pp = T.softmax(probe.head_logits(sc.transform(Fte[:, sel["slice"]]), clf.coef_, clf.intercept_), T.fit_temperature(probe.head_logits(sc.transform(Fva[:, sel["slice"]]), clf.coef_, clf.intercept_), y["val"]))
        baselines["dinov2_frozen_probe"] = {"feature_kind": sel["feature_kind"], "C": sel["C"], **FT._brief(T.full_metrics(Pp, y["test"], classes)), "note": "re-computed here from the cached features on this test set"}
        Pk, kk = FT.knn_baseline(Ftr, y["train"], Fva, y["val"], Fte, k)
        baselines["dinov2_knn_cosine"] = {"k": kk, **FT._brief(T.full_metrics(Pk, y["test"], classes))}
        Rtr, Rte = gather(Fr, splits["train"]), gather(Fr, splits["test"])
        scr, lrr = FT._scaled_lr(Rtr, y["train"], 0.1)
        Pr = np.zeros((len(Rte), k)); Pr[:, lrr.classes_] = lrr.predict_proba(scr.transform(Rte))
        baselines["resnet18_frozen_features_logreg"] = {**FT._brief(T.full_metrics(Pr, y["test"], classes)), "note": "same ImageNet-1k backbone, not fine-tuned"}
        for b, v in baselines.items():
            log(f"baseline {b}: macro-F1 {v['macro_f1']}")
        stage("baselines")

        # ---- test metrics (same tables as the probe's card)
        test = T.full_metrics(P, y["test"], classes, splits["test"])
        test["ece_uncalibrated"] = round(T.ece(P_raw, y["test"]), 3)
        test["temperature"] = round(Tt, 3)
        pred, conf = P.argmax(1), P.max(1)
        test["selective_prediction"] = [{"threshold": t, "coverage": round(float((conf >= t).mean()), 3),
                                        "accuracy_on_accepted": round(float((pred[conf >= t] == y["test"][conf >= t]).mean()), 3) if (conf >= t).any() else None}
                                       for t in (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)]
        crit = list(spec_task.critical_classes)
        test["by_source"] = FT.subgroup_table(splits["test"], y["test"], pred, lambda e: e["_src"], k, classes, crit)
        test["by_identifier_known"] = FT.subgroup_table(splits["test"], y["test"], pred, lambda e: "patient/lesion id known" if not e["_nogroup"] else "no identifier", k, classes, crit)
        if any(e.get("label_provenance", "").startswith("scin") for e in splits["test"]):
            test["case_level"] = FT._case_level(splits["test"], Lt, Tt, y["test"], classes)
        if crit:
            test["critical_class_sensitivity_by_fitzpatrick"] = FT.subgroup_table(
                splits["test"], y["test"], pred, lambda e: ("not recorded" if str(e.get("fitzpatrick") or "") in ("", "None", "nan") else "I-II" if str(e["fitzpatrick"]) in ("1", "2", "1.0", "2.0") else "III" if str(e["fitzpatrick"]) in ("3", "3.0") else "IV-VI"),
                k, classes, crit)
        stage("test_metrics")

        # ---- robustness on a fixed random subset of the test set, and out-of-distribution behaviour
        import cv2
        sub = np.sort(np.random.default_rng(seed).choice(len(splits["test"]), size=min(robustness_n, len(splits["test"])), replace=False))
        ys = y["test"][sub]
        test["robustness_reference"] = {"subset_n": int(len(sub)), "clean": {"macro_f1": round(T.macro_f1(ys, pred[sub], k), 3), "balanced_accuracy": round(T.balanced_acc(ys, pred[sub], k), 3)},
                                        "note": "the perturbations were applied to a fixed random subset of the test set; compare them with these clean figures on the same images, not with the whole-test-set figures"}

        def corrupt(im, kind):
            if kind == "blur":
                return cv2.GaussianBlur(im, (0, 0), 3)
            if kind == "dark":
                return (im * 0.45).astype(np.uint8)
            if kind == "jpeg20":
                return cv2.imdecode(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 20])[1], 1)
            return np.rot90(im)
        rob = {}
        for kind in ("blur", "dark", "jpeg20", "rotate90"):
            arr = np.stack([corrupt(np.ascontiguousarray(views["test"][int(i)]), kind) for i in sub])
            Pc = T.softmax(T.predict_logits(model, arr, device, batch=32), Tt)
            rob[kind] = {"macro_f1": round(T.macro_f1(ys, Pc.argmax(1), k), 3), "balanced_accuracy": round(T.balanced_acc(ys, Pc.argmax(1), k), 3), "mean_confidence": round(float(Pc.max(1).mean()), 3)}
        test["robustness"] = rob
        if ood_dir and os.path.isdir(ood_dir):
            from PIL import Image
            from sklearn.metrics import roc_auc_score
            files = sorted(p for p in os.listdir(ood_dir) if p.lower().endswith((".png", ".jpg", ".jpeg")))
            if files:
                O = np.stack([np.asarray(T._square_resize(Image.open(os.path.join(ood_dir, f)), FE.SIZE)) for f in files])
                co, ci_ = T.softmax(T.predict_logits(model, O, device, batch=32), Tt).max(1), conf
                test["ood"] = {"n": len(files), "source": "non-skin natural images (scikit-image sample set)", "mean_confidence": round(float(co.mean()), 3), "in_distribution_mean_confidence": round(float(ci_.mean()), 3),
                               "fraction_above_threshold": {str(t): round(float((co >= t).mean()), 3) for t in (0.5, 0.7, 0.9)},
                               "in_distribution_fraction_above_threshold": {str(t): round(float((ci_ >= t).mean()), 3) for t in (0.5, 0.7, 0.9)},
                               "auroc_confidence_separates_in_vs_out": round(float(roc_auc_score(np.r_[np.ones(len(ci_)), np.zeros(len(co))], np.r_[ci_, co])), 3)}
        stage("robustness_ood")

        # ---- external sources: scored once, after the model is fixed
        ext = {}
        for n, v in external.items():
            if not v:
                continue
            yv = np.array([c2i[e["label"]] for e in v])
            m = T.full_metrics(T.softmax(T.predict_logits(model, View(Xs, v), device, batch=32), Tt), yv, classes, v)
            m["per_class_support"] = {c: int((yv == i).sum()) for i, c in enumerate(classes)}
            ext[n] = {**{kk_: m[kk_] for kk_ in ("n", "accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95", "ece", "per_class", "confusion_matrix", "classes", "warnings")},
                      "removed_as_near_duplicate_of_training": removed.get(f"external:{n}", 0), "note": "never used for training, selection or calibration"}
            log(f"external {n}: n={m['n']} macro-F1 {m['macro_f1']} balanced accuracy {m['balanced_accuracy']}")
        if ext:
            test["external"] = ext
        stage("external")

        # ---- artifact, card, registration (the artifact is the layout `serving` already loads for CNNs)
        mid = db.uid()
        out_dir = MODEL_DIR / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        apath = out_dir / f"{mid}.pt"
        torch.save(model.state_dict(), apath)
        prov = json.loads(wspec["provenance"].read_text()) if wspec["provenance"].exists() else {}
        import psutil
        proc = psutil.Process()
        peak = getattr(proc.memory_info(), "peak_wset", proc.memory_info().rss)
        card = {"model_id": mid, "task": task_id, "experiment": name, "architecture": "resnet18", "classes": classes, "dataset_id": specs[0].dataset_id, "dataset_version_id": specs[0].version_id,
                "dataset_version": srcs[0]["mf"]["version"],
                "sources": [{"name": s["spec"].name, "role": s["spec"].role, "dataset": s["spec"].dataset_id, "version_id": s["spec"].version_id, "version": s["mf"]["version"], "manifest_sha256": s["manifest_sha256"],
                             "images": len(s["entries"]), "split_policy": s["spec"].split_policy, "unknown_group_train_only": s["spec"].unknown_group_train_only, "license": s["license"]} for s in srcs],
                "seed": seed, "device": device, "image_size": FE.SIZE, "finetune": "layer4", "epochs": epochs, "lr": lr, "batch": batch, "class_weights": "sqrt inverse frequency (train)", "label_smoothing": 0.1,
                "split_sizes": {p: len(v) for p, v in splits.items()}, "patients_per_split": {p: len({e["_gkey"] for e in v}) for p, v in splits.items()},
                "class_counts_per_split": {p: dict(sorted({c: int(sum(1 for e in v if e["label"] == c)) for c in classes}.items())) for p, v in splits.items()},
                "split_notes": split_notes, "excluded_labels_not_in_class_set": excluded_labels, "near_duplicates_vs_train": {"val": 0, "test": 0}, "near_duplicates_removed_before_evaluation": removed,
                "split_hash": split_hash, "resumed": resumed, "pretrained_weights": str(wspec["weights"]), "pretrained_sha256": wsha, "pretrained_provenance": json.dumps(prov),
                "weights_provenance_note": IMAGENET_NOTE, "baselines": baselines, "history": history, "best_val_macro_f1": round(best, 4), "test_metrics": test, "training_seconds": round(trained_s),
                "artifact_path": str(apath), "artifact_sha256": T.sha256_file(apath), "code_revision": T.code_revision(),
                "dataset_license": next((s["license"] for s in srcs if s["spec"].role == "train"), {}), "dataset_licenses": [s["license"] for s in srcs],
                "intended_use": intended_use, "approval": None, "evidence_class": "public_research_dataset", "log": log_lines[-60:], "limitations": limitations + [IMAGENET_NOTE],
                "serving": {"quality_gate": quality_gate, "warnings": serving_warnings},
                "resources": {"hardware": {"cpu": platform.processor(), "logical_cpus": os.cpu_count(), "ram_total_mb": round(psutil.virtual_memory().total / 1e6), "gpu": "none" if device == "cpu" else device},
                              "device": device, "threads": threads, "stage_seconds": stages, "peak_working_set_mb": round(peak / 1e6), "artifact_bytes": os.path.getsize(apath), "backbone_bytes": os.path.getsize(wspec["weights"]),
                              "precision": "fp32 (no mixed precision in fine-tuning)", "feature_store": "data/features (not in Git)"},
                "pipeline": {"version": "finetune-1.0.0", "run_id": run_id, "config_sha256": sha256_json(cfg)}}
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (mid, task_id, json.dumps(card, default=str), "research_only", db.now()))
        from ..ai import lifecycle
        lifecycle.registered(mid, task_id, actor, card)
        gates = T.evaluate_gates(card)
        (out_dir / f"{mid}.model-card.md").write_text(cards.model_card_markdown(card, gates, "experimental", "public_research_dataset"), encoding="utf-8")
        ckpt.discard()                                                      # a finished run needs no checkpoint
        db.x("UPDATE training_runs SET status='done', finished_at=?, model_id=? WHERE id=?", (db.now(), mid, run_id))
        db.audit(actor, "model.train", mid, {"task": task_id, "method": "resnet18_layer4_finetune", "experiment": name, "test_macro_f1": test["macro_f1"], "code": card["code_revision"]})
        log(f"{name}: model {mid} macro-F1 {test['macro_f1']} (95% CI {test['macro_f1_ci95']}), balanced accuracy {test['balanced_accuracy']}, ECE {test['ece']}; gates passed {sum(g['passed'] for g in gates)}/{len(gates)}")
        return card
    except FE.Paused as e:
        db.x("UPDATE training_runs SET status='stopped', finished_at=?, error=? WHERE id=?", (db.now(), f"paused: {e}. The checkpoint is kept; run the same experiment again to continue.", run_id))
        raise
    except BaseException as e:
        db.x("UPDATE training_runs SET status='failed', finished_at=?, error=? WHERE id=?", (db.now(), f"{type(e).__name__}: {str(e)[:300]}", run_id))
        raise

"""Transfer-learning trainer + evaluator (PyTorch). CPU, Apple-silicon MPS and CUDA are auto-selected.

Rules enforced here:
* pretrained weights are NEVER downloaded implicitly: pass pretrained_path to a local file; its sha256 and provenance go on the model card;
* splits are patient/group-level and class-stratified (no leakage), duplicates across splits are measured;
* every candidate is compared with baselines (majority class, frozen-feature logistic regression) before it is considered;
* the held-out test set is used once, after model selection on the validation set;
* metrics, progress and logs come from the running process; runs are resumable and stoppable."""
from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path

import numpy as np

from .. import db
from ..config import DATA_DIR, MODEL_DIR

CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CODE_ROOT = Path(__file__).resolve().parent.parent


class Stopped(Exception):
    pass


def code_revision() -> str:
    """Hash of the application source tree (the repository is not under git here); recorded on every model card."""
    h = hashlib.sha256()
    for p in sorted(CODE_ROOT.rglob("*.py")):
        h.update(p.relative_to(CODE_ROOT).as_posix().encode()); h.update(p.read_bytes())
    return "src-" + h.hexdigest()[:16]


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


DEVICE_PREFERENCE: str | None = None        # set by the orchestrator for the duration of one run (only one run is active at a time)


def pick_device(prefer: str | None = None):
    """CPU unless a GPU was explicitly requested (COSMO_AI_DEVICE, the run's device setting or `prefer`) or has a passing verification record: see ai/resources.select_device.
    A GPU that merely exists is not used."""
    from ..ai.resources import select_device
    return select_device(prefer or DEVICE_PREFERENCE).kind


# ---------------------------------------------------------------- data
def stratified_group_split(entries: list[dict], seed: int = 17, n_folds: int = 7):
    """Patient-level (group) split, stratified on label. ~72/14/14 train/val/test. Deterministic."""
    from sklearn.model_selection import StratifiedGroupKFold
    y = [e["label"] for e in entries]
    g = [e["group_id"] for e in entries]
    folds = list(StratifiedGroupKFold(n_splits=n_folds, shuffle=True, random_state=seed).split(entries, y, g))
    idx = {"test": folds[0][1], "val": folds[1][1]}
    taken = set(idx["test"]) | set(idx["val"])
    idx["train"] = np.array([i for i in range(len(entries)) if i not in taken])
    out = {k: [entries[i] for i in v] for k, v in idx.items()}
    groups = {k: {e["group_id"] for e in v} for k, v in out.items()}
    assert not (groups["train"] & groups["val"] or groups["train"] & groups["test"] or groups["val"] & groups["test"]), "split leakage"
    return out


def _square_resize(im, size):
    from PIL import Image
    im = im.convert("RGB")
    w, h = im.size
    s = max(w, h)
    canvas = Image.new("RGB", (s, s), (int(np.mean([p for p in im.resize((1, 1)).getdata()][0])),) * 3)
    canvas.paste(im, ((s - w) // 2, (s - h) // 2))     # pad (never crop): the lesion may not be central
    return canvas.resize((size, size), Image.BILINEAR)


def build_cache(root, entries, size, log=lambda m: None) -> np.ndarray:
    """uint8 [N,size,size,3] cache keyed by file hashes and size, so epochs do not re-decode large PNGs."""
    from PIL import Image
    key = hashlib.sha256((str(size) + "".join(e["sha256"] for e in entries)).encode()).hexdigest()[:20]
    path = CACHE_DIR / f"{key}.npy"
    if path.exists():
        return np.load(path, mmap_mode="r")
    arr = np.lib.format.open_memmap(str(path) + ".tmp.npy", mode="w+", dtype=np.uint8, shape=(len(entries), size, size, 3))
    t = time.time()
    for i, e in enumerate(entries):
        with Image.open(Path(root) / e["file"]) as im:
            arr[i] = np.asarray(_square_resize(im, size))
        if i % 200 == 0:
            log(f"cache {i}/{len(entries)} ({time.time() - t:.0f}s)")
    arr.flush(); del arr
    Path(str(path) + ".tmp.npy").replace(path)
    return np.load(path, mmap_mode="r")


def near_duplicate_pairs(X_a, X_b, threshold: float = 0.985) -> list[tuple[int, int, float]]:
    """Pairs (i in a, j in b) whose 32x32 grayscale thumbnails correlate above `threshold` (zero-mean, unit-norm dot product).
    Much stricter than a perceptual hash, which over-matches low-texture images."""
    import cv2

    def thumbs(X):
        out = np.empty((len(X), 1024), np.float32)
        for i in range(len(X)):
            g = cv2.resize(cv2.cvtColor(np.ascontiguousarray(X[i]), cv2.COLOR_RGB2GRAY), (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
            g -= g.mean()
            out[i] = g / (np.linalg.norm(g) + 1e-6)
        return out
    A, B = thumbs(X_a), thumbs(X_b)
    sim = A @ B.T
    ii, jj = np.where(sim >= threshold)
    return [(int(i), int(j), float(sim[i, j])) for i, j in zip(ii, jj)]


# ---------------------------------------------------------------- model
def build_model(arch: str, num_classes: int, weights_path: str | None, finetune: str):
    import torch
    import torch.nn as nn
    import torchvision.models as tvm
    if arch == "resnet18":
        m = tvm.resnet18(weights=None)
    elif arch == "mobilenet_v3_small":
        m = tvm.mobilenet_v3_small(weights=None)
    else:
        raise ValueError("unsupported architecture")
    if weights_path:
        m.load_state_dict(torch.load(weights_path, map_location="cpu", weights_only=True))
    if arch == "resnet18":
        m.fc = nn.Linear(m.fc.in_features, num_classes)
    else:
        m.classifier[-1] = nn.Linear(m.classifier[-1].in_features, num_classes)
    for p in m.parameters():
        p.requires_grad = False
    head = [m.fc] if arch == "resnet18" else [m.classifier]
    trainable = list(head)
    if finetune in ("layer4", "all") and arch == "resnet18":
        trainable.append(m.layer4)
    if finetune == "all":
        trainable = [m]
    for mod in trainable:
        for p in mod.parameters():
            p.requires_grad = True
    m._trainable_modules = trainable
    return m


def _set_train(model):
    model.eval()                                  # frozen parts keep fixed BatchNorm statistics
    for mod in getattr(model, "_trainable_modules", [model]):
        mod.train()


MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def _to_tensor(batch_u8):
    import torch
    x = (batch_u8.astype(np.float32) / 255.0 - MEAN) / STD
    return torch.from_numpy(np.ascontiguousarray(x.transpose(0, 3, 1, 2)))


def _augment(batch_u8: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    import cv2
    out = np.empty_like(batch_u8)
    for i, im in enumerate(batch_u8):
        if rng.random() < 0.5:
            im = im[:, ::-1]
        if rng.random() < 0.5:
            im = im[::-1]
        h, w = im.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), rng.uniform(-25, 25), rng.uniform(0.9, 1.1))
        im = cv2.warpAffine(np.ascontiguousarray(im), M, (w, h), borderMode=cv2.BORDER_REFLECT)
        im = np.clip(im.astype(np.float32) * rng.uniform(0.8, 1.2) + rng.uniform(-15, 15), 0, 255).astype(np.uint8)
        out[i] = im
    return out


def predict_logits(model, X, device, batch=64):
    import torch
    model.eval().to(device)
    outs = []
    with torch.no_grad():
        for s in range(0, len(X), batch):
            outs.append(model(_to_tensor(np.asarray(X[s:s + batch])).to(device)).cpu().numpy())
    return np.concatenate(outs) if outs else np.zeros((0, 1))


def softmax(z, T=1.0):
    z = z / T
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def fit_temperature(logits, y) -> float:
    """Single-parameter temperature scaling fitted on the VALIDATION set (never on test)."""
    best, bt = 1e9, 1.0
    for T in np.linspace(0.5, 5, 91):
        p = softmax(logits, T)
        nll = -np.log(p[np.arange(len(y)), y] + 1e-12).mean()
        if nll < best:
            best, bt = nll, float(T)
    return bt


def ece(P, y, bins=10):
    conf, pred = P.max(1), P.argmax(1)
    edges, e = np.linspace(0, 1, bins + 1), 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            e += m.mean() * abs(conf[m].mean() - (pred[m] == y[m]).mean())
    return float(e)


def macro_f1(y, pred, k):
    f = []
    for c in range(k):
        tp = ((pred == c) & (y == c)).sum(); fp = ((pred == c) & (y != c)).sum(); fn = ((pred != c) & (y == c)).sum()
        f.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return float(np.mean(f))


def balanced_acc(y, pred, k):
    r = [((pred == c) & (y == c)).sum() / max((y == c).sum(), 1) for c in range(k) if (y == c).any()]
    return float(np.mean(r))


def bootstrap_ci(y, pred, k, fn, n=1000, seed=0):
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        vals.append(fn(y[i], pred[i], k))
    return [round(float(np.percentile(vals, 2.5)), 3), round(float(np.percentile(vals, 97.5)), 3)]


def full_metrics(P, y, classes, entries=None, groups_key="fitzpatrick"):
    from sklearn.metrics import roc_auc_score
    k = len(classes)
    pred = P.argmax(1)
    cm = np.zeros((k, k), int)
    for t, p in zip(y, pred):
        cm[t, p] += 1
    per = {}
    for i, c in enumerate(classes):
        tp = cm[i, i]; fn = cm[i].sum() - tp; fp = cm[:, i].sum() - tp; tn = cm.sum() - tp - fn - fp
        try:
            auc = float(roc_auc_score((y == i).astype(int), P[:, i])) if 0 < (y == i).sum() < len(y) else None
        except ValueError:
            auc = None
        per[c] = {"n": int(cm[i].sum()), "sensitivity": round(tp / max(tp + fn, 1), 3) if cm[i].sum() else None, "specificity": round(tn / max(tn + fp, 1), 3),
                  "precision": round(tp / max(tp + fp, 1), 3) if (tp + fp) else None, "auroc": round(auc, 3) if auc is not None else None,
                  "false_negatives": int(fn), "false_positives": int(fp)}
    out = {"n": int(len(y)), "accuracy": round(float((pred == y).mean()), 3), "macro_f1": round(macro_f1(y, pred, k), 3), "balanced_accuracy": round(balanced_acc(y, pred, k), 3),
           "macro_f1_ci95": bootstrap_ci(y, pred, k, macro_f1), "balanced_accuracy_ci95": bootstrap_ci(y, pred, k, balanced_acc), "ece": round(ece(P, y), 3),
           "per_class": per, "confusion_matrix": cm.tolist(), "classes": classes}
    if entries is not None:
        sub = {}
        def bucket(e):
            v = str(e.get(groups_key) or "NA")
            return "NA" if v in ("None", "", "NA", "nan") else ("1-2" if v in ("1.0", "2.0", "1", "2") else "3" if v in ("3.0", "3") else "4-6")
        for b in sorted({bucket(e) for e in entries}):
            idx = [i for i, e in enumerate(entries) if bucket(e) == b]
            yy, pp = y[idx], pred[idx]
            sub[b] = {"n": len(idx), "accuracy": round(float((pp == yy).mean()), 3), "balanced_accuracy": round(balanced_acc(yy, pp, k), 3) if len(idx) >= 10 else None,
                      "reliable": len(idx) >= 50}
        out["subgroups_by_fitzpatrick"] = sub
    fails = [{"file": entries[i]["file"], "true": classes[y[i]], "predicted": classes[pred[i]], "confidence": round(float(P[i].max()), 3)}
             for i in range(len(y)) if pred[i] != y[i]] if entries is not None else []
    out["failure_cases"] = sorted(fails, key=lambda f: -f["confidence"])[:50]
    out["warnings"] = (["Fewer than 100 test images: metrics are statistically unreliable."] if len(y) < 100 else []) + \
                      [f"Class '{c}' has fewer than 20 test images; its metrics are unreliable." for c, m in per.items() if m["n"] < 20]
    return out


# ---------------------------------------------------------------- main training entry
CHECKPOINT_DIR = MODEL_DIR / "_checkpoints"


def fingerprint(config: dict) -> str:
    """Identity of a run: everything that must be identical for a resume to continue the SAME experiment."""
    import torch
    return hashlib.sha256(json.dumps({**config, "torch": torch.__version__.split("+")[0]}, sort_keys=True).encode()).hexdigest()[:32]


class Checkpoint:
    """One checkpoint per run fingerprint, written atomically after every completed epoch, with a SHA-256 sidecar verified before loading.
    Loaded with weights_only=True (no arbitrary pickles). A checkpoint for a different configuration, dataset version, split or torch version has a different
    fingerprint and is simply not found: a resume can never continue a different experiment. Progress inside an unfinished epoch is lost on stop."""

    def __init__(self, directory, fp: str):
        self.dir, self.fp = Path(directory), fp
        self.pt, self.meta = self.dir / f"{fp}.pt", self.dir / f"{fp}.json"

    def save(self, state: dict):
        import torch
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.pt.with_suffix(".tmp")
        torch.save(state, tmp)
        digest = sha256_file(tmp)
        tmp.replace(self.pt)
        self.meta.write_text(json.dumps({"fingerprint": self.fp, "sha256": digest, "epoch": state["epoch"], "saved_at": state["saved_at"], **state.get("meta", {})}), encoding="utf-8")

    def load(self):
        import torch
        if not self.pt.is_file() or not self.meta.is_file():
            return None
        meta = json.loads(self.meta.read_text(encoding="utf-8"))
        if meta.get("fingerprint") != self.fp or sha256_file(self.pt) != meta.get("sha256"):
            raise ValueError("The checkpoint failed its integrity check and was not loaded.")
        return torch.load(self.pt, map_location="cpu", weights_only=True)

    def discard(self):
        self.pt.unlink(missing_ok=True)
        self.meta.unlink(missing_ok=True)


def list_checkpoints(directory=None) -> list[dict]:
    d = Path(directory or CHECKPOINT_DIR)
    out = []
    for m in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(json.loads(m.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def train(task_id: str, dataset_id: str, version_id: str, actor: str, arch="resnet18", epochs=3, lr=1e-3, batch=16,
          weights_path: str | None = None, freeze_backbone=True, seed=17, max_per_split: int | None = None,
          image_size=224, finetune: str | None = None, pretrained_note: str = "", progress=None, should_stop=None,
          license_info: dict | None = None, extra_eval=None, ood_dir: str | None = None, resume: bool = False, checkpoint_dir=None) -> dict:
    import torch
    import torch.nn as nn
    log_lines: list[str] = []

    def log(m):
        log_lines.append(f"{time.strftime('%H:%M:%S')} {m}")
        if progress:
            progress({"log": log_lines[-1]})

    def prog(done, total, phase):
        if progress:
            progress({"phase": phase, "percent": round(100 * done / max(total, 1), 1)})

    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    row = db.q1("SELECT manifest FROM dataset_versions WHERE id=? AND dataset_id=?", (version_id, dataset_id))
    if not row:
        raise ValueError("unknown dataset version")
    mf = json.loads(row["manifest"])
    if mf.get("complete") is False:
        raise ValueError("This dataset version is incomplete (the import was cancelled). Import it again before training.")
    entries = [e for e in mf["entries"] if e.get("label")]
    splits = stratified_group_split(entries, seed)
    if max_per_split:
        splits = {k: v[:max_per_split] for k, v in splits.items()}
    classes = sorted({e["label"] for e in entries})
    if len(classes) < 2 or not splits["train"] or not splits["test"] or not splits["val"]:
        raise ValueError("Need at least two labelled classes and non-empty train, validation and test splits.")
    c2i = {c: i for i, c in enumerate(classes)}
    y = {k: np.array([c2i[e["label"]] for e in v]) for k, v in splits.items()}
    log(f"splits: " + ", ".join(f"{k}={len(v)} images/{len({e['group_id'] for e in v})} patients" for k, v in splits.items()))

    X = {k: build_cache(mf["root"], v, image_size, log) for k, v in splits.items()}

    # leakage audit: near-duplicates across splits (thumbnail correlation; the perceptual-hash count is kept for reference only)
    from ..datasets.importer import hamming
    tr_hash = [e["phash"] for e in splits["train"]]
    phash_flag = {s: sum(1 for e in splits[s] if any(hamming(e["phash"], h) <= 2 for h in tr_hash)) for s in ("val", "test")}
    pairs = {s: near_duplicate_pairs(X[s], X["train"]) for s in ("val", "test")}
    leak = {s: len({i for i, _, _ in pairs[s]}) for s in pairs}
    same_pat = {s: sum(1 for i, j, _ in pairs[s] if splits[s][i]["group_id"] == splits["train"][j]["group_id"]) for s in pairs}
    log(f"near-duplicate audit vs train (thumbnail corr >= 0.985): val={leak['val']} test={leak['test']}; perceptual-hash flags (unreliable): {phash_flag}")
    device = pick_device()
    finetune = finetune or ("head" if freeze_backbone else "layer4")
    model = build_model(arch, len(classes), weights_path, finetune).to(device)
    pre_sha = sha256_file(weights_path) if weights_path else None
    counts = np.bincount(y["train"], minlength=len(classes))
    split_hash = hashlib.sha256(json.dumps({k: [e["sha256"] for e in v] for k, v in splits.items()}).encode()).hexdigest()
    ckpt = Checkpoint(checkpoint_dir or CHECKPOINT_DIR, fingerprint(dict(task=task_id, dataset=dataset_id, version=version_id, arch=arch, epochs=epochs, lr=lr, batch=batch, seed=seed,
                                                                     image_size=image_size, finetune=finetune, pretrained=pre_sha, split_hash=split_hash, max_per_split=max_per_split)))

    # ---------- baselines
    baselines = {}
    maj = int(counts.argmax())
    Pm = np.zeros((len(y["test"]), len(classes))); Pm[:, maj] = 1
    baselines["majority_class"] = {"class": classes[maj], **{k: v for k, v in full_metrics(Pm, y["test"], classes).items() if k in ("accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95")}}
    if weights_path:
        from sklearn.linear_model import LogisticRegression
        import torchvision.models as tvm
        feat = tvm.resnet18(weights=None); feat.load_state_dict(torch.load(weights_path, map_location="cpu", weights_only=True)); feat.fc = nn.Identity()
        log("baseline: extracting frozen ImageNet features")
        F = {k: predict_logits(feat, X[k], device) for k in ("train", "val", "test")}
        lr_clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5).fit(F["train"], y["train"])
        Pb = lr_clf.predict_proba(F["test"])
        baselines["frozen_features_logreg"] = {k: v for k, v in full_metrics(Pb, y["test"], classes).items() if k in ("accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95", "ece")}
        log(f"baseline frozen+logreg: macro-F1 {baselines['frozen_features_logreg']['macro_f1']}")
        del feat, F

    # ---------- fine-tune
    w = torch.tensor((counts.sum() / np.maximum(counts, 1)) ** 0.5, dtype=torch.float32)
    w = (w / w.mean()).to(device)
    lossf = nn.CrossEntropyLoss(weight=w, label_smoothing=0.1)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=1e-4)
    steps_per_epoch = max(1, (len(y["train"]) + batch - 1) // batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=epochs * steps_per_epoch)
    rng = np.random.default_rng(seed)
    history, best, best_state, step = [], -1.0, None, 0
    start_ep, resumed = 0, None
    if resume:
        st = ckpt.load()
        if st is None:
            log("resume requested but no matching checkpoint exists: starting from epoch 1")
        else:
            model.load_state_dict(st["model"]); opt.load_state_dict(st["opt"]); sched.load_state_dict(st["sched"])
            rng.bit_generator.state = st["np_rng"]; torch.set_rng_state(st["torch_rng"])
            history, best, best_state, step, start_ep = st["history"], st["best"], st["best_state"], st["step"], st["epoch"]
            resumed = {"from_epoch": start_ep, "checkpoint_code_revision": st["code_revision"], "resumed_code_revision": code_revision(), "checkpoint_saved_at": st["saved_at"]}
            log(f"resumed from checkpoint after epoch {start_ep}/{epochs}" + ("" if st["code_revision"] == code_revision() else f" (code revision changed: {st['code_revision']} -> {code_revision()})"))
    t0 = time.time()
    for ep in range(start_ep, epochs):
        _set_train(model)
        order = rng.permutation(len(y["train"]))
        tot, n = 0.0, 0
        for s in range(0, len(order), batch):
            if should_stop and should_stop():
                raise Stopped()
            idx = np.sort(order[s:s + batch])
            xb = _to_tensor(_augment(np.asarray(X["train"][idx]), rng)).to(device)
            yb = torch.from_numpy(y["train"][idx]).to(device)
            opt.zero_grad(); loss = lossf(model(xb), yb); loss.backward(); opt.step(); sched.step()
            tot += float(loss) * len(idx); n += len(idx); step += 1
            if step % 10 == 0:
                prog(step, epochs * steps_per_epoch, "training")
        Lv = predict_logits(model, X["val"], device)
        vf1 = macro_f1(y["val"], Lv.argmax(1), len(classes))
        history.append({"epoch": ep + 1, "train_loss": round(tot / max(n, 1), 4), "val_macro_f1": round(vf1, 4), "val_accuracy": round(float((Lv.argmax(1) == y["val"]).mean()), 4),
                        "elapsed_s": round(time.time() - t0)})
        log(f"epoch {ep + 1}/{epochs} loss {history[-1]['train_loss']} val macro-F1 {vf1:.3f} ({time.time() - t0:.0f}s)")
        if vf1 > best:
            best, best_state = vf1, {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        ckpt.save({"epoch": ep + 1, "model": model.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(), "np_rng": rng.bit_generator.state,
                   "torch_rng": torch.get_rng_state(), "history": history, "best": best, "best_state": best_state, "step": step,
                   "code_revision": code_revision(), "saved_at": time.time(), "meta": {"task": task_id, "dataset": dataset_id, "epochs": epochs}})
    model.load_state_dict(best_state)

    # ---------- calibration (on validation), then the single test evaluation
    Lv = predict_logits(model, X["val"], device)
    T = fit_temperature(Lv, y["val"])
    Lt = predict_logits(model, X["test"], device)
    P_raw, P = softmax(Lt), softmax(Lt, T)
    test = full_metrics(P, y["test"], classes, splits["test"])
    test["ece_uncalibrated"] = round(ece(P_raw, y["test"]), 3)
    test["temperature"] = round(T, 3)
    # selective prediction: coverage vs accuracy at max-probability thresholds
    conf, pred = P.max(1), P.argmax(1)
    test["selective_prediction"] = [{"threshold": t, "coverage": round(float((conf >= t).mean()), 3),
                                     "accuracy_on_accepted": round(float((pred[conf >= t] == y["test"][conf >= t]).mean()), 3) if (conf >= t).any() else None}
                                    for t in (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)]
    # robustness on the test set
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
        Pc = softmax(predict_logits(model, corrupt(kind), device), T)
        rob[kind] = {"macro_f1": round(macro_f1(y["test"], Pc.argmax(1), len(classes)), 3), "balanced_accuracy": round(balanced_acc(y["test"], Pc.argmax(1), len(classes)), 3),
                     "mean_confidence": round(float(Pc.max(1).mean()), 3)}
    test["robustness"] = rob
    if ood_dir and Path(ood_dir).is_dir():
        from PIL import Image
        from sklearn.metrics import roc_auc_score
        files = sorted(p for p in Path(ood_dir).iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        if files:
            O = np.stack([np.asarray(_square_resize(Image.open(p), image_size)) for p in files])
            Po = softmax(predict_logits(model, O, device), T)
            co, ci_ = Po.max(1), P.max(1)
            thr = (0.5, 0.7, 0.9)
            test["ood"] = {"n": len(files), "source": "non-lesion natural images supplied by the administrator", "mean_confidence": round(float(co.mean()), 3),
                           "in_distribution_mean_confidence": round(float(ci_.mean()), 3),
                           "fraction_above_threshold": {str(t): round(float((co >= t).mean()), 3) for t in thr},
                           "in_distribution_fraction_above_threshold": {str(t): round(float((ci_ >= t).mean()), 3) for t in thr},
                           "auroc_confidence_separates_in_vs_out": round(float(roc_auc_score(np.r_[np.ones(len(ci_)), np.zeros(len(co))], np.r_[ci_, co])), 3)}
    if extra_eval:                         # e.g. out-of-distribution images supplied by the caller
        test.update(extra_eval(lambda arr: softmax(predict_logits(model, arr, device), T), image_size))

    mid = db.uid()
    out_dir = MODEL_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    wpath = out_dir / f"{mid}.pt"
    torch.save(model.state_dict(), wpath)
    card = {"model_id": mid, "task": task_id, "architecture": arch, "classes": classes, "dataset_id": dataset_id,
            "dataset_version_id": version_id, "dataset_version": mf["version"], "seed": seed, "device": device, "image_size": image_size,
            "finetune": finetune, "epochs": epochs, "lr": lr, "batch": batch, "class_weights": "sqrt inverse frequency (train)", "label_smoothing": 0.1,
            "split_sizes": {k: len(v) for k, v in splits.items()}, "patients_per_split": {k: len({e["group_id"] for e in v}) for k, v in splits.items()},
            "near_duplicates_vs_train": leak, "near_duplicate_pairs_same_patient": same_pat, "perceptual_hash_flags_unreliable": phash_flag,
            "split_hash": split_hash, "resumed": resumed,
            "pretrained_weights": weights_path or "none (random initialisation)", "pretrained_sha256": pre_sha, "pretrained_provenance": pretrained_note or "none recorded",
            "weights_provenance_note": "Pretrained weights must come from a local file whose source and licence were checked by an administrator.",
            "baselines": baselines, "history": history, "best_val_macro_f1": round(best, 4), "test_metrics": test, "training_seconds": round(time.time() - t0),
            "artifact_path": str(wpath), "artifact_sha256": sha256_file(wpath), "code_revision": code_revision(),
            "dataset_license": license_info or {}, "intended_use": "Research only. Not clinically validated. Not offered to patients.", "approval": None,
            "log": log_lines[-60:],
            "limitations": ["Small-sample metrics are unreliable", "Dataset demographics may not represent target patients"]}
    db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (mid, task_id, json.dumps(card), "research_only", db.now()))
    from ..ai import lifecycle
    lifecycle.registered(mid, task_id, actor, card)
    ckpt.discard()                                  # a finished run needs no checkpoint
    db.audit(actor, "model.train", mid, {"task": task_id, "dataset": dataset_id, "version": mf["version"], "test_macro_f1": test["macro_f1"], "code": card["code_revision"]})
    return card


# ---------------------------------------------------------------- registry & release gates
GATES = {  # evaluation gates to be STAGED (not to be approved: approval additionally needs the five release requirements in status.py)
    "min_test_images": 200, "beat_baselines_macro_f1": True, "improvement_supported_by_ci": True, "max_ece_calibrated": 0.15,
    "critical_class_min_sensitivity": 0.80, "critical_class_min_test_images": 20, "ood_min_auroc": 0.90, "dark_skin_min_test_images": 50,
}
# Task-specific safety definitions. For lesion classification, missing a malignant class is the costly error.
TASK_SAFETY = {"lesion_classification_research": {"critical_classes": ["MEL", "BCC", "SCC"]}}


def evaluate_gates(card: dict) -> list[dict]:
    t = card.get("test_metrics", {})
    base = max((b.get("macro_f1", 0) for b in card.get("baselines", {}).values()), default=0)
    lic = card.get("dataset_license", {})
    ci_lo = (t.get("macro_f1_ci95") or [0, 0])[0]
    checks = [
        ("licence_gate_passed", bool(lic.get("spdx")) and bool(lic.get("terms_snapshot_hash")), f"licence {lic.get('spdx')} with evidence hash" if lic.get("spdx") else "no verified licence recorded"),
        ("min_test_images", t.get("n", 0) >= GATES["min_test_images"], f"test n={t.get('n')} (need >= {GATES['min_test_images']})"),
        ("no_near_duplicates_in_test", card.get("near_duplicates_vs_train", {}).get("test", 1) == 0, f"near-duplicates test/train: {card.get('near_duplicates_vs_train', {}).get('test')}"),
        ("beat_baselines_macro_f1", t.get("macro_f1", 0) > base, f"macro-F1 {t.get('macro_f1')} vs best baseline {base}"),
        ("improvement_supported_by_ci", ci_lo > base, f"95% CI lower bound {ci_lo} must exceed the best baseline {base}"),
        ("max_ece_calibrated", t.get("ece", 1) <= GATES["max_ece_calibrated"], f"ECE {t.get('ece')} (limit {GATES['max_ece_calibrated']})"),
        ("robustness_report_present", bool(t.get("robustness")), "robustness table present" if t.get("robustness") else "missing"),
        ("subgroup_report_present", bool(t.get("subgroups_by_fitzpatrick")), "Fitzpatrick subgroup table present" if t.get("subgroups_by_fitzpatrick") else "missing"),
    ]
    crit = TASK_SAFETY.get(card.get("task"), {}).get("critical_classes")
    if crit is not None:
        pc = t.get("per_class", {})
        bad = []
        for c in crit:
            m = pc.get(c) or {}
            if (m.get("n") or 0) < GATES["critical_class_min_test_images"]:
                bad.append(f"{c}: only {m.get('n', 0)} test images")
            elif (m.get("sensitivity") or 0) < GATES["critical_class_min_sensitivity"]:
                bad.append(f"{c}: sensitivity {m.get('sensitivity')}")
        checks.append(("critical_class_sensitivity", not bad, "; ".join(bad) if bad else f"all of {crit} >= {GATES['critical_class_min_sensitivity']}"))
        sub = (t.get("subgroups_by_fitzpatrick") or {}).get("4-6", {})
        checks.append(("dark_skin_evaluated", (sub.get("n") or 0) >= GATES["dark_skin_min_test_images"], f"Fitzpatrick 4-6 test images: {sub.get('n', 0)} (need >= {GATES['dark_skin_min_test_images']})"))
        ood = t.get("ood", {}).get("auroc_confidence_separates_in_vs_out")
        checks.append(("ood_detection", ood is not None and ood >= GATES["ood_min_auroc"], f"confidence separates lesion from non-lesion images with AUROC {ood} (need >= {GATES['ood_min_auroc']})" if ood is not None else "no OOD evaluation recorded"))
    return [{"gate": g, "passed": bool(ok), "detail": d} for g, ok, d in checks]


def list_models(task_id: str | None = None):
    rows = db.q("SELECT * FROM model_versions" + (" WHERE task=?" if task_id else "") + " ORDER BY created_at DESC", (task_id,) if task_id else ())
    out = []
    for r in rows:
        c = json.loads(r["data"])
        c.update(status=r["status"], created_at=r["created_at"], gates=evaluate_gates(c))
        out.append(c)
    return out


def compare(model_a: str, model_b: str):
    ms = {m["model_id"]: m for m in list_models()}
    a, b = ms[model_a], ms[model_b]
    ta, tb = a["test_metrics"], b["test_metrics"]
    return {"a": {"id": model_a, "accuracy": ta["accuracy"], "macro_f1": ta["macro_f1"], "ece": ta.get("ece"), "n": ta["n"]},
            "b": {"id": model_b, "accuracy": tb["accuracy"], "macro_f1": tb["macro_f1"], "ece": tb.get("ece"), "n": tb["n"]},
            "same_test_set": a["split_hash"] == b["split_hash"],
            "note": "Differences are only meaningful when both models were evaluated on the same held-out set."}


def approve(model_id: str, actor: str, intended_use: str, requirements: dict, regulatory_note: str, signoff_by: str | None = None):
    """Records an approval request through the model lifecycle (ai/lifecycle.py): the evaluation gates are run, the release requirements are checked, and - when clinical sign-off is claimed -
    a verified clinician must be named. The model becomes `approved` only if all of that holds; otherwise the record says exactly what is missing."""
    from ..ai import lifecycle
    return lifecycle.approve(model_id, actor, intended_use, requirements, regulatory_note, signoff_by)


def deploy(model_id: str, actor: str):
    """Only an approved model whose file matches its checksum, with its record complete and no open clinical issue, can be activated; the previous active model is kept as the known-good fallback."""
    from ..ai import lifecycle
    return lifecycle.activate(model_id, actor)


def rollback(task: str, actor: str):
    """A technical rollback to the most recent known-good model (or to none). It does not change any result already issued; see ai/lifecycle.py."""
    from ..ai import lifecycle
    return lifecycle.rollback(task, actor)

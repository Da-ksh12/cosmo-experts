"""Training orchestration: a reproducible configuration, a resource plan BEFORE anything starts, one run at a time, a persisted record of every run, and safe failure handling.

* Configuration: a validated, hashable `TrainConfig`. The same configuration, dataset version, seed and code revision are what a model card records, and what `resume` is keyed on.
* Resource plan: free memory, free disk, estimated duration (calibrated from this machine's earlier measured runs, then a conservative default), device. A run that does not fit is
  REFUSED with reasons (override of the duration limit only with `allow_long_run`); nothing large is started on a small host by accident.
* One run at a time per process; a second is refused. Progress, cancellation and resume are passed through to the trainer, which keeps its own integrity-checked checkpoints.
* Every run is recorded in `training_runs` (planned configuration, plan, status, timings, resulting model id, a sanitised error). A run that ends abnormally leaves no half-written
  model artifact behind (checkpoints are kept: they are what makes resume possible). Runs left `running` by a crashed process are marked `interrupted` at start-up.
* GPU: requested through `device`; an unverified GPU is allowed only when explicitly asked for and is recorded as unverified. Never assumed."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import db
from ..ai.evaluation.common import sha256_json
from ..ai.resources import available_mb, select_device
from ..config import MODEL_DIR
from ..redact import safe_exc
from . import trainer

# Measured on the development machine (4 logical CPUs, no GPU): ResNet18 fine-tune of layer4 at 224 px took 1199 s for 6 epochs over 1621 training images with per-epoch validation
# (0.123 s per training image per epoch); the DINOv2-small linear probe took 755 s for 2271 images including model-selection (0.33 s per image). Used only until a run of the same
# architecture and device has been recorded; then the recorded median is used.
DEFAULT_SECONDS = {"finetune": 0.125, "probe": 0.35}
OVERHEAD = 1.25                                   # baselines, robustness and out-of-distribution evaluation
PARAM_MB = {"resnet18": 47, "resnet50": 100, "mobilenet_v3_small": 10, "efficientnet_b0": 21}
ACT_MB_PER_IMG = {"resnet18": 28, "resnet50": 95, "mobilenet_v3_small": 18, "efficientnet_b0": 45, "probe": 18}
_active = threading.Lock()
_device_lock = threading.Lock()


class NotFeasible(Exception):
    def __init__(self, blockers: list[str]):
        super().__init__("; ".join(blockers))
        self.blockers = blockers


class TrainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    dataset_id: str
    version_id: str
    method: Literal["finetune", "probe"] = "finetune"
    architecture: str = "resnet18"
    epochs: int = Field(default=3, ge=1, le=100)
    lr: float = Field(default=1e-3, gt=0, le=0.1)
    batch: int = Field(default=16, ge=1, le=256)
    image_size: int = Field(default=224, ge=32, le=512)
    finetune: Literal["head", "layer4", "all"] = "layer4"
    weights_path: str | None = None
    pretrained_note: str = ""
    ood_dir: str | None = None
    max_per_split: int | None = Field(default=None, ge=1)
    seed: int = 17
    resume: bool = False
    device: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    allow_long_run: bool = False

    def canonical(self) -> dict:
        """The configuration as recorded: everything that changes the result, nothing that only changes how the run is scheduled."""
        d = self.model_dump()
        for k in ("resume", "allow_long_run"):
            d.pop(k)
        return d

    def config_hash(self) -> str:
        return sha256_json(self.canonical())


def _manifest(cfg: TrainConfig) -> dict | None:
    r = db.q1("SELECT manifest FROM dataset_versions WHERE id=? AND dataset_id=?", (cfg.version_id, cfg.dataset_id))
    return json.loads(r["manifest"]) if r else None


def _recorded_rate(cfg: TrainConfig, device: str) -> float | None:
    """Median seconds per training image per epoch from earlier finished runs with the same method, architecture and device."""
    rates = []
    for r in db.q("SELECT config,plan,started_at,finished_at FROM training_runs WHERE status='done' ORDER BY started_at DESC LIMIT 20"):
        c, p = json.loads(r["config"]), json.loads(r["plan"] or "{}")
        if c.get("method") == cfg.method and c.get("architecture") == cfg.architecture and p.get("device") == device and p.get("train_images") and r["finished_at"] and r["started_at"]:
            rates.append((r["finished_at"] - r["started_at"]) / (p["train_images"] * max(c.get("epochs", 1), 1)))
    rates.sort()
    return rates[len(rates) // 2] if rates else None


def plan(cfg: TrainConfig) -> dict:
    """Is this run feasible here, and what will it cost? Returns the plan; `feasible` is False with `blockers` when it does not fit."""
    from ..datasets import importer
    blockers: list[str] = []
    warnings: list[str] = []
    mf = _manifest(cfg)
    n_train, n_all = 0, 0
    if mf is None:
        blockers.append("Unknown dataset version.")
    else:
        if mf.get("complete") is False:
            blockers.append("This dataset version is incomplete (the import was cancelled). Import it again.")
        entries = [e for e in mf["entries"] if e.get("label")]
        n_all = len(entries)
        n_train = int(n_all * 0.72) if not cfg.max_per_split else min(int(n_all * 0.72), cfg.max_per_split)
        if len({e["label"] for e in entries}) < 2:
            blockers.append("Fewer than two labelled classes.")
        check = importer.verify_files(mf, sample=30)
        if not check["ok"]:
            blockers.append(f"{len(check['missing'])} missing and {len(check['mismatched'])} altered files in a sample of {check['checked']}: the stored dataset no longer matches its manifest.")
    try:
        dev = select_device(None if cfg.device == "auto" else cfg.device)
    except Exception as e:
        dev = None
        blockers.append(getattr(e, "public_message", str(e)))
    device_kind = dev.kind if dev else "cpu"
    if dev is not None and device_kind != "cpu":
        warnings.append(dev.note)
    key = "probe" if cfg.method == "probe" else cfg.architecture
    rate = _recorded_rate(cfg, device_kind)
    per = rate if rate is not None else DEFAULT_SECONDS[cfg.method] * (1.0 if cfg.architecture == "resnet18" or cfg.method == "probe" else 3.0)
    if cfg.method == "probe":
        est_seconds = (n_all * per) * OVERHEAD
    else:
        est_seconds = n_train * cfg.epochs * per * OVERHEAD
    if device_kind != "cpu":
        est_seconds /= 4.0
        warnings.append("Duration on an accelerator is a rough guess (divided by four); it has not been measured here.")
    scale = (cfg.image_size / 224) ** 2
    import sys
    base = 200 if "torch" in sys.modules else 600            # the framework itself: already resident when the server (or this script) has imported it
    est_ram = base + PARAM_MB.get(cfg.architecture, 120) * (3 if cfg.method == "finetune" else 1) + ACT_MB_PER_IMG.get(key, 60) * cfg.batch * scale + 200
    free = available_mb()
    frac = float(os.environ.get("COSMO_TRAIN_RAM_FRACTION", "0.8"))
    if est_ram > free * frac:
        blockers.append(f"Not enough free memory: this run needs about {est_ram:.0f} MB and {free:.0f} MB is free (using at most {int(frac * 100)} %). Lower the batch size or image size, or close other programs.")
    cache_mb = n_all * cfg.image_size ** 2 * 3 / 1e6
    free_disk = shutil.disk_usage(MODEL_DIR).free / 1e9
    if free_disk * 1000 < cache_mb + 2000:
        blockers.append(f"Not enough free disk space: the image cache alone needs about {cache_mb:.0f} MB plus a 2 GB margin; {free_disk:.1f} GB is free.")
    limit = float(os.environ.get("COSMO_TRAIN_MAX_SECONDS", "7200"))
    if est_seconds > limit and not cfg.allow_long_run:
        blockers.append(f"Estimated duration {est_seconds / 3600:.1f} h exceeds the {limit / 3600:.1f} h limit for this machine ({'measured on this machine from earlier runs' if rate else 'default estimate'}). "
                        "Use an external compute worker, or set allow_long_run to accept it.")
    return {"feasible": not blockers, "blockers": blockers, "warnings": warnings, "train_images": n_train, "labelled_images": n_all, "est_seconds": round(est_seconds), "seconds_basis": "recorded runs" if rate else "default estimate",
            "est_ram_mb": round(est_ram), "free_ram_mb": round(free), "free_disk_gb": round(free_disk, 1), "device": device_kind, "device_verified": dev.verified if dev else None,
            "cpu_count": os.cpu_count(), "config_sha256": cfg.config_hash()}


def mark_interrupted() -> int:
    """Runs left 'running' by a process that ended abnormally cannot still be running: say so."""
    with db.context.as_system():
        rows = db.q("SELECT id FROM training_runs WHERE status='running'")
        for r in rows:
            db.x("UPDATE training_runs SET status='interrupted', finished_at=?, error=? WHERE id=?", (db.now(), "The process ended before this run finished. Its checkpoint, if any, can be resumed.", r["id"]))
    return len(rows)


def list_runs(limit: int = 50) -> list[dict]:
    rows = db.q("SELECT * FROM training_runs ORDER BY started_at DESC LIMIT ?", (min(limit, 200),))
    return [dict(r, config=json.loads(r["config"]), plan=json.loads(r["plan"] or "{}")) for r in rows]


def _cleanup_partial_artifacts(task_id: str, since: float) -> list[str]:
    """Model files written by this run that never reached the registry."""
    d = MODEL_DIR / task_id
    if not d.is_dir():
        return []
    with db.context.as_system():
        known = {r["id"] for r in db.q("SELECT id FROM model_versions WHERE task=?", (task_id,))}
    gone = []
    for p in d.iterdir():
        if p.is_file() and p.suffix in (".pt", ".npz", ".tmp") and p.stat().st_mtime >= since and p.stem not in known:
            p.unlink(missing_ok=True)
            gone.append(p.name)
    return gone


def run_training(cfg: TrainConfig, actor: str, *, progress=None, should_stop=None, license_info: dict | None = None, probe_provenance: dict | None = None) -> dict:
    """Plan, refuse if infeasible, run, record. Returns {"run_id", "status", "model_id"}. Raises NotFeasible before anything starts."""
    if not _active.acquire(blocking=False):
        raise NotFeasible(["Another training run is already in progress on this server."])
    try:
        return _run_locked(cfg, actor, progress, should_stop, license_info, probe_provenance)
    finally:
        _active.release()


def _run_locked(cfg: TrainConfig, actor: str, progress, should_stop, license_info, probe_provenance) -> dict:
    p = plan(cfg)
    if not p["feasible"]:
        raise NotFeasible(p["blockers"])
    run_id = db.uid()
    started = db.now()
    db.x("INSERT INTO training_runs(id,task,dataset_id,version_id,config,config_hash,plan,status,started_at,actor,device) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (run_id, cfg.task_id, cfg.dataset_id, cfg.version_id, json.dumps(cfg.canonical()), cfg.config_hash(), json.dumps(p), "running", started, actor, p["device"]))
    db.audit(actor, "training.start", run_id, {"task": cfg.task_id, "dataset": cfg.dataset_id, "config_sha256": cfg.config_hash()})
    with _device_lock:
        trainer.DEVICE_PREFERENCE = None if cfg.device == "auto" else cfg.device
    try:
        if cfg.method == "probe":
            from . import probe
            if not cfg.weights_path:
                raise ValueError("A probe needs the backbone weights file (weights_path).")
            card = probe.train_probe(cfg.task_id, cfg.dataset_id, cfg.version_id, actor, cfg.weights_path, probe_provenance or {"note": cfg.pretrained_note}, seed=cfg.seed,
                                     image_size=cfg.image_size, progress=progress, ood_dir=cfg.ood_dir, license_info=license_info)
        else:
            card = trainer.train(cfg.task_id, cfg.dataset_id, cfg.version_id, actor, cfg.architecture, cfg.epochs, cfg.lr, cfg.batch, weights_path=cfg.weights_path, seed=cfg.seed,
                                 max_per_split=cfg.max_per_split, image_size=cfg.image_size, finetune=cfg.finetune, pretrained_note=cfg.pretrained_note, progress=progress,
                                 should_stop=should_stop, license_info=license_info, ood_dir=cfg.ood_dir, resume=cfg.resume)
        db.x("UPDATE training_runs SET status='done', finished_at=?, model_id=? WHERE id=?", (db.now(), card["model_id"], run_id))
        return {"run_id": run_id, "status": "done", "model_id": card["model_id"], "card": card}
    except trainer.Stopped:
        db.x("UPDATE training_runs SET status='stopped', finished_at=? WHERE id=?", (db.now(), run_id))
        db.audit(actor, "training.stopped", run_id)
        return {"run_id": run_id, "status": "stopped", "model_id": None}
    except BaseException as e:                                          # incl. KeyboardInterrupt: record it, clean up, re-raise what is not an ordinary failure
        removed = _cleanup_partial_artifacts(cfg.task_id, started)
        db.x("UPDATE training_runs SET status='failed', finished_at=?, error=? WHERE id=?", (db.now(), safe_exc(e)[:400], run_id))
        db.audit(actor, "training.failed", run_id, {"error": safe_exc(e)[:200], "removed_partial_files": len(removed)})
        if not isinstance(e, Exception):
            raise
        return {"run_id": run_id, "status": "failed", "model_id": None, "error": safe_exc(e)}
    finally:
        with _device_lock:
            trainer.DEVICE_PREFERENCE = None

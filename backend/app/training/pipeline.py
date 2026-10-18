"""The reusable pipeline for image-model tasks: readiness check, dataset validation, subject-level split verification, training through the orchestrator, evaluation (inside the
trainer), model and dataset cards, registration as a RESEARCH model.

It finishes the engineering for every task in `task_specs.py` whether or not data exists. When suitable licensed data is missing the answer is `blocked`, with the reasons: the
pipeline never invents a dataset, never treats a synthetic fixture as evidence (a fixture run is stamped `synthetic_fixture`, carries no licence, and therefore cannot pass the
release gates), and never makes a model available: availability and approval belong to the lifecycle (ai/lifecycle.py)."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

from .. import db
from ..config import MODEL_DIR
from ..datasets import registry
from . import cards, orchestrator, task_specs, tasks, trainer


class Blocked(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def candidates(task_id: str) -> list[dict]:
    t = tasks.get_task(task_id)
    if t is None:
        raise KeyError(task_id)
    out = []
    for rec in (registry.get(r["id"]) for r in db.q("SELECT id FROM datasets ORDER BY id")):
        comp = registry.compatibility(rec, t)
        gate = registry.license_gate(rec, "model_training", commercial=False)
        versions = db.q1("SELECT COUNT(*) c FROM dataset_versions WHERE dataset_id=?", (rec["id"],))["c"]
        out.append({"dataset": rec["id"], "name": rec.get("name"), "compatible": comp["compatible"], "reasons_incompatible": comp["reasons_incompatible"], "licence_gate_allowed": gate["allowed"],
                    "licence_gate_reasons": gate["reasons"], "import_status": rec.get("import_status", "not_imported"), "versions": versions, "approval_status": rec.get("approval_status")})
    return out


def readiness(task_id: str) -> dict:
    """Can a training run for this task start today, and if not, exactly why not?"""
    t = tasks.get_task(task_id)
    spec = task_specs.get(task_id)
    if t is None:
        raise KeyError(task_id)
    blockers: list[str] = []
    cands = candidates(task_id)
    if spec is None:
        blockers.append("No training specification exists for this task.")
    compat = [c for c in cands if c["compatible"]]
    if not compat:
        blockers.append("No catalogued dataset is compatible with this task" + (f" ({'; '.join(spec.candidate_datasets) or 'none identified'} listed as candidates)." if spec else "."))
    elif not any(c["licence_gate_allowed"] for c in compat):
        blockers.append("No compatible dataset has passed the licence gate (official terms verified, evidence recorded, approved by an administrator): "
                        + "; ".join(f"{c['dataset']}: {c['licence_gate_reasons'][0]}" for c in compat if c["licence_gate_reasons"]))
    elif not any(c["licence_gate_allowed"] and c["versions"] for c in compat):
        blockers.append("A compatible, approved dataset exists but has not been imported yet.")
    return {"task": task_id, "name": t["name"], "status": "blocked" if blockers else "ready", "blockers": blockers, "candidates": cands,
            "specification": asdict(spec) if spec else None, "segmentation": "not implemented: it needs pixel-level masks and a supported use case, and no catalogued dataset provides masks",
            "current_availability": t["status"]["status"]}


def verify_subject_split(splits: dict[str, list[dict]]) -> dict:
    """No subject (patient) may appear in two splits. Returns counts; raises if there is leakage."""
    groups = {k: {e["group_id"] for e in v} for k, v in splits.items()}
    names = list(groups)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = groups[a] & groups[b]
            if both:
                raise Blocked([f"Subject-level leakage: {len(both)} subject(s) appear in both '{a}' and '{b}'."])
    return {"subjects_per_split": {k: len(v) for k, v in groups.items()}, "images_per_split": {k: len(v) for k, v in splits.items()},
            "class_counts_per_split": {k: dict(Counter(e["label"] for e in v)) for k, v in splits.items()}}


def validate_dataset(spec: task_specs.TaskSpec, manifest: dict, seed: int = 17) -> dict:
    """Problems that make a training run meaningless, and warnings that qualify its results."""
    problems: list[str] = []
    warnings: list[str] = []
    if manifest.get("complete") is False:
        problems.append("The dataset version is incomplete.")
    entries = [e for e in manifest["entries"] if e.get("label")]
    counts = Counter(e["label"] for e in entries)
    if len(counts) < 2:
        problems.append("Fewer than two labelled classes.")
    if spec.label_schema:
        unknown = sorted(set(counts) - set(spec.label_schema))
        if unknown:
            problems.append(f"Labels {unknown} are not in the task's label scheme {list(spec.label_schema)}; record a class mapping at import.")
    small = {c: n for c, n in counts.items() if n < spec.min_images_per_class}
    if small:
        problems.append(f"Classes with fewer than {spec.min_images_per_class} images: {small}.")
    subjects = {e["group_id"] for e in entries}
    if len(subjects) < spec.min_subjects:
        problems.append(f"Only {len(subjects)} distinct subjects (need at least {spec.min_subjects}); results would not generalise across people.")
    if spec.needs_skin_type:
        known = sum(1 for e in entries if str(e.get("fitzpatrick") or "") not in ("", "None", "nan"))
        dark = sum(1 for e in entries if str(e.get("fitzpatrick") or "") in ("4", "4.0", "5", "5.0", "6", "6.0"))
        if known < 0.5 * len(entries):
            warnings.append(f"Skin type is recorded for only {known} of {len(entries)} images: performance across skin tones cannot be shown.")
        if dark < 50:
            warnings.append(f"Only {dark} images are Fitzpatrick IV-VI (the release gate needs at least {trainer.GATES['dark_skin_min_test_images']} in the TEST set).")
    split_info = None
    if not problems:
        try:
            split_info = verify_subject_split(trainer.stratified_group_split(entries, seed))
            for c in counts:
                for sp in ("val", "test"):
                    if split_info["class_counts_per_split"][sp].get(c, 0) == 0:
                        warnings.append(f"Class '{c}' has no images in the {sp} split: its figures cannot be computed.")
        except Blocked as e:
            problems += e.reasons
        except ValueError as e:
            problems.append(f"The subject-level split could not be built: {e}")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "class_counts": dict(counts), "subjects": len(subjects), "split": split_info}


def run_task(cfg: orchestrator.TrainConfig, actor: str, *, fixture: bool = False, progress=None, should_stop=None) -> dict:
    """Train a research model for `cfg.task_id`. `fixture=True` is for pipeline tests on synthetic data: it skips the licence gate, records no licence (so the release gates cannot pass)
    and stamps the model card as a synthetic fixture."""
    t = tasks.get_task(cfg.task_id)
    spec = task_specs.get(cfg.task_id)
    if t is None or spec is None:
        raise Blocked(["Unknown task."])
    rec = registry.get(cfg.dataset_id)
    row = db.q1("SELECT manifest FROM dataset_versions WHERE id=? AND dataset_id=?", (cfg.version_id, cfg.dataset_id))
    if row is None:
        raise Blocked(["Unknown dataset version."])
    manifest = json.loads(row["manifest"])
    license_info = {}
    if not fixture:
        if rec is None:
            raise Blocked(["The dataset is not in the registry."])
        comp = registry.compatibility(rec, t)
        if not comp["compatible"]:
            raise Blocked(comp["reasons_incompatible"])
        gate = registry.license_gate(rec, "model_training", commercial=False)
        if not gate["allowed"]:
            raise Blocked(gate["reasons"])
        license_info = {"spdx": rec.get("verified_spdx"), "terms_snapshot_hash": rec.get("terms_snapshot_hash"), "official_license_name": rec.get("official_license_name"),
                        "source": rec.get("url"), "attribution": rec.get("attribution")}
    check = validate_dataset(spec, manifest, cfg.seed) if not fixture else {"ok": True, "problems": [], "warnings": ["Synthetic fixture: dataset requirements were not enforced."], "split": None}
    if not check["ok"]:
        raise Blocked(check["problems"])
    result = orchestrator.run_training(cfg, actor, progress=progress, should_stop=should_stop, license_info=license_info)
    if result["status"] != "done":
        return result
    card = result["card"]
    mid = card["model_id"]
    card["evidence_class"] = "synthetic_fixture" if fixture else "public_research_dataset"
    card["pipeline"] = {"version": "task-pipeline-1.0.0", "task_spec": task_specs.get(cfg.task_id).intended_task, "dataset_check": {"warnings": check["warnings"], "split": check["split"]},
                        "config_sha256": cfg.config_hash(), "training_run": result["run_id"], "fixture": fixture}
    if fixture:
        card["limitations"] = list(card.get("limitations", [])) + ["SYNTHETIC FIXTURE: generated to test the pipeline. It says nothing about any clinical task and cannot pass the release gates."]
    with db.context.as_system():
        db.x("UPDATE model_versions SET data=? WHERE id=?", (json.dumps(card), mid))
    out_dir = MODEL_DIR / cfg.task_id
    gates = trainer.evaluate_gates(card)
    (out_dir / f"{mid}.model-card.md").write_text(cards.model_card_markdown(card, gates, "experimental", card["evidence_class"]), encoding="utf-8")
    dcard = cards.dataset_card(rec or {"id": cfg.dataset_id, "name": cfg.dataset_id, "modality": "synthetic fixture" if fixture else None}, manifest)
    (out_dir / f"{mid}.dataset-card.md").write_text(cards.dataset_card_markdown(dcard), encoding="utf-8")
    return {**{k: v for k, v in result.items() if k != "card"}, "gates": gates, "gates_passed": all(g["passed"] for g in gates), "cards": [f"{mid}.model-card.md", f"{mid}.dataset-card.md"], "warnings": check["warnings"]}

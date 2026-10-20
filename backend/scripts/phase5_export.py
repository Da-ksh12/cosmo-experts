"""Export the Phase 5 results from the registry into files that can be committed: one evaluation report per trained model, and the job ledger.

    python -m scripts.phase5_export [--out ../docs/eval]

Writes <out>/phase5/<experiment>.json (the model card of every Phase 5 model, with paths made relative to the data directory and nothing that is not already in the registry) and
<out>/phase5_jobs.json: every dataset considered and what was decided, every task and whether it can be trained today, every run with its status (done, failed, paused, interrupted), and
every model with its lifecycle state and gate results. Model weights, features, images and the database are NOT exported."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.ai import lifecycle  # noqa: E402
from app.datasets import registry  # noqa: E402
from app.training import cards, pipeline, task_specs, trainer  # noqa: E402

DATASETS = ("pad-ufes-20", "isic-dermoscopy", "isic-clinical", "scin", "derm12345", "isic-archive", "ham10000", "fitzpatrick17k", "ddi", "acne04")


def clean(card: dict) -> dict:
    c = json.loads(json.dumps(card, default=str))
    for k in ("artifact_path", "pretrained_weights"):
        if c.get(k):
            c[k] = cards._rel(c[k])
    for s in c.get("sources", []):
        s.pop("root", None)
    c["log"] = c.get("log", [])[-25:]
    return c


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / "docs" / "eval")
    a = ap.parse_args(argv)
    db.init()
    (a.out / "phase5").mkdir(parents=True, exist_ok=True)
    models = []
    for r in db.q("SELECT id, task, data, status, created_at FROM model_versions ORDER BY created_at"):
        card = json.loads(r["data"])
        if not card.get("experiment"):
            continue
        gates = trainer.evaluate_gates(card)
        state = lifecycle.state_of(r, card)
        t = card["test_metrics"]
        models.append({"model_id": r["id"], "experiment": card["experiment"], "task": r["task"], "lifecycle_state": state, "registry_status": r["status"], "classes": card["classes"],
                       "test_n": t["n"], "macro_f1": t["macro_f1"], "macro_f1_ci95": t["macro_f1_ci95"], "balanced_accuracy": t["balanced_accuracy"], "accuracy": t["accuracy"], "ece": t["ece"],
                       "best_baseline_macro_f1": max(b["macro_f1"] for b in card["baselines"].values()), "gates_passed": f"{sum(g['passed'] for g in gates)}/{len(gates)}",
                       "failed_gates": [g["gate"] for g in gates if not g["passed"]], "artifact_sha256": card["artifact_sha256"], "training_seconds": card["training_seconds"],
                       "external": {k: {x: v[x] for x in ("n", "macro_f1", "balanced_accuracy")} for k, v in t.get("external", {}).items()}})
        out = clean(card) | {"gates": gates, "lifecycle_state": state}
        (a.out / "phase5" / f"{card['experiment']}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        beside = Path(card["artifact_path"]).parent
        if beside.is_dir():                                           # the readable card kept next to the artifact (not in Git) always matches the registry
            (beside / f"{r['id']}.model-card.md").write_text(cards.model_card_markdown(card | {"task": r["task"]}, gates, state, card.get("evidence_class", "public_research_dataset")), encoding="utf-8")
    runs = []
    for r in db.q("SELECT * FROM training_runs ORDER BY started_at"):
        cfg = json.loads(r["config"])
        if "experiment" not in cfg:
            continue
        runs.append({"run_id": r["id"], "experiment": cfg["experiment"], "task": r["task"], "status": r["status"], "started_at": r["started_at"], "finished_at": r["finished_at"],
                     "seconds": round(r["finished_at"] - r["started_at"]) if r["finished_at"] and r["started_at"] else None, "model_id": r["model_id"], "error": r["error"], "device": r["device"],
                     "config_sha256": r["config_hash"]})
    datasets = []
    for d in DATASETS:
        rec = registry.get(d)
        vs = [json.loads(v["manifest"]) for v in db.q("SELECT manifest FROM dataset_versions WHERE dataset_id=? ORDER BY created_at", (d,))]
        datasets.append({"dataset": d, "name": rec["name"], "decision": rec["approval_status"], "licence": rec.get("claimed_license"), "verified_spdx": rec.get("verified_spdx"),
                         "import_status": rec.get("import_status"), "versions": [{"version": m["version"], "images": m["count"], "subjects": m.get("subjects"), "complete": m.get("complete"),
                                                                                    "class_distribution": {k: v["n"] for k, v in m.get("class_distribution", {}).items()},
                                                                                    "fitzpatrick": {k: v["n"] for k, v in m.get("fitzpatrick_distribution", {}).items()},
                                                                                    "note": m.get("superseded_note")} for m in vs]})
    tasks_ = []
    for t in task_specs.SPECS:
        rd = pipeline.readiness(t)
        tasks_.append({"task": t, "can_train_today": rd["status"] == "ready", "blockers": rd["blockers"], "models": [m["model_id"] for m in models if m["task"] == t]})
    ledger = {"schema": "phase5-jobs-1", "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "datasets": datasets, "tasks": tasks_, "runs": runs, "models": models,
              "note": "No model here is approved or active; every model is experimental research output on synthetic-label-free public data, not clinically validated."}
    (a.out / "phase5_jobs.json").write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"exported {len(models)} model reports, {len(runs)} runs, {len(datasets)} datasets, {len(tasks_)} tasks -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

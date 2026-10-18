"""Evaluate registered image models on their held-out split and write a machine-readable report.

    python -m scripts.evaluate_model --task lesion_classification_research [--split test] [--limit N] [--out ../docs/eval/vision_lesion_research_test.json]
    python -m scripts.evaluate_model --model <model_id> [--model <model_id> ...]

Runs on the CPU in small batches (safe on a low-memory host); checks free memory first. Reads the development database and the locally stored dataset and model files; changes
nothing in the registry (recording an evaluation against a model is done by the lifecycle module). The output separates what was recomputed from what was copied from the model card."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--task", help="evaluate every registered model of this task")
    ap.add_argument("--split", default="test", choices=("test", "val"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--ood-dir", type=Path)
    ap.add_argument("--metadata", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--min-free-mb", type=int, default=700)
    a = ap.parse_args(argv)
    from app import db
    from app.ai.evaluation import models as ev
    from app.ai.evaluation.common import write_report
    from app.ai.resources import available_mb
    db.init()
    ids = list(a.model)
    if a.task:
        with db.context.as_system():
            ids += [r["id"] for r in db.q("SELECT id FROM model_versions WHERE task=? ORDER BY created_at", (a.task,))]
    if not ids:
        print("no models selected", file=sys.stderr)
        return 2
    if available_mb() < a.min_free_mb:
        print(f"refusing: only {available_mb():.0f} MB of memory is free (need {a.min_free_mb}); close other programs or lower --min-free-mb", file=sys.stderr)
        return 3
    last = [0]

    def progress(done, total):
        if done == total or done - last[0] >= 80:
            last[0] = done
            print(f"  {done}/{total}", flush=True)
    rep = ev.evaluate_models(ids, split=a.split, limit=a.limit or None, batch=a.batch, ood_dir=a.ood_dir, metadata_csv=a.metadata, n_boot=a.n_boot, progress=progress)
    out = a.out or Path(__file__).resolve().parents[2] / "docs" / "eval" / f"vision_{(a.task or 'models').replace('_classification', '')}_{a.split}.json"
    write_report(out, rep)
    for mid, r in rep["reports"].items():
        print(f"{mid[:8]} {r['model']['architecture'][:28]:<28} n={r['n']} macroF1={r['macro_f1']} {r['macro_f1_ci95']} bAcc={r['balanced_accuracy']} ECE={r['calibration']['ece']} "
              f"gates_passed={r['release_gates']['passed']} reproduces_card={r['reproduces_model_card']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

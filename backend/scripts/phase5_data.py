"""Acquire and import the datasets that passed the licence gate (Phase 5).

    python -m scripts.phase5_data isic-clinical isic-dermoscopy scin derm12345

For each name: refuse if the registry's licence gate does not allow training on it, download from the official source (resumable, every file verified), lay the files out, and import them
through the standard importer (exact-duplicate removal, decode check, SHA-256 of every file, perceptual-hash flags, versioned manifest). Prints a summary; nothing is trained here."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.config import DATASET_DIR  # noqa: E402
from app.datasets import importer, registry, sources as S  # noqa: E402

ACTOR = "phase5-agent"
# classes that are over-represented are sub-sampled (deterministically); everything else is kept. The caps keep feature extraction within what this CPU can do in hours.
CAPS = {"isic-dermoscopy": {"NV": 3500, "BKL": 2500}}


def log(m: str):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def build(name: str) -> dict:
    rec = registry.get(name)
    gate = registry.license_gate(rec, "model_training", commercial=True)
    if not gate["allowed"]:
        raise SystemExit(f"{name}: the licence gate does not allow this dataset: {gate['reasons']}")
    root = DATASET_DIR / name
    root.mkdir(parents=True, exist_ok=True)
    registry.set_import_status(name, "importing", "Phase 5 acquisition")
    t0 = time.time()
    if name == "isic-clinical":
        rep = S.build_isic("clinical", name, CAPS.get(name), progress=log)
    elif name == "isic-dermoscopy":
        rep = S.build_isic("dermoscopy", name, CAPS.get(name), progress=log, workers=12)
    elif name == "scin":
        rep = S.build_scin(name, progress=log)
    elif name == "derm12345":
        rep = S.build_derm12345(name, progress=log)
    else:
        raise SystemExit(f"unknown dataset {name}")
    fails = rep.get("download", {}).get("failed_count", 0)
    log(f"{name}: acquisition done in {time.time() - t0:.0f}s; selected {rep.get('selected', rep.get('images', '?'))}; download failures {fails}")
    if fails:
        log(f"  first failures: {rep['download']['failed'][:3]}")
    res = importer.ingest_folder(name, root / "images", ACTOR, root / "labels.csv")
    registry.set_import_status(name, "imported" if res["complete"] else "incomplete", f"version {res['version']}, {res['count']} images")
    m = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (res["version_id"],))["manifest"])
    log(f"{name}: imported version {res['version']} ({res['version_id']}): {res['count']} images, {m['subjects']} subjects; duplicates skipped {res['duplicates_skipped']}, "
        f"label conflicts excluded {res['label_conflicts_excluded']}, near-duplicates flagged {res['near_duplicates_flagged']}, decode errors {len(res['errors'])}")
    log(f"  class distribution: { {k: v['n'] for k, v in m['class_distribution'].items()} }")
    log(f"  Fitzpatrick: { {k: v['n'] for k, v in m['fitzpatrick_distribution'].items()} }")
    return {"dataset": name, "version_id": res["version_id"], "version": res["version"], "count": res["count"], "subjects": m["subjects"], "acquisition": rep}


def main(argv=None) -> int:
    names = (argv if argv is not None else sys.argv[1:]) or ["isic-clinical", "isic-dermoscopy", "scin", "derm12345"]
    db.init()
    registry.seed()
    summary = []
    for n in names:
        try:
            summary.append(build(n))
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 - continue with the next dataset; report this one
            registry.set_import_status(n, "failed", str(e)[:150])
            log(f"{n}: FAILED {type(e).__name__}: {str(e)[:300]}")
            summary.append({"dataset": n, "error": str(e)[:300]})
    out = DATASET_DIR / "_phase5_import_summary.json"
    out.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    log(f"summary written to {out}")
    return 0 if all("error" not in s for s in summary) else 1


if __name__ == "__main__":
    sys.exit(main())

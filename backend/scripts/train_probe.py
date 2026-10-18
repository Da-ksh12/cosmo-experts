"""Train the DINOv2 linear probe on a registered dataset version (research only).
Usage: python -m scripts.train_probe <dataset_version_id> [--dataset pad-ufes-20] [--ood-dir data/ood_eval]
Requires: pretrained weights + provenance in data/models/pretrained/dinov2_small/ (see DATASET_CATALOG / MODEL_REGISTRY for the source and licence)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.training import probe  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("version_id")
ap.add_argument("--dataset", default="pad-ufes-20")
ap.add_argument("--task", default="lesion_classification_research")
ap.add_argument("--ood-dir", default="data/ood_eval")
ap.add_argument("--seed", type=int, default=17)
a = ap.parse_args()
db.init()
prov = json.load(open("data/models/pretrained/dinov2_small/provenance.json"))
d = json.loads(db.q1("SELECT data FROM datasets WHERE id=?", (a.dataset,))["data"])
lic = {"spdx": d.get("verified_spdx"), "terms_snapshot_hash": d.get("terms_snapshot_hash"), "official_license_name": d.get("official_license_name"), "source": d.get("url"), "attribution": d.get("attribution")}
card = probe.train_probe(a.task, a.dataset, a.version_id, "ml-engineer-cli", "data/models/pretrained/dinov2_small/model.safetensors", prov, seed=a.seed, ood_dir=a.ood_dir,
                         license_info=lic, resnet_baseline="data/models/pretrained/resnet18_tv_in1k.pth", progress=lambda e: print(e["log"], flush=True) if "log" in e else None)
t = card["test_metrics"]
print(json.dumps({"model": card["model_id"], "macro_f1": t["macro_f1"], "ci": t["macro_f1_ci95"], "balanced_accuracy": t["balanced_accuracy"], "accuracy": t["accuracy"], "ece": t["ece"]}))

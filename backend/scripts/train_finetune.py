"""Fine-tune ResNet18 on a registered dataset version, with the SAME settings as the first research run (for reproducibility checks).
Usage: python -m scripts.train_finetune <dataset_version_id> [--compare <model_id>]"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.training import trainer  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("version_id")
ap.add_argument("--dataset", default="pad-ufes-20")
ap.add_argument("--task", default="lesion_classification_research")
ap.add_argument("--compare", default=None, help="model id whose metrics/artifact the new run should reproduce")
a = ap.parse_args()
db.init()
prov = json.load(open("data/models/pretrained/resnet18_tv_in1k.provenance.json"))
d = json.loads(db.q1("SELECT data FROM datasets WHERE id=?", (a.dataset,))["data"])
lic = {"spdx": d.get("verified_spdx"), "terms_snapshot_hash": d.get("terms_snapshot_hash"), "official_license_name": d.get("official_license_name"), "source": d.get("url"), "attribution": d.get("attribution")}
card = trainer.train(a.task, a.dataset, a.version_id, "ml-engineer-cli", arch="resnet18", epochs=6, lr=5e-4, batch=32, image_size=224, finetune="layer4", seed=17,
                     weights_path="data/models/pretrained/resnet18_tv_in1k.pth", pretrained_note=f"{prov['source']} @ {prov['commit'][:10]}", license_info=lic,
                     ood_dir="data/ood_eval", progress=lambda e: print(e["log"], flush=True) if "log" in e else None)
t = card["test_metrics"]
res = {"model": card["model_id"], "macro_f1": t["macro_f1"], "ci": t["macro_f1_ci95"], "balanced_accuracy": t["balanced_accuracy"], "accuracy": t["accuracy"], "artifact_sha256": card["artifact_sha256"]}
if a.compare:
    old = json.loads(db.q1("SELECT data FROM model_versions WHERE id=?", (a.compare,))["data"])
    res["reference"] = {"model": a.compare, "macro_f1": old["test_metrics"]["macro_f1"], "accuracy": old["test_metrics"]["accuracy"], "artifact_sha256": old["artifact_sha256"]}
    res["metrics_identical"] = all(old["test_metrics"][k] == t[k] for k in ("accuracy", "macro_f1", "balanced_accuracy", "macro_f1_ci95"))
    res["artifact_identical"] = old["artifact_sha256"] == card["artifact_sha256"]
    res["split_identical"] = old["split_hash"] == card["split_hash"]
print("RESULT " + json.dumps(res))

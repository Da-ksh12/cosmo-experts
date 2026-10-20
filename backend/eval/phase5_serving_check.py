"""Does SERVING reproduce the EVALUATION for the Phase 5 models? For each experiment: take a deterministic sample of its held-out test images, send the raw files (PNG or JPEG, not the training cache)
through `serving.predict`, and compare the probabilities with the ones the evaluation path computes from the cached features and the saved head. Both paths must agree; a difference
would mean the model answers differently from how it was measured.

Usage: python -m eval.phase5_serving_check <experiment> [<experiment> ...] [--n 60]     (writes docs/eval/phase5_serving_consistency.json)"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.training import featuretrain as FT, features as FE, probe, serving, trainer as T  # noqa: E402


def check(experiment: str, n: int) -> dict:
    row = next(r for r in db.q("SELECT id, data FROM model_versions ORDER BY created_at DESC") if json.loads(r["data"]).get("experiment") == experiment)
    card = json.loads(row["data"])
    specs = [FT.SourceSpec(s["name"], s["dataset"], s["version_id"], s["role"], s["unknown_group_train_only"], s["split_policy"]) for s in card["sources"]]
    srcs = [FT.load_source(s) for s in specs]
    splits, _, _ = FT.assemble(srcs, card["seed"])
    test = splits["test"]
    pick = test[:: max(1, len(test) // n)][:n]
    by = {s["spec"].name: s for s in srcs}
    z = np.load(card["artifact_path"], allow_pickle=False)
    sl = slice(0, 384 if card["feature_kind"] == "cls" else 768)
    feats = {}
    for name, s in by.items():
        X = FE.image_cache(s["mf"]["root"], s["entries"])
        feats[name] = FE.extract_features("dinov2_s14", X, s["entries"])            # complete from the training run: returned without recomputing
    worst, agree, kinds = 0.0, 0, {}
    t0 = time.time()
    for e in pick:
        f = np.asarray(feats[e["_src"]][e["_row"]], np.float32)[None, sl]
        p_eval = T.softmax(probe.head_logits((f - z["mean"]) / z["scale"], z["coef"], z["intercept"]), float(z["temperature"]))[0]
        data = (Path(by[e["_src"]]["mf"]["root"]) / e["file"]).read_bytes()
        out = serving.predict(row["id"], data, enforce_quality=False)
        p_serve = np.array([out["all_probabilities"][c] for c in card["classes"]])
        worst = max(worst, float(np.abs(p_serve - p_eval).max()))
        agree += int(p_serve.argmax() == p_eval.argmax())
        kinds[Path(e["file"]).suffix.lower()] = kinds.get(Path(e["file"]).suffix.lower(), 0) + 1
    return {"experiment": experiment, "model": row["id"], "images_checked": len(pick), "file_types": kinds, "top1_agreement": f"{agree}/{len(pick)}", "max_abs_probability_difference": round(worst, 5),
            "note": "evaluation path = cached features (PIL decode) + saved head; serving path = raw file (OpenCV decode) through serving.predict; probabilities are rounded to 4 decimals by serving",
            "seconds": round(time.time() - t0)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("experiments", nargs="+")
    ap.add_argument("--n", type=int, default=60)
    a = ap.parse_args(argv)
    db.init()
    res = [check(e, a.n) for e in a.experiments]
    for r in res:
        print(json.dumps(r))
    dest = Path(__file__).resolve().parent.parent.parent / "docs" / "eval" / "phase5_serving_consistency.json"
    dest.write_text(json.dumps({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": res}, indent=1), encoding="utf-8")
    print("written", dest)
    return 0 if all(r["top1_agreement"].split("/")[0] == r["top1_agreement"].split("/")[1] and r["max_abs_probability_difference"] < 0.02 for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())

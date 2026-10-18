"""Evaluation of the quality-2.0 checks (app/imaging/quality.py) on REAL photographs (PAD-UFES-20 smartphone lesion close-ups, CC BY 4.0, patient-level splits) plus SIMULATED faults.

    python -m eval.quality_v2_eval [--limit N] [--workers 2] [--out ../docs/eval/quality_v2.json]

Criteria, fixed BEFORE looking at results (they decide whether a check may ever reject a photo; a check that misses them stays an advisory warning):
  false-flag rate on usable photos  <= 3 %   (validation + test splits, thresholds fitted on the TRAIN split only)
  detection of simulated faults     >= 80 %  (faults covering >= 20 % of the frame, test split)
This does not weaken or change any existing rule. It evaluates skin close-ups only: faces and hair/scalp have no real photographs here, so nothing is validated for them.
Evidence class: public research dataset (real photographs) for false flags; SIMULATED degradations for detection; synthetic fixtures for the document check."""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CRIT_FALSE_FLAG, CRIT_DETECT = 0.03, 0.80
SEED = 17


def load_entries():
    import sqlite3
    from app.config import DB_PATH
    c = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    row = c.execute("select manifest from dataset_versions where dataset_id='pad-ufes-20' order by created_at desc limit 1").fetchone()
    if not row:
        raise SystemExit("No PAD-UFES-20 dataset version is registered in the development database.")
    m = json.loads(row[0])
    return [e for e in m["entries"] if e.get("label")], m["root"]


def features(args):
    from app.imaging import quality, suitability
    path, = args
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    return {"min_side": int(min(img.shape[:2])), "obstruction": quality.obstruction_fraction(img), "center_skin": quality.center_skin_fraction(img),
            "doc_words": quality.document_words(img), "blockiness": round(suitability.blockiness(img), 3)}


# ---------------------------------------------------------------- simulated faults
def occlude(img: np.ndarray, kind: str, frac: float) -> np.ndarray:
    out = img.copy()
    H, W = out.shape[:2]
    if kind == "black_corner":
        s = np.sqrt(frac)
        out[: int(H * s), : int(W * s)] = 0
    elif kind == "finger_blob":
        skin = (np.array(out.reshape(-1, 3).mean(0)) * 0.8).astype(np.uint8)
        mask = np.zeros((H, W), np.uint8)
        cv2.ellipse(mask, (W // 2, H), (int(W * 0.5), int(H * frac * 2.0)), 0, 0, 360, 255, -1)
        blob = np.full_like(out, skin)
        blob = cv2.GaussianBlur(blob, (0, 0), 15)
        m = (cv2.GaussianBlur(mask, (0, 0), 6) / 255.0)[..., None]
        out = (out * (1 - m) + blob * m).astype(np.uint8)
    elif kind == "glare":
        mask = np.zeros((H, W), np.uint8)
        cv2.ellipse(mask, (W, H // 2), (int(W * frac * 1.6), int(H * 0.5)), 0, 0, 360, 255, -1)
        m = (cv2.GaussianBlur(mask, (0, 0), 8) / 255.0)[..., None]
        out = (out * (1 - m) + 255 * m).astype(np.uint8)
    return out


def shift_subject(img: np.ndarray, frac: float = 0.4) -> np.ndarray:
    """Subject pushed to one side: the frame's middle now shows plain background."""
    H, W = img.shape[:2]
    out = np.full_like(img, 128)
    dx = int(W * frac)
    out[:, dx:] = img[:, : W - dx]
    return out


def reencode(img: np.ndarray, q: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes()


def synthetic_report_photo(seed: int) -> np.ndarray:
    import pymupdf
    rng = np.random.default_rng(seed)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    names = ["Haemoglobin", "Ferritin", "TSH", "Vitamin D", "Vitamin B12", "Creatinine", "Glucose Fasting", "Total Cholesterol", "ALT", "AST", "Platelet Count", "WBC Count"]
    y = 60
    page.insert_text((60, y), "CITY DIAGNOSTIC LABORATORY   Report Date: 12/03/2024", fontsize=11)
    for n in names:
        y += 26
        page.insert_text((60, y), f"{n:<22} {rng.uniform(1, 200):6.1f}   mg/dL      {rng.integers(1, 20)} - {rng.integers(30, 300)}", fontsize=10)
    pix = page.get_pixmap(dpi=150)
    img = cv2.cvtColor(np.array(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")), cv2.COLOR_RGB2BGR)
    a = float(rng.uniform(-3, 3))
    M = cv2.getRotationMatrix2D((img.shape[1] / 2, img.shape[0] / 2), a, 1.0)
    img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderValue=(235, 235, 235))
    img = cv2.GaussianBlur(img, (0, 0), 0.8)
    img = np.clip(img.astype(np.float32) * rng.uniform(0.75, 1.0), 0, 255).astype(np.uint8)
    return cv2.imdecode(np.frombuffer(reencode(img, int(rng.integers(40, 80))), np.uint8), cv2.IMREAD_COLOR)


# ---------------------------------------------------------------- analysis
def rate(a, b):
    return round(a / b, 4) if b else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / "docs" / "eval" / "quality_v2.json")
    a = ap.parse_args(argv)
    from app.imaging import quality, suitability
    from app.training import trainer
    entries, root = load_entries()
    splits = trainer.stratified_group_split(entries, SEED)
    if a.limit:
        splits = {k: v[: a.limit] for k, v in splits.items()}
    t0 = time.time()
    feats = {}
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for name, ents in splits.items():
            feats[name] = list(ex.map(features, [(str(Path(root) / e["file"]),) for e in ents], chunksize=8))
            print(f"{name}: {len(ents)} photos in {time.time() - t0:.0f}s", flush=True)
    col = lambda split, k: np.array([f[k] for f in feats[split]], float)
    cal, report = {}, {}

    # ---- resolution (advisory): report how often it would fire
    for sp in splits:
        report.setdefault("resolution_advisory_rate", {})[sp] = rate(int((col(sp, "min_side") < quality.RECOMMENDED_MIN_SIDE).sum()), len(feats[sp]))

    # ---- obstruction
    thr_candidates = [0.12, 0.15, 0.2, 0.25, 0.3]
    tr = col("train", "obstruction")
    ob = {"train_percentiles": {str(p): round(float(np.percentile(tr, p)), 4) for p in (50, 90, 95, 99)}}
    # fit: the smallest candidate whose TRAIN false-flag rate is within the criterion
    chosen = next((t for t in thr_candidates if (tr >= t).mean() <= CRIT_FALSE_FLAG), None)
    ob["candidate_false_flag_train"] = {str(t): round(float((tr >= t).mean()), 4) for t in thr_candidates}
    if chosen is not None:
        ob["threshold"] = chosen
        ob["false_flag"] = {sp: round(float((col(sp, "obstruction") >= chosen).mean()), 4) for sp in ("train", "val", "test")}
        det = {}
        test_files = [str(Path(root) / e["file"]) for e in splits["test"]]
        for kind in ("black_corner", "finger_blob", "glare"):
            for frac in (0.10, 0.20, 0.30):
                hit = 0
                for pth in test_files:
                    img = cv2.imdecode(np.fromfile(pth, dtype=np.uint8), cv2.IMREAD_COLOR)
                    hit += quality.obstruction_fraction(occlude(img, kind, frac)) >= chosen
                det[f"{kind}@{int(frac * 100)}%"] = round(hit / len(test_files), 4)
        ob["detection_simulated"] = det
        big = [v for k, v in det.items() if not k.endswith("@10%")]
        ob["detection_at_least_20pct_coverage_mean"] = round(float(np.mean(big)), 4)
        ob["detection_at_least_20pct_coverage_min"] = round(float(np.min(big)), 4)
        ff_ok = max(ob["false_flag"]["val"], ob["false_flag"]["test"]) <= CRIT_FALSE_FLAG
        ob["validated"] = bool(ff_ok and ob["detection_at_least_20pct_coverage_mean"] >= CRIT_DETECT)
    else:
        ob["validated"] = False
        ob["threshold"] = 0.12
    ob["basis"] = ("PAD-UFES-20 lesion close-ups: threshold fitted on the train split; false flags on val/test; detection on simulated occluders (test split)"
                   if ob["validated"] else "advisory only: did not meet the validation criteria (see numbers); not applied as a rejection")
    cal["obstruction"] = {k: ob[k] for k in ("threshold", "validated", "basis")}
    report["obstruction"] = ob

    # ---- centre skin (framing)
    ct = col("train", "center_skin")
    cs = {"train_percentiles": {str(p): round(float(np.percentile(ct, p)), 4) for p in (1, 3, 5, 10, 50)}}
    cand = [0.05, 0.10, 0.15, 0.20, 0.30]
    cs["candidate_false_flag_train"] = {str(t): round(float((ct < t).mean()), 4) for t in cand}
    chosen_c = max((t for t in cand if (ct < t).mean() <= CRIT_FALSE_FLAG), default=None)
    if chosen_c is not None:
        cs["threshold"] = chosen_c
        cs["false_flag"] = {sp: round(float((col(sp, "center_skin") < chosen_c).mean()), 4) for sp in ("train", "val", "test")}
        hit = 0
        test_files = [str(Path(root) / e["file"]) for e in splits["test"]]
        for pth in test_files:
            img = cv2.imdecode(np.fromfile(pth, dtype=np.uint8), cv2.IMREAD_COLOR)
            hit += quality.center_skin_fraction(shift_subject(img)) < chosen_c
        cs["detection_simulated_subject_shifted_40pct"] = round(hit / len(test_files), 4)
        cs["validated"] = bool(max(cs["false_flag"]["val"], cs["false_flag"]["test"]) <= CRIT_FALSE_FLAG and cs["detection_simulated_subject_shifted_40pct"] >= CRIT_DETECT)
    else:
        cs["validated"], cs["threshold"] = False, 0.15
    cs["basis"] = ("PAD-UFES-20 lesion close-ups: threshold fitted on train; false flags on val/test; detection of a subject shifted 40 % off-centre (simulated)"
                   if cs["validated"] else "advisory only: did not meet the validation criteria; not applied as a rejection")
    cal["center_skin"] = {k: cs[k] for k in ("threshold", "validated", "basis")}
    report["center_skin"] = cs

    # ---- compression estimator accuracy and advisory rate (test split, re-encoded at known qualities)
    test_files = [str(Path(root) / e["file"]) for e in splits["test"]][:120]
    err = {}
    flagged = {}
    for q in (95, 85, 75, 60, 40, 20):
        es, fl = [], 0
        for pth in test_files:
            img = cv2.imdecode(np.fromfile(pth, dtype=np.uint8), cv2.IMREAD_COLOR)
            data = reencode(img, q)
            est = quality.estimate_jpeg_quality(data)
            es.append(abs(est - q))
            fl += (est < quality.JPEG_QUALITY_WARN) or (suitability.blockiness(cv2.imdecode(np.frombuffer(data, np.uint8), 1)) >= suitability.BLOCKINESS_WARN)
        err[str(q)] = {"mean_abs_error": round(float(np.mean(es)), 2), "max_abs_error": round(float(np.max(es)), 2)}
        flagged[str(q)] = round(fl / len(test_files), 4)
    report["compression"] = {"estimator_error_vs_true_quality": err, "advisory_flag_rate_by_true_quality": flagged,
                             "note": "Real photographs re-encoded at known libjpeg qualities; the estimator uses the standard IJG table ratio. Camera-specific tables (not measured here) may differ. Advisory only."}

    # ---- document-like detection
    prefilter_and_words = np.concatenate([col(sp, "doc_words") for sp in splits])
    fp = int((prefilter_and_words >= quality.DOCUMENT_MIN_WORDS).sum())
    n_syn = 12
    syn = [quality.document_words(synthetic_report_photo(i)) for i in range(n_syn)]
    report["document_like"] = {"false_positives_on_skin_photographs": fp, "skin_photographs": int(len(prefilter_and_words)), "false_positive_rate": rate(fp, len(prefilter_and_words)),
                               "word_counts_on_synthetic_report_photos": syn, "detected_synthetic_report_photos": int(sum(w >= quality.DOCUMENT_MIN_WORDS for w in syn)),
                               "synthetic_report_photos": n_syn, "evidence_class": "false positives: real skin photographs; detection: synthetic fixtures (rendered lab-style pages with rotation, blur, dimming, JPEG)"}
    out = {"schema": "cosmo-eval-1", "name": "quality_v2", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "quality_version": quality.VERSION, "suitability_version": suitability.VERSION,
           "evidence_class": "public_research_dataset (real photographs) + simulated faults + synthetic fixtures", "dataset": {"id": "pad-ufes-20", "license": "CC BY 4.0", "photographs": sum(len(v) for v in splits.values()),
           "split_seed": SEED, "split_sizes": {k: len(v) for k, v in splits.items()}, "limit": a.limit or None},
           "criteria": {"false_flag_rate_max": CRIT_FALSE_FLAG, "detection_rate_min": CRIT_DETECT, "applied_to": "val and test splits (false flags), test split (detection)"},
           "scope": "Skin close-ups only. Nothing here is validated for faces or hair/scalp (no real photographs available); those profiles keep their advisory-only status.",
           "calibration": cal, "results": report, "runtime_seconds": round(time.time() - t0)}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"calibration": cal, "runtime_seconds": out["runtime_seconds"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

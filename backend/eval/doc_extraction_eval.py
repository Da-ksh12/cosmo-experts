"""Measured evaluation of the document-extraction pipeline on SYNTHETIC reports with exact ground truth.

No real patient data is used. Reports are generated from a catalogue of common laboratory analytes (names/units/typical ranges
are generic and chosen for layout realism only; they are not clinical reference data), rendered as digital PDFs and as
degraded scans (rotation, blur, noise + JPEG, low resolution). The same pipeline that serves patients is run on every case.

Usage:  python -m eval.doc_extraction_eval --docs 16 --seed 7 --out ../docs/eval
"""
from __future__ import annotations

import argparse
import io
import json
import random
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.documents import pipeline  # noqa: E402

CATALOGUE = [  # name, unit, lo, hi, decimals
    ("Hemoglobin", "g/dL", 12.0, 15.5, 1), ("Ferritin", "ng/mL", 15, 150, 0), ("TSH", "mIU/L", 0.4, 4.0, 2), ("Vitamin D", "ng/mL", 30, 100, 1),
    ("Vitamin B12", "pg/mL", 200, 900, 0), ("Glucose Fasting", "mg/dL", 70, 99, 0), ("Creatinine", "mg/dL", 0.6, 1.2, 2), ("Total Cholesterol", "mg/dL", 125, 200, 0),
    ("HDL Cholesterol", "mg/dL", 40, 80, 0), ("Triglycerides", "mg/dL", 50, 150, 0), ("SGPT", "U/L", 7, 56, 0), ("SGOT", "U/L", 10, 40, 0),
    ("Platelet Count", "10^3/uL", 150, 410, 0), ("WBC Count", "10^3/uL", 4.0, 11.0, 1), ("Hematocrit", "%", 36, 46, 1), ("Serum Iron", "ug/dL", 60, 170, 0),
    ("Calcium", "mg/dL", 8.6, 10.2, 1), ("Sodium", "mmol/L", 135, 145, 0), ("Potassium", "mmol/L", 3.5, 5.1, 1), ("Free T4", "ng/dL", 0.8, 1.8, 2),
    ("Uric Acid", "mg/dL", 3.5, 7.2, 1), ("HbA1c", "%", 4.0, 5.6, 1), ("Zinc", "ug/dL", 70, 120, 0), ("Testosterone Total", "ng/dL", 300, 1000, 0),
]


def fmt(v: float, d: int) -> str:
    return f"{v:.{d}f}"


def make_case(rng: random.Random, layout: str):
    rows = []
    for name, unit, lo, hi, d in rng.sample(CATALOGUE, rng.randint(6, 11)):
        span = hi - lo
        r = rng.random()
        v = rng.uniform(lo - 0.4 * span, lo - 0.02 * span) if r < 0.15 else rng.uniform(hi + 0.02 * span, hi + 0.5 * span) if r < 0.3 else rng.uniform(lo + 0.05 * span, hi - 0.05 * span)
        v = max(v, 0.1)
        value, ref = fmt(v, d), f"{fmt(lo, d)} - {fmt(hi, d)}"
        vf, lof, hif = float(value), float(fmt(lo, d)), float(fmt(hi, d))
        flag = "outside_printed_range" if (vf < lof or vf > hif) else "within_printed_range"
        rows.append({"name": name, "value": value, "unit": unit, "ref": ref, "expect_flag": flag, "printed_flag": "L" if vf < lof else "H" if vf > hif else ""})
    return rows


def render_pdf(rows, layout: str, lab: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    c.setFont("Helvetica-Bold", 14); c.drawString(50, y, f"{lab} DIAGNOSTIC LABORATORY - Laboratory Report"); y -= 22
    c.setFont("Helvetica", 10); c.drawString(50, y, "Report Date: 14/02/2025   Specimen: Serum"); y -= 26
    c.setFont("Helvetica-Bold", 10)
    if layout == "columns":
        c.drawString(50, y, "Test"); c.drawString(230, y, "Result"); c.drawString(300, y, "Unit"); c.drawString(380, y, "Reference range"); y -= 20
    c.setFont("Helvetica", 11)
    for r in rows:
        if layout == "columns":
            c.drawString(50, y, r["name"]); c.drawString(230, y, r["value"]); c.drawString(300, y, r["unit"]); c.drawString(380, y, r["ref"] + (f"   {r['printed_flag']}" if r["printed_flag"] else ""))
        else:   # inline
            c.drawString(50, y, f"{r['name']} : {r['value']} {r['unit']} ({r['ref']})" + (f"  {r['printed_flag']}" if r["printed_flag"] else ""))
        y -= 20
    c.save()
    return buf.getvalue()


def rasterize(pdf: bytes, dpi: int) -> np.ndarray:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
        return cv2.cvtColor(img[:, :, :3], cv2.COLOR_RGB2BGR)


def degrade(img: np.ndarray, cond: str, rng: random.Random) -> bytes:
    if cond == "scan_rot90":
        img = np.rot90(img, 1)
    elif cond == "scan_rot180":
        img = np.rot90(img, 2)
    elif cond == "scan_rot270":
        img = np.rot90(img, 3)
    elif cond == "scan_blur":
        img = cv2.GaussianBlur(img, (0, 0), 1.3)
    elif cond == "scan_noise":
        n = np.random.default_rng(rng.randint(0, 2 ** 31)).normal(0, 18, img.shape)
        img = np.clip(img + n, 0, 255).astype(np.uint8)
    q = 40 if cond == "scan_noise" else 90
    ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(img), [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes()


def norm_name(s: str) -> str:
    return " ".join(s.lower().replace("_", " ").split())


def norm_ref(s: str) -> str:
    return "".join(ch for ch in s if ch not in "() ").replace("–", "-").replace("to", "-")


def norm_unit(s: str) -> str:
    return s.replace("µ", "u").replace("μ", "u").replace(" ", "").lower()


def score(rows, fields):
    gt = {norm_name(r["name"]): r for r in rows}
    used, tp, nv, flag_ok, fp = set(), 0, 0, 0, 0
    for f in fields:
        r = gt.get(norm_name(f.name))
        if r is None or norm_name(f.name) in used:
            fp += 1
            continue
        used.add(norm_name(f.name))
        if f.value == r["value"]:
            nv += 1
            if norm_unit(f.unit) == norm_unit(r["unit"]) and norm_ref(f.ref_range) == norm_ref(r["ref"]):
                tp += 1
                exp = r["expect_flag"]
                if f.flag in (exp, "printed_flag_high", "printed_flag_low") and (exp == "within_printed_range") == (f.flag == "within_printed_range"):
                    flag_ok += 1
    return {"gt": len(rows), "extracted": len(fields), "exact": tp, "name_value": nv, "false_positive": fp, "flag_ok": flag_ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=16)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="../docs/eval")
    ap.add_argument("--conditions", default="digital,scan_clean,scan_rot90,scan_rot180,scan_rot270,scan_blur,scan_noise,scan_lowres")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    conds = a.conditions.split(",")
    ocr = pipeline.ocr_available()
    agg = {c: [] for c in conds}
    t_all = time.time()
    for i in range(a.docs):
        layout = "columns" if i % 2 == 0 else "inline"
        rows = make_case(rng, layout)
        pdf = render_pdf(rows, layout, f"CITY{i:02d}")
        for cond in conds:
            if cond != "digital" and not ocr:
                continue
            if cond == "digital":
                data, name = pdf, "r.pdf"
            else:
                dpi = 100 if cond == "scan_lowres" else 200
                data, name = degrade(rasterize(pdf, dpi), cond, rng), "r.jpg"
            t = time.time()
            try:
                res = pipeline.process(data, name)
                s = score(rows, res.fields)
                s["doc_type"] = res.doc_type
            except Exception as e:  # a crash is a result, not something to hide
                s = {"gt": len(rows), "extracted": 0, "exact": 0, "name_value": 0, "false_positive": 0, "flag_ok": 0, "error": str(e)[:120], "doc_type": ""}
            s["sec"] = time.time() - t
            agg[cond].append(s)
        print(f"doc {i + 1}/{a.docs} done ({time.time() - t_all:.0f}s)", flush=True)
    report = {"seed": a.seed, "docs": a.docs, "ocr_available": ocr, "ocr_languages": pipeline.ocr_languages(), "pipeline_version": pipeline.PROCESSING_VERSION, "conditions": {}}
    for c, items in agg.items():
        if not items:
            continue
        gt = sum(x["gt"] for x in items); ex = sum(x["extracted"] for x in items); tp = sum(x["exact"] for x in items)
        nv = sum(x["name_value"] for x in items); fp = sum(x["false_positive"] for x in items); fl = sum(x["flag_ok"] for x in items)
        report["conditions"][c] = {"n_docs": len(items), "gt_rows": gt, "extracted_rows": ex, "recall_exact": round(tp / gt, 3), "precision_exact": round(tp / ex, 3) if ex else None,
                                   "recall_name_value": round(nv / gt, 3), "false_positive_rows": fp, "flag_accuracy_on_exact": round(fl / tp, 3) if tp else None,
                                   "doc_type_laboratory_rate": round(sum(1 for x in items if x["doc_type"] == "laboratory_report") / len(items), 3),
                                   "crashes": sum(1 for x in items if x.get("error")), "mean_sec": round(statistics.mean(x["sec"] for x in items), 2)}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "doc_extraction.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["| Condition | Docs | GT rows | Recall (exact) | Precision (exact) | Value-only recall | FP rows | Flag acc. | Lab-type | Crashes | s/doc |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, m in report["conditions"].items():
        lines.append(f"| {c} | {m['n_docs']} | {m['gt_rows']} | {m['recall_exact']} | {m['precision_exact']} | {m['recall_name_value']} | {m['false_positive_rows']} | {m['flag_accuracy_on_exact']} | {m['doc_type_laboratory_rate']} | {m['crashes']} | {m['mean_sec']} |")
    (out / "doc_extraction.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

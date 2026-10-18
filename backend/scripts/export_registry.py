"""Generates MODEL_REGISTRY.md from the live registry (model_versions) and the model files on disk, so documented numbers cannot drift from
what was measured. Usage: python -m scripts.export_registry ../MODEL_REGISTRY.md"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.config import MODEL_DIR  # noqa: E402
from app.training import trainer as T  # noqa: E402

STATIC = [  # name, role, file (relative to MODEL_DIR), source, licence, intended use, limitations
    ("Tesseract OCR 5.4.0", "Scanned-document text recognition", None, "https://github.com/UB-Mannheim/tesseract (winget UB-Mannheim.TesseractOCR)", "Apache-2.0",
     "Read printed lab reports/prescriptions; output is reviewed by a clinician", "English + OSD only installed; Hindi traineddata not installed; accuracy in docs/eval/doc_extraction.md"),
    ("BGE-small-en-v1.5", "Passage/query embeddings for retrieval", "embed/bge-small-en-v1.5/model.safetensors", "https://huggingface.co/BAAI/bge-small-en-v1.5", "MIT",
     "Rank the patient's own document lines and approved articles", "English; abstention threshold calibrated on synthetic data (docs/eval/retrieval.md)"),
    ("faster-whisper base (CTranslate2, int8)", "Speech-to-text", "stt/faster-whisper-base/model.bin", "https://huggingface.co/Systran/faster-whisper-base", "MIT",
     "Let a patient dictate a question; transcript is shown for correction before sending", "Evaluated only on synthetic English speech; Hindi/Hinglish unverified"),
    ("Qwen2.5-1.5B-Instruct Q4_K_M", "Optional claim drafting (OFF by default)", "llm/qwen2.5-1.5b-instruct-q4_k_m.gguf", "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF", "Apache-2.0",
     "Experimental draft of source-cited claims, always verified before display", "Measured as NOT adequate (docs/eval/llm_*.json); 3-4 tokens/s on 2 CPU cores"),
    ("ResNet18 (ImageNet-1k, torchvision weights via timm)", "Backbone for baseline and fine-tuning experiments", "pretrained/resnet18_tv_in1k.pth", "https://huggingface.co/timm/resnet18.tv_in1k", "BSD-3-Clause (weights trained on ImageNet-1k; upstream data terms need legal review for commercial use)",
     "Research backbone only", "—"),
    ("DINOv2 ViT-S/14 (timm)", "Frozen backbone for the lesion linear probe", "pretrained/dinov2_small/model.safetensors", "https://huggingface.co/timm/vit_small_patch14_dinov2.lvd142m", "Apache-2.0 (trained on Meta's LVD-142M; data terms not independently reviewed)",
     "Research backbone only", "—"),
]


def f(v, nd=3):
    return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def main(out_path: str):
    L = ["# Model Registry", "", "Generated from the live registry by `backend/scripts/export_registry.py` on the machine where the models were trained; "
         "numbers are copied from model cards, not typed. **No model here is approved for patient use.**", ""]
    L += ["## Models used by the product (not trained here)", "", "| Model | Role | Licence | Source | sha256 (file) | Intended use | Known limitations |", "|---|---|---|---|---|---|---|"]
    for name, role, rel, src, lic, use, lim in STATIC:
        sha = T.sha256_file(MODEL_DIR / rel)[:16] + "…" if rel and (MODEL_DIR / rel).exists() else ("n/a (installed binary)" if rel is None else "not downloaded")
        L.append(f"| {name} | {role} | {lic} | {src} | {sha} | {use} | {lim} |")
    L += ["", "## Image classifiers trained in this repository", ""]
    rows = db.q("SELECT * FROM model_versions ORDER BY created_at")
    if not rows:
        L.append("None registered.")
    for r in rows:
        c = json.loads(r["data"]); t = c["test_metrics"]
        c["task"] = r["task"]
        gates = T.evaluate_gates(c)
        L += [f"### {c['model_id']}  — {c['architecture']}", "",
              f"* **Status:** `{r['status']}` (research only; intended use: {c['intended_use']})",
              f"* **Task:** {c['task']} · classes {', '.join(c['classes'])}",
              f"* **Dataset:** {c['dataset_id']} v{c['dataset_version']} (licence {c.get('dataset_license', {}).get('official_license_name') or c.get('dataset_license', {}).get('spdx')}); split images {c['split_sizes']}; patients {c.get('patients_per_split')}",
              f"* **Training:** seed {c['seed']}, device {c['device']}, image {c['image_size']}px, fine-tune `{c['finetune']}`, epochs {c['epochs']}, {c.get('training_seconds')} s; code revision `{c.get('code_revision')}`",
              f"* **Pretrained weights:** `{c['pretrained_weights']}` sha256 `{(c.get('pretrained_sha256') or '')[:16]}…`",
              f"* **Artifact:** `{Path(c['artifact_path']).name}` sha256 `{c['artifact_sha256'][:16]}…`",
              f"* **Held-out test (n={t['n']}):** macro-F1 **{t['macro_f1']}** (95% CI {t.get('macro_f1_ci95')}), balanced accuracy {t.get('balanced_accuracy')}, accuracy {t['accuracy']}, ECE {t.get('ece')} (uncalibrated {t.get('ece_uncalibrated')}, T={t.get('temperature')})",
              "* **Baselines on the same test set:** " + "; ".join(f"{k}: macro-F1 {b['macro_f1']}" for k, b in c.get("baselines", {}).items()), "",
              "| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |", "|---|---|---|---|---|---|---|"]
        for k, v in t["per_class"].items():
            L.append(f"| {k} | {v['n']} | {f(v['sensitivity'])} | {f(v['specificity'])} | {f(v['precision'])} | {f(v['auroc'])} | {v['false_negatives']} |")
        L += ["", "Subgroups (Fitzpatrick): " + "; ".join(f"{g}: n={v['n']}, acc {v['accuracy']}{'' if v['reliable'] else ' (unreliable)'}" for g, v in t["subgroups_by_fitzpatrick"].items()),
              "", "Robustness (macro-F1): " + "; ".join(f"{k} {v['macro_f1']}" for k, v in t["robustness"].items())]
        if t.get("ood"):
            o = t["ood"]
            L.append(f"\nOut-of-distribution: {o['n']} non-lesion photos got mean confidence {o['mean_confidence']} vs {o['in_distribution_mean_confidence']} for real test images; "
                     f"AUROC of confidence for telling them apart = **{o['auroc_confidence_separates_in_vs_out']}** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).")
        L += ["", "| Release gate | Result | Detail |", "|---|---|---|"] + [f"| {g['gate']} | {'PASS' if g['passed'] else '**FAIL**'} | {g['detail']} |" for g in gates]
        L += ["", "**Known limitations:** " + "; ".join(c.get("limitations", [])), ""]
    Path(out_path).write_text("\n".join(L), encoding="utf-8")
    print("written", out_path, len(rows), "models")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../MODEL_REGISTRY.md")

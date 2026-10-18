# AI Architecture — Cosmo Experts AI

Status date: 2026-10-17. Everything below was inspected or measured in this repository; where something is **not** implemented or **not**
verified it says so. Measured results live in `docs/eval/` and are reproducible with the commands shown.

## 0. Phase-zero inventory (what actually exists)

| Area | Reality |
|---|---|
| Repository | Not a git repository. Backend `backend/` (FastAPI, SQLite, Python 3.11 venv), frontend `frontend/` (Next.js 16, Tailwind 4), Supabase SQL in `supabase/migrations` (**not applied** to any project). |
| Hardware here | Intel i5-5350U, 2 cores / 4 threads, 7.9 GB RAM, no GPU, 165 GB free disk. CPU-only. This bounds every model choice below. |
| Auth / RBAC | Local HMAC tokens + PBKDF2 users (`patient`, `doctor`, `admin`); role checks on every route; tests prove tenant isolation. No separate `ml_engineer` role (admin covers it). Supabase Auth not wired. |
| Storage | Private upload directory (never served statically), authorised download endpoints, SQLite. |
| AI before this phase | Heuristic image validator (OpenCV + Haar face detector), regex/rule document extraction, TF-IDF retrieval, extractive answers, OS offline TTS. **No trained model existed.** |
| Mocked / simulated | Nothing returns fabricated predictions. Unimplemented tasks raise `NotImplementedError` (tested). |

## 1. Services and interfaces

```
Browser ──/api──▶ FastAPI app ──┬─ Gateway /api/v1 (jobs, SSE, health, metrics)  ─┐
                                ├─ Patient / Clinic / Admin routes                │ shared
                                └─ services/core.py  ◀───────────────────────────┘ business logic
 A Image understanding   imaging/suitability.py          OpenCV + Haar + heuristics (task registry in training/tasks.py)
 B Document intelligence documents/pipeline.py           PyMuPDF text (word-box line rebuild) + Tesseract OCR + parsers
 C Conversational        assistant/engine.py             guardrails → retrieval → extractive answer (default) | verified-LLM (optional)
 D Knowledge retrieval   assistant/retrieval.py          BGE-small embeddings (MIT) + TF-IDF fallback, content-hash cache
 E Voice                 speech/adapters.py              faster-whisper base (STT) · pyttsx3 offline OS voices (TTS)
 F Training platform     training/trainer.py, datasets/  licence gate, import, splits, baselines, calibration, registry, gates, rollback
 G Inference gateway     gateway/jobs.py, gateway/api.py durable queue, timeouts, cancel, idempotency, SSE, health, metrics
 H Clinician review      routers/staff.py, routers/care.py  field review, care plans (doctor-only approval), audit
```

Every service is behind a small interface and replaceable: `Embedder`, `LLMBackend` (llama.cpp in-process, or any self-hosted
OpenAI-compatible URL), `TTSAdapter`, retrieval strategy (`HybridRetriever(dense_weight)`), dataset source adapters.

## 2. Component decisions and evidence

| Component | Choice | Licence | Evidence (see `docs/eval/`) | Limitation |
|---|---|---|---|---|
| OCR | Tesseract 5.4 (installed via winget, Apache-2.0) with orientation fix (OSD), upscale, denoise | Apache-2.0 | `doc_extraction.md`: digital PDF recall/precision 1.00/1.00; scans ≈0.83/0.86 incl. 90/180/270° rotation, blur, noise, low-res | Synthetic layouts we generated; English only (`eng`,`osd` installed; Hindi OCR **not** installed/verified) |
| Retrieval | BGE-small-en-v1.5 dense, abstention threshold calibrated | MIT | `retrieval.md`: Hit@1 0.986 vs TF-IDF 0.80; AUROC 0.994; 0/120 out-of-scope answered | 10 synthetic docs; English; threshold must be re-fitted if models/corpus change |
| STT | faster-whisper `base`, int8, CPU | MIT | `stt.md`: WER ≈ 5–12 % on synthetic English speech | Synthetic voices only. **Hindi/Hinglish not verified** (no Hindi voice/audio available) |
| TTS | pyttsx3 → OS offline voices | MPL-2.0 | Works; refuses languages without an installed voice | Only 3 English voices installed here; Hindi falls back to browser local voice or text |
| LLM | Qwen2.5-1.5B-Instruct Q4_K_M (Apache-2.0) via llama.cpp | Apache-2.0 | `llm_*.json`: **not adequate** — only ~45 % of its claims were supported by their cited source; 20 % correct on answerable questions after verification | 3.5 tok/s on this CPU. **Disabled by default**; assistant stays extractive |
| Image classifiers (research) | ResNet18 fine-tune and DINOv2-S/14 frozen + logistic probe, both on PAD-UFES-20 | BSD-3 / Apache-2.0 code; CC BY 4.0 data | `MODEL_REGISTRY.md`; **serving reproduces the evaluation exactly** (`docs/eval/serving_consistency.json`) | Research only; lesion photos, not selfies; skin-tone coverage inadequate; both fail release gates |
| Input-quality gate | OpenCV metrics; `skin_closeup` profile calibrated on real PAD-UFES-20 images (fit on train split, reported on test split) | — | `docs/eval/quality_calibration.json`: false-reject of usable photos 49 % → 8.4 %; blur/dark/bright/washed-out detection 98–100 %; motion-blur 71 % (was 93 %); heavy JPEG not detected | Face profile is **not** calibrated; degradations are simulated; no real bad-photo labels |
| Experiment tracking / registry | Built-in registry (SQLite `model_versions`) instead of MLflow | — | model cards record dataset version, code hash, config, seeds, metrics, artifact sha256, licence | MLflow was **not** added: one more service on a 2-core/8 GB machine for no capability the registry lacks. It can be added behind `trainer.train` later |
| Vector search | In-process normalised-vector matmul with content-hash cache | — | adequate for per-patient corpora of 10²–10³ passages | No persistent ANN index; not for millions of passages |
| Containers | **None** (Docker not installed here) | — | — | Deployment instructions in `docs/DEPLOYMENT.md` are untested containers-wise |

## 2b. Research-only model serving (`training/serving.py`, `POST /api/v1/research/predict`)
Admin-only job route (patients and clinicians get 403; `tasks.predict` still refuses). Before loading it re-hashes the model artifact and the backbone weights against
the model card and refuses on mismatch; rejects unusable images (decode, size, exposure, focus) **without predicting**; preprocesses with the training function; applies the saved
temperature; abstains below the confidence at which held-out accuracy first reached 0.80; returns top-3 calibrated probabilities with the model's known limitations (including the
measured failure of confidence as an out-of-distribution signal). `eval/serving_consistency.py` runs the whole test split through this path from raw files: the metrics are identical to the model card.

## 3. Answer provenance (spec §8)
Every assistant source carries `provenance`: `report_fact` (from the user's own document, with page and excerpt), `approved_reference`
(clinician-approved knowledge-base article, with version), `quotation`. LLM-drafted claims (optional mode) are labelled by provenance of the
cited source and are only shown after **deterministic verification**: cited source exists, quoted text occurs in it, every number occurs in it,
no prescriptive/diagnostic wording, and no interpretive word ("normal", "within", "healthy", …) unless the source itself says it (a printed
H/L flag licenses "high"/"low"). A real failure that motivated the last rule is recorded in `docs/eval/llm_*.json`.

## 4. Inference gateway (`/api/v1`)
Job states reported by the worker: `queued → processing → completed | uncertain | rejected | failed | cancelled`. Durable `jobs` table; bounded pool
(`COSMO_JOB_WORKERS`, default 2), queue limit and per-user limit return `429 + Retry-After`; per-task timeouts; cooperative cancellation; idempotency
keys; request IDs (`X-Request-ID`) on jobs and audit events; input bytes deleted at terminal state; restart recovery marks interrupted jobs
failed; SSE and polling; `/health/live`, `/health/ready` (database + private storage are core; OCR, STT, TTS, embeddings, LLM reported as optional
components with honest `unavailable` states and the degraded list), admin `/metrics` (counts, p50/p95 latency, failure rate, queue). 8 tests.

## 5. Training and release lifecycle
1. Licence gate → official terms evidence (hash) → admin verification (SPDX must match the repository's own record) → approval → checksum-verified download → safe unzip → import (exact-duplicate and **label-conflict** handling, near-duplicate flags).
2. Patient-level, class-stratified split; thumbnail-correlation leakage audit; baselines (majority class; frozen features + logistic regression).
3. Fine-tune with explicit local pretrained weights (hash + provenance recorded), class weighting, augmentation, validation-based model selection, temperature scaling fitted on validation.
4. One held-out test evaluation: per-class sensitivity/specificity/precision/AUROC, confusion matrix, bootstrap 95 % CIs, Fitzpatrick subgroups with reliability flag, calibration, robustness, selective prediction, out-of-distribution separation.
5. Evaluation gates (`evaluate_gates`) then approval record (5 release requirements in `status.py`) then deploy, rollback.
A model that fails any gate cannot become `approved_for_defined_use`, and `deploy` refuses anything else (tested).

## 6. What requires network
Training data download, model weight download, and package installation require internet once. **Runtime inference needs no network**: OCR, embeddings, STT,
TTS, LLM (if enabled) and image checks are local; `local_files_only` is set for models. No patient data is sent to any external service.

## 7. Not implemented / not verified (honest list)
* ML models for hair/scalp/body suitability (heuristics only), segmentation, and any patient-facing condition classifier — **none exist or are validated**.
* Hindi/Hinglish STT/OCR/TTS quality; real-patient audio; real clinical documents (no real data was used or available).
* GPU training; multi-node queue (a single-process thread pool is used; use a broker for multiple instances).
* Supabase Auth/Storage/RLS applied to a live project; containerised deployment; TLS termination; MFA.
* Clinician-approved knowledge base content and escalation policy (seeded articles are drafts).

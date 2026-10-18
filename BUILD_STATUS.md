# BUILD_STATUS

Last verified: 2026-10-17 on the development machine (Intel i5-5350U, 2 cores, 7.9 GB RAM, no GPU, Windows 11). Everything below was executed in this
repository; the independent audit with per-item classification is **`PROJECT_GAP_AUDIT.md`** (read that for gaps).

## Verified results (commands in parentheses)

| Area | Result |
|---|---|
| Backend tests (`cd backend && .venv\Scripts\python -m pytest tests`) | **247 tests pass with live Supabase credentials (4 skip without them); line coverage 91 %** (`coverage run --source=app -m pytest`) |
| Frontend (`cd frontend && npx tsc --noEmit && npx eslint . && npm run build`) | clean, 14 routes |
| Patient UI journey (`e2e/ui-journey.mjs`, Playwright, fresh DB) | 97 checks pass: 10 screens at 390 px, 7 desktop pages, fake-camera capture, editor, validation (accepted + rejected), upload, report tabs, audio, grounded answer + refusal, quote + PDF, history, isolation |
| **Clinician↔patient UI journey** (`e2e/clinical-journey.mjs`, fresh DB) | 36 checks pass: patient submits photo + report + flag + quote request; **clinician uses only the browser** to open and see the photo, review it, correct a value (sees the patient's flag), mark reviewed, author/save/approve a care plan, record progress, approve the quote; **admin cannot author or approve**; patient then sees the plan, verified-clinician badge, progress 50 %, approved PDF, corrected value, "reviewed by a clinician"; audit trail checked |
| Document extraction (`python -m eval.doc_extraction_eval`) | synthetic: digital 1.00/1.00 recall/precision; scans/rotations 0.89–0.95 / 0.97–0.98 (`docs/eval/doc_extraction.md`) |
| Retrieval (`python -m eval.retrieval_eval`) | dense BGE-small Hit@1 0.986, AUROC 0.994, 0/120 out-of-scope answered (`docs/eval/retrieval.md`) |
| Speech-to-text (`python -m eval.stt_eval`) | synthetic English WER 4.1 % clean, 7.5 % noisy/phone, 0.67× real time (`docs/eval/stt.md`); Hindi unverified |
| LLM (`python -m eval.llm_eval`) | Qwen2.5-1.5B **inadequate** (39 % claims supported, 20 % correct); stays disabled (`docs/eval/llm_*.json`) |
| Lesion classifiers (research) | PAD-UFES-20 (CC BY 4.0, checksum-verified): ResNet18 fine-tune macro-F1 0.502; DINOv2 probe 0.558 (CI 0.471–0.630). **Both fail release gates** (see `MODEL_REGISTRY.md`) |
| **Input-quality calibration** (`python -m eval.quality_calibration`) | false-reject of usable skin close-ups 49.4 % to 8.4 % on the held-out split; blur/dark/bright/washed-out detection 98-100 %; motion-blur 71 % (was 93 %); heavy JPEG no longer flagged (`docs/eval/quality_calibration.json`) |
| **Reproducibility** (`python -m scripts.train_probe`) | same seed gives a byte-identical probe artifact and identical metrics |
| **ResNet re-run** (`scripts/train_finetune.py`) | metrics identical (macro-F1 0.502) but artifact bytes differ: outcome-reproducible, not byte-reproducible |
| **Supabase (live dev project)** | RLS e2e 63/63, advisors 0 findings, ES256 JWT verification, private Storage round-trip, CDN-cache-after-delete defect fixed |
| **Browser / accessibility / devices** (`docs/TESTING_P11.md`) | axe WCAG A/AA 0 violations on all pages and roles; keyboard + focus trap; reduced motion; reflow and 200% text; Chromium, Firefox, WebKit; 5 emulated devices; admin workflows incl. training Start/Stop: a11y 65/65, browsers 111/111, admin 43/43. **No real devices, no screen readers** |
| **Clinical content governance** | drafts shipped but never used; attested, hash-bound approval; urgent policy = built-in default until a clinician-approved file validates. **Nothing is clinically approved** (`docs/clinical_review/`) |
| **Ops foundation** | encrypted backups + restore drill (`docs/runs/restore_drill_dev_data.json`), TLS via Caddy verified live locally, Prometheus rules tested with promtool, linted Dockerfiles/CI. **No image built, no CI run, no real Redis** (`docs/RUNBOOK.md`) |
| **Serving ≡ evaluation** (`python -m eval.serving_consistency <model_id>`) | The saved DINOv2 model served from raw PNG files reproduces the model card **exactly** (accuracy 0.615, macro-F1 0.558, balanced accuracy 0.576) (`docs/eval/serving_consistency.json`) |

## Implemented this phase
Inference gateway `/api/v1` (jobs, SSE, idempotency, back-pressure, health/ready, metrics, rate limits); licence-gated dataset acquisition with official checksums;
label-conflict-aware import; patient-level stratified training with baselines, calibration, bootstrap CIs, subgroup/robustness/OOD evaluation, release gates;
model registry with provenance; **research-only model serving** (`POST /api/v1/research/predict`, admin-only, checksum-verified, input-quality-gated, calibrated, abstaining);
OCR with orientation/unit repair; dense retrieval with calibrated abstention; verified-claim LLM mode (off); local Whisper STT; thread-safe offline TTS;
clinician correction records (never training-eligible without governance reference); patient data erasure; backup/restore; clinician case view, photo viewer and photo review;
AI Studio UI (training with real progress, model cards, release gates, research inference); model warm-up at startup.

## Defects found and fixed while auditing (each has a regression test unless noted)
Follow-up questions borrowing themselves as context; tests silently using lexical retrieval; HTTP 500 on undecodable audio; per-thread TTS voice discovery and Hindi read by an English voice
(plus a COM cross-thread deadlock found while fixing it); cold-start latency > 30 s; positional `INSERT` breaking after a migration (images); a Home pill that would have shown
"Needs retake" for clinician-reviewed photos; **the input-quality gate rejected 46 % of real lesion photos** (fixed by data-driven calibration, below).

## Known limits (details in the audit)
No model is approved or exposed to patients. Hair/scalp/body and other conditions have no model and no dataset. Everything measured used synthetic or public research data — **no real patient
documents, audio or images were evaluated**. Hindi/Hinglish speech/OCR unverified. Single-process deployment, SQLite, local auth; Supabase schema applied and tested on a dev project (RLS 63/63, Auth and Storage live) but the application data layer is still SQLite; container/CI/TLS files exist but were never built or run on real infrastructure.
Real-device camera/microphone and screen-reader testing not done (Firefox and WebKit engines and device emulation are covered by automated tests).

## Needs resources or approval (not engineering-only)
Clinical approval of knowledge-base content and urgent-symptom policy; consented de-identified real documents; licensed medical references; GPU / ≥16 GB RAM for larger LLMs; datasets
for non-lesion tasks and darker skin tones; Supabase project credentials; legal review (retention, consent, regulatory); production infrastructure.

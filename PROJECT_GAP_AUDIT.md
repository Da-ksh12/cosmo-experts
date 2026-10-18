# PROJECT GAP AUDIT — Cosmo Experts AI

Audit date: 2026-10-17 (machine: Intel i5-5350U, 2 cores, 7.9 GB RAM, no GPU, Windows 11). Audited against (1) the 10-screen premium UI mandate and
(2) the AI/ML production-system mandate. **Both mandate texts were truncated when received** (the AI/ML one ends mid-sentence in §16, "Conv…"), so
anything after those points could not be audited.

Classification: **VC** = verified complete · **IU** = implemented but unverified · **PI** = partially implemented · **MS** = missing · **BL** = blocked
(needs data, hardware, credentials or clinical/legal approval). "Verified" means I executed it in this session and saw the result; evidence
files are named.

## 0. Evidence collected in this audit (all executed today)

| Check | Result |
|---|---|
| Backend `pytest tests` (5 files) | **At first audit: 51 passed.** After the work below: **54 passed, 0 failed** (97 s). Per file: test_api 14, test_gateway 10, test_grounded 11, test_imaging 9, test_screens 10. |
| Backend line coverage (`coverage run --source=app`) | First audit **79 %**; after the work below **83 %** (`probe.py` 0 → 86 %, new `serving.py` 95 %). Still low: `assistant/llm.py` 51 % (real llama.cpp path only exercised by `eval/llm_eval.py`), `datasets/importer.py` 63 %, `datasets/registry.py` 64 %, `routers/staff.py` 69 %. |
| Frontend `tsc --noEmit`, `eslint .`, `next build` | all clean; 14 routes built |
| Browser journeys (Playwright/Chromium, fresh DB each; final code) | `e2e/ui-journey.mjs` **97 passed, 0 failed** and the new `e2e/clinical-journey.mjs` **36 passed, 0 failed** (see section 6). Patient journey covers: all 10 patient screens at 390 px, 7 desktop pages at 1280 px, real camera stream, editor pixel changes, accepted + rejected validation, upload/preview/delete, report tabs, audio, grounded answer + refusal, care-plan visibility, quote total, PDF download, history filters, patient isolation, console-error check |
| Admin AI Studio against the real research DB (`admincheck.mjs`, `researchcheck.mjs`) | Admin 9/9 (after fixing a race in my script); research inference through the UI 8/8 with a real dataset image (abstained at 0.52 < 0.80, calibrated probabilities, warnings; unusable image refused). Screenshots reviewed. |
| Benchmarks (`docs/eval/*`) | OCR/extraction, retrieval, STT, LLM — see §2/§4. All on **synthetic** data. |
| Training runs | 2 real runs on PAD-UFES-20 — see §3. |

**Defects found by this audit and fixed during it** (each has a regression test unless noted):
1. Assistant follow-ups ("and its range?") abstained or matched the wrong line: the engine read the *current* question as the "previous" one. Fixed (`assistant/engine.py`); test `test_followup_…`.
2. The test suite silently ran retrieval in **lexical fallback** because models were absent from its temp data dir, so the shipped dense path (and its calibrated 0.676 threshold) was untested in the app. Fixed (`tests/conftest.py`).
3. Undecodable audio → **HTTP 500** on `/api/speech/transcribe`; now 422 / failed job with a clear message.
4. Windows TTS: voice list differed per thread (COM affinity; admin page showed "offline voice ready" and "0 voices" at once) and a Hindi request was read by an English voice (substring match on "mac**hi**ne"). Fixed by a dedicated TTS thread, tag-based language match, and a hang-free release path; test `test_tts_voice_discovery_is_consistent_across_threads`.
5. Cold start: first assistant call after a restart took > 30 s under CPU load (model load) and failed the UI journey. Added background warm-up (`/api/health` reports `warm_up`; 17–19 s paid at startup, 0.4 s per question afterwards).

---

## 1. The 10 screens

Visual verification = screenshots from the Playwright runs, inspected by me. Mobile = 390×844. Desktop = 1280×900. Reference-image fidelity differences are listed in `BUILD_STATUS.md` (no stock photos / no invented clinical content / no booking or payment).

| # | Screen | Status | Evidence / gaps |
|---|---|---|---|
| 1 | Home dashboard `frontend/app/page.tsx` | **VC** | e2e S1 checks; mobile + desktop screenshots reviewed. |
| 2 | Capture & upload `app/photo/page.tsx`, `components/CameraPanel.tsx` | **VC** (camera: IU on real devices) | Camera verified only with Chromium's **fake device**; iOS Safari/Android untested. Mobile + desktop reviewed. |
| 3 | Crop & adjust `app/photo/adjust`, `components/ImageEditor.tsx` | **VC** on mobile; desktop **not captured** | e2e asserts flip/reset/zoom change real canvas pixels. Pinch-zoom code exists but no test drives two pointers. |
| 4 | Validation `app/photo/check` | **VC** on mobile; desktop **not captured** | Six checks come from the server; accepted + rejected paths tested; UI now shows real `queued/processing` states from the job API. |
| 5 | Reports upload/viewer `app/reports` | **VC** | Real progress (XHR) → job polling; thumbnails, preview, delete confirmed. Multi-file tested with 2 files. |
| 6 | Report explanation `app/reports/[id]` | **VC** | Tabs, printed-range logic, source page/text, EN/Hinglish switch, audio. **Hindi script output not reviewed by a Hindi speaker** (IU). |
| 7 | Assistant `app/ask` | **VC** (voice input IU) | Grounded answer, refusal, history, suggestions verified. Voice-mode UI verified only to the "starts/denied" level; no real microphone speech test. |
| 8 | Doctor review & care plan `app/care` | **VC** *(was PI at first audit)* | Patient view verified; the clinician authoring UI is now driven through the browser by `clinical-journey.mjs` (author, draft, approve, record progress; admin blocked). |
| 9 | Quotation builder `app/quote` | **VC** | Live totals (6×1000 + 18 % = 7 080), server-verified total, approval flow, PDF download. Clinician UI side seeded via API. |
| 10 | History & reports `app/history` | **VC** | Timeline, filters, search, progress ring (25 % from 1/4 stages), privacy toggle, compare. Before/after compare UI **not exercised** in e2e (needs two accepted photos of one region). |

Other UI: login (VC); clinic workspace (**VC** for photo viewing/review, document review, care plan, quote approval; KB approval and price-list editing are still only API-tested); admin AI Studio (**VC** for model cards, gates, research inference; **Start-training and Stop buttons never clicked in a browser: IU**).

## 2. AI services — real inference actually executed?

| Service | Model / version | Inference implementation | Executed successfully? | Status |
|---|---|---|---|---|
| Image suitability | OpenCV 4.11.0.86 + bundled Haar frontal-face cascade + colour/texture heuristics (`suitability-1.0.0`) | `imaging/suitability.py` | Yes (tests, e2e, jobs) | **PI**: deterministic checks work. **Skin close-up quality thresholds are now calibrated on real data** (false-reject 49 % to 8.4 %; `docs/eval/quality_calibration.json`); **face thresholds remain uncalibrated**; accuracy on real bad photos never measured. Hair/scalp/body subject checks are heuristics, frequently "uncertain". No ML localisation/segmentation. |
| Lesion classifier A | ResNet18 (ImageNet weights, timm mirror) fine-tuned, `fc7eef89…` | `training/trainer.py`, **served by `training/serving.py`** | Yes: automated test serves it (checksum-verified, tamper refused, deterministic) | **VC as a research model** (fails release gates; never patient-facing; `tasks.predict` still refuses by design) |
| Lesion classifier B | DINOv2 ViT-S/14 frozen + logistic probe, `124f81d4…` | `training/probe.py` + `serving.py`, route `POST /api/v1/research/predict` (admin only) | **Yes, three ways**: UI run on a real PAD image; test on synthetic data; **`eval.serving_consistency` reproduces the model card exactly from raw files (acc 0.615, macro-F1 0.558, bal-acc 0.576, n=325)** | **VC as a research model** |
| Document OCR | Tesseract 5.4.0 (eng, osd) + PyMuPDF text | `documents/pipeline.py` | Yes | **VC on synthetic**, **IU on real documents** |
| Field extraction | Regex/rule parser (`docpipe-1.0.0`) | same | Yes | as above; layouts beyond two synthetic ones unverified |
| Report explanation | Fixed templates EN/Hindi/Hinglish `documents/explain.py` | template fill | Yes | **VC** (no generation); Hindi/Hinglish wording **unreviewed** |
| Embeddings / retrieval | BAAI/bge-small-en-v1.5 (MIT) | `assistant/retrieval.py` | Yes (benchmark + app) | **VC on synthetic** (Hit@1 0.986, AUROC 0.994, 0/120 out-of-scope answered); threshold calibrated on 10 synthetic docs only |
| Conversational reasoning | **Extractive** answers (default). Optional Qwen2.5-1.5B-Instruct Q4_K_M via llama.cpp + deterministic citation verifier | `assistant/engine.py`, `grounded.py`, `llm.py` | LLM: yes, in `eval/llm_eval.py` (34 questions), not in tests | Extractive **VC**. LLM **BL**: measured inadequate — 39 % of claims supported by cited text, 20 % correct on answerable questions, 2/6 unanswerable answered (verifier then blocks); 0/3 injections leaked; 2.9 tok/s. Larger model needs more RAM/GPU. |
| Clinical knowledge retrieval over licensed references | — | — | — | **MS / BL**: only 2 **draft** seeded articles; no licensed medical reference corpus ingested; needs licensed content + clinician approval |
| Speech-to-text | faster-whisper `base` int8 (MIT) | `speech/adapters.py` | Yes (benchmark, e2e job) | **VC for English synthetic speech** (WER 4.1 % clean, 7.5 % noisy/phone; key-term recall 0.86 clean, 0.77 at 10 dB). Real speakers, accents, **Hindi/Hinglish: unverified** |
| Text-to-speech | OS offline voices via pyttsx3 (3 English voices installed) | same | Yes (after fixes above) | **VC for English**; **Hindi BL** (no voice installed; request correctly refused) |
| Clinician review service | rules + audit | `routers/staff.py`, `care.py` | tests | **PI** (see §6) |
| Safety guardrails | regex refusal (prescribe/diagnose), urgent-symptom message, abstention | `assistant/engine.py` | tested | **PI**: works; escalation wording is a **placeholder awaiting clinician approval**; regex coverage is narrow (English only) |

Not built (MS): task-specific **model router** for image analysis in the gateway (no analysis task is registered because no model is approved), segmentation, hair/scalp/body ML models, out-of-distribution detector for the validator.

## 3. Training pipelines

| Item | Evidence | Status |
|---|---|---|
| Dataset discovery/registry (7 sources) | `datasets/registry.py`; admin UI | **VC** as a registry; licence strings are claims until verified |
| Licence gate (evidence hash, SPDX consistency vs official record, approval, task compatibility) | tests + live run against Mendeley | **VC** for Mendeley-hosted data; other hosts need manual review (no adapter) |
| **PAD-UFES-20** | Official record: CC BY 4.0; 4 files sha256-verified; 2,298 → 2,271 images after cleaning (12 conflicting-label pairs excluded, 3 dups skipped); DATASET_CATALOG.md | **VC** |
| Import, dedup, split | exact + label-conflict handling, near-dup flags; patient-level stratified split; thumbnail-correlation leakage audit = 0 pairs | **VC** |
| Training command | Admin API `POST /api/admin/train` / `python -m scripts.train_via_api` (run A); `python -m scripts.train_probe <version_id>` (run B) — **now committed** | **VC** |
| Run A (ResNet18 fine-tune, 6 epochs, 20 min CPU) | model card in DB; artifact `data/models/lesion_classification_research/fc7eef89….pt`; test macro-F1 0.502 (CI 0.429–0.576) vs frozen-feature baseline 0.515 → **does not beat baseline**; MEL sensitivity 1/7 | **VC** (run + evidence). Fails gates. |
| Run B (DINOv2 probe) | `124f81d4….npz`; test macro-F1 0.558 (0.471–0.630), ECE 0.035; BCC/SCC/MEL sensitivity 0.55/0.41/0.43; OOD AUROC **0.349** (confidence is *worse* than chance at flagging non-lesion photos) | **VC**. Fails 4 gates. |
| Reproducibility | **Probe re-run with the same seed produced a byte-identical artifact (sha256 `f7303a50…`), the same split hash and identical metrics** (models `124f81d4` and `20b66199`). The ResNet fine-tune was **not** re-run | **VC for the probe; IU for fine-tuning** |
| Experiment tracker (MLflow) | not installed; built-in registry used | **PI** by design (documented) |
| Other tasks: acne, pigmentation, redness, dryness, scarring, hair thinning, scalp findings, body | no verified dataset exists | **BL** (data); tasks correctly `unavailable`, predictions impossible |
| Resumable/stoppable jobs | stop flag implemented; **resume from checkpoint not implemented**; stop untested in browser | **PI** |
| GPU / cloud-GPU adapter | `pick_device` for CUDA/MPS exists; never run | **IU**; cloud adapter **MS** |
| Release gates, approval, deploy, rollback | `trainer.evaluate_gates`; tests incl. gate-pass fixture; both real models fail | **VC** (logic). No model has ever been deployed. |

## 4. Documents, OCR, citations, explanation

| Item | Status | Evidence |
|---|---|---|
| Upload validation (type, magic bytes, size, pages, encrypted, corrupt) | **VC** | tests |
| Digital PDF extraction | **VC (synthetic)** | recall/precision 1.00/1.00 on 133 rows |
| Scanned/rotated/blurred/noisy/low-res | **VC (synthetic)** | recall 0.89–0.95, precision 0.97–0.98, 0 crashes (`docs/eval/doc_extraction.md`). The 107.9 s/doc outlier in that table was CPU contention; timing column is unreliable |
| Real-world layouts, handwriting, multi-column, Hindi OCR | **MS / BL** | no data; Hindi traineddata not installed |
| Unit handling | **VC** | exact preservation; OCR confusions snapped only to a lexicon, flagged lower confidence |
| Source citations (document, page, text span, processing version) | **VC** | every field row; UI shows page + exact line |
| Medication/prescription structured extraction | **MS** | prescriptions are classified, not parsed into drug/dose/frequency |
| Document comparison across dates | **VC** | same-unit only; no conversion |
| Plain-language explanation | **VC** (templates) | no clinical interpretation; "outside printed range" only |
| Clinician correction + provenance | **VC** | `field_corrections` versioned; training-eligible only with governance ref (tested) |

## 5. Backend, database, storage, auth, security

| Item | Status | Notes |
|---|---|---|
| FastAPI app: 89 routes (clinic 16, admin 24, v1 gateway 10, documents 9, …) | **VC** | 79 % covered |
| Database | **PI** | SQLite single file, additive migrations in `db.init`. **Supabase schema `supabase/migrations/0001_core.sql` exists but was never applied or tested**; it also lacks tables added later (care_plans, jobs, field_corrections, quote config…) → **drift** |
| Authentication | **PI** | local HMAC tokens, PBKDF2; demo users with one shared dev password; no MFA/lockout beyond rate limit; Supabase Auth not wired |
| Authorisation / tenant isolation | **VC** | tests across documents, photos, thumbnails, jobs, care plans, history, quotes, conversations |
| Private storage | **PI** | private dir + authorised downloads (VC); local disk only, no encryption at rest, no object storage |
| Gateway: jobs, queue, timeouts, cancel, idempotency, SSE, health, metrics, rate limits | **VC** | 10 tests; **single process** (thread pool) — not horizontally scalable |
| Upload hardening | **PI** | magic bytes/size/page caps/safe unzip (VC); **no malware scan or parser sandbox**; PyMuPDF is AGPL (licence review) |
| Audit log | **VC** | covers logins, uploads, reviews, approvals, dataset/model actions, staff access |
| Retention & erasure | **PI** | patient erasure endpoint + conversation purge tested; **purge is manual (no scheduler)**; retention schedule needs legal input |
| Consent capture / privacy notice / DPIA | **MS / BL** | legal |

## 6. Workflows and integration

| Workflow | Status | Notes |
|---|---|---|
| Patient: photo → validate → saved → history | **VC** | UI e2e |
| Patient: report → extract → explain → ask → listen | **VC** | UI e2e (voice input IU) |
| Patient: request quote → clinic approves → PDF | **VC** from patient side | clinician side via API in e2e |
| Doctor: see submissions, **view patient photo** | **VC** *(was MS)* | photo grid + viewer with the automated check; access audited (`image.staff_access`, `case.opened`); review state shown to the patient |
| Doctor: correct extracted values, mark reviewed | **VC** *(was IU)* | UI-driven in `clinical-journey.mjs`; patient sees the corrected value and status |
| Doctor: author + approve care plan, record progress | **VC** *(was IU)* | UI-driven; admin cannot author or approve (UI + API) |
| Doctor: approve quote, KB article | quote **VC** (UI-driven); KB article **IU** | KB approval and price-list editing only API-tested |
| Admin: licence-gated dataset → download → import → train → registry → gates | **VC** via API/scripts; **UI Start/Stop IU** | real run completed |
| Admin: approve/deploy/rollback | **VC** (API) | no real model passes gates, so never exercised on a real model |
| Patient is notified when a plan/quote is approved | **MS** | appears in History/Home on next visit; no notification channel |
| Patient ↔ doctor messaging / appointments | **MS** | not built (and omitted from UI deliberately) |

## 7. Tests actually executed (this session)
See §0. **Failures observed along the way:** 1 failing new test (follow-up), 1 failing test (speech 500), 1 e2e failure (cold start), 1 TTS hang, 1 test race in `admincheck`. All fixed except the last (script-only). **Not run:** load/soak tests, security scan, accessibility audit with a screen reader, Safari/Firefox, real-device camera/mic, real-document OCR.

## 8. Production readiness
| Item | Status |
|---|---|
| Health/readiness/metrics | **VC** (`/api/v1/health/*`, `/api/v1/metrics`) |
| Alerting, log shipping, dashboards | **MS** |
| Containers / CI / CD | **MS** (Docker not installed here; no CI config) |
| Process supervision / multi-instance | **MS** (single uvicorn worker; per-process queue and rate limiter) |
| Backup + restore | **VC** locally (tests incl. tamper detection); encryption of backups, scheduled backups, off-site copy **MS**; DR drill **MS** |
| Model rollback | **VC** logic (API, tested); never needed in practice |
| TLS, secrets management, production auth | **MS** |
| Documentation | AI_ARCHITECTURE, MODEL_REGISTRY (was stale at first audit; **regenerated from the live registry**, 3 models), DATASET_CATALOG, EVALUATION_PLAN, SECURITY_AND_PRIVACY, DEPLOYMENT, BUILD_STATUS (was stale; **rewritten**). `docs/DEPLOYMENT.md` describes container deployment that is untested. |

## 9. Mock data, hardcoded results, placeholders, fake progress, unfinished integrations
No code path fabricates predictions: unimplemented tasks raise; grep for mock/fake/simulate/TODO in app code finds only comments saying so. Things a reader could mistake for real, listed honestly:
* **Demo users + shared password** `cosmo-dev` seeded at startup (`security.py`) — must be removed for production.
* **Draft KB articles** seeded (`main.py`) — unapproved, unused by the assistant until a doctor approves.
* **Dataset registry licence strings** are claims written from memory (`datasets/registry.py`); only PAD-UFES-20 has been verified.
* **`image_suitability` status is declared `available`** in `training/tasks.py` (hard-coded), although accuracy was never measured.
* **Urgent-symptom message** is a placeholder awaiting clinician approval (`assistant/engine.py`).
* **Validator thresholds** (blur 40, brightness 55/215, …) are untuned defaults.
* **Hindi/Hinglish explanation strings** are hand-written templates, not reviewed.
* **Audio waveform bars** in `AudioPlayer.tsx` are a *decorative fixed pattern*; only the fill progress and the time are real.
* **Training progress %** counts optimisation steps only; data preparation and baselines are logged but not in the percentage (stated in the UI).
* **Seeded evaluation data** in `eval/` is synthetic by design; the 5 evaluation-only KB articles in `retrieval_eval.py` are not clinical content.
* **Legacy synchronous routes** (`/api/images`, `/api/documents`, `/api/speech/transcribe`) duplicate the v1 job routes; the UI now uses v1 for photos, reports, voice.
* Repository hygiene: no git history; `docs/*.log` run logs are committed to the tree; run scripts for the lesion probe are not in the repo.

## 10. Prioritised remaining work

| P | Item | Depends on | Acceptance criteria |
|---|---|---|---|
| **P1: DONE** | **Complete the real patient↔clinician workflow in the UI**: clinician can view submitted photos and documents, correct values, write/approve a care plan, record progress, approve a quote — all through the browser, then the patient sees it | none | New Playwright journey passes: patient submits photo+report → doctor (UI only) reviews, edits a value, views the photo, creates + approves plan and quote → patient sees plan, approved quote PDF, history entries; audit log shows staff photo access; admin cannot approve |
| **P2: DONE** | **Real inference for the research model through the gateway (admin/ML-engineer only, never patient-facing)** with serving≡evaluation consistency | none | Endpoint loads a registered model, returns calibrated probabilities for an uploaded image; test proves identical probabilities to offline evaluation for the same test image; patients get 403; response labelled research-only with OOD/skin-tone warnings |
| P3: DONE (the OOD image-set builder is not committed; it used a scratch environment) | Commit training scripts, regenerate `MODEL_REGISTRY.md`, refresh `BUILD_STATUS.md`, demonstrate reproducibility | P2 | Done for the probe (identical artifact) |
| **P4: DONE** | Test the untested code: probe, real llama.cpp backend, dataset download/resume/cancel, serving edges | — | Measured: `llm.py` 93 %, `importer.py` 98 %, `registry.py` 92 %, `probe.py` 92 %, `serving.py` 100 %, `staff.py` 79 %; total 89 % |
| **P5: PARTIAL** | Supabase migration reconciled and applied to the real dev project; Auth (JWKS) and private Storage wired and tested live. **Not done:** the application's data still lives in SQLite (no Postgres data layer), and the browser login still uses the local-auth form | frontend + data-layer work | RLS: 63/63 live checks pass; security advisor 0 findings. Remaining: Postgres repository layer, Supabase sign-in UI |
| **P6: PREPARED, AWAITING CLINICIAN** | Review packet, attested approval workflow and an approved-policy loader are built and tested. **No content is approved; none is enabled.** Hindi/Hinglish wording is unverified candidates only | **clinical approval** | Signed-off articles and policy (`docs/clinical_review/`); then escalation tests updated |
| P7 | Real, consented, de-identified documents to evaluate extraction; Hindi OCR | **data + governance** | Report on ≥ 100 real documents; recalibrate |
| P8 | Larger LLM evaluation (≥ 7B) on adequate hardware | **GPU / ≥ 16 GB RAM** | Meets the criteria in EVALUATION_PLAN §4 |
| P9 | Datasets for acne/pigmentation/hair/scalp; skin-tone-diverse lesion data | **data acquisition + licences** | Verified licence, ≥ 50 test images in Fitzpatrick 4–6 |
| **P10: FOUNDATION BUILT, PARTLY VERIFIED** | Container files, compose, CI, Caddy TLS, Redis-capable rate limiter, Prometheus metrics/alerts, encrypted pruned backups, health-check alerting, startup safety checks, secrets handling, runbook, restore drill. **Not verified:** any container build/run, a GitHub Actions run, a real Redis, public certificate issuance, webhook delivery, penetration test. Shared job queue deliberately not built (single instance) | infra for the unverified items | Restore drill done on dev data; image build and CI run still required |
| **P11: AUTOMATED PART DONE; DEVICE AND SCREEN-READER PART BLOCKED** | Done: axe WCAG A/AA on all pages × roles (0 violations), keyboard and focus management, reduced motion, reflow/200% text, Chromium + Firefox + WebKit journeys, 5 emulated devices, fake camera, admin/clinician workflows in the browser incl. training Start/Stop, desktop screenshots. **Blocked (needs people and hardware):** real iOS/Android devices, real camera/microphone, screen-reader sessions (NVDA/JAWS/VoiceOver/TalkBack), Hindi UI | devices + assistive-technology testers | Automated suites green (`docs/TESTING_P11.md`); checklist for real devices still unsigned |
| — | Clinical validation, regulatory review, legal retention schedule, consent flows | **clinical / legal** | outside engineering |

## Update log
*(each entry names what was run)*

**2026-10-17: priorities P1 to P3 and the defects found on the way.**
* **P1 (clinical workflow in the UI):** added `GET /api/clinic/cases/{id}`, `POST /api/clinic/images/{id}/review` and a richer `GET /api/clinic/submissions`; the clinic page now has a photo gallery/viewer, a case overview and photo review; history and home show "reviewed by a clinician". New test `test_clinician_can_see_photos_cases_and_review_them`; new `e2e/clinical-journey.mjs`: **36/36 pass** on a fresh database (run twice, the last time on the final code).
* **P2 (real inference):** `training/serving.py`, `POST /api/v1/research/predict` (admin only), admin "Try a model" panel; tests for the CNN and probe paths including tamper refusal, patient 403 and determinism; `eval/serving_consistency.py` is **identical to the model card**.
* **P3:** `scripts/train_probe.py`, `scripts/train_via_api.py`, `scripts/export_registry.py` (regenerated `MODEL_REGISTRY.md`, 3 models), probe reproduction run (byte-identical artifact), `BUILD_STATUS.md` rewritten, run logs moved to `docs/runs/`.
* **New defects found and fixed:** the input-quality gate falsely rejected 45 % of real lesion photos (calibrated with `eval/quality_calibration.py`, tests added, reason order now exposure before focus); binary-class probe/serving crash (found by the new probe test); `images` positional insert broken by a migration; Home pill that would have said "Needs retake" for reviewed photos.
* **Re-run on the final code:** backend 54 passed (83 % coverage); frontend tsc/eslint/build clean; `ui-journey` 97/97; `clinical-journey` 36/36; admin and research UI checks pass.

**Still open (unchanged by this work):** everything under P4 to P11 above, plus hair/scalp/body/other models (no data), a larger LLM (no hardware), real-document and Hindi evaluation (no data), Supabase (no credentials), clinical content approval, production infrastructure. The ResNet fine-tune has not been re-run for reproducibility; KB approval, price-list editing and the AI Studio Start/Stop buttons have not been driven in a browser.

**2026-10-17 (later): P4 and P5.**
* **P4:** new tests `test_datasets_ops.py` (21: download resume/416/size cap/redirect downgrade/checksum/cancel/zip-slip/label conflicts), `test_llm.py` (15: real llama.cpp path against the actual GGUF, OpenAI-compatible backend, URL allow-list incl. the `10.0.0.5.evil.com` bypass), `test_serving_edges.py` (20). Real defects found and fixed: HTTP 416 on a complete partial file, size cap not enforced without Content-Length, https-to-http redirect downgrade, LLM URL prefix bypass. Full suite without credentials: **152 passed, 4 skipped**; coverage 89 % (`coverage run --source=app -m pytest`).
* **ResNet re-run** (`scripts/train_finetune.py`, log `docs/runs/train_finetune_repro.log`): split identical, **metrics identical** (macro-F1 0.502, accuracy 0.637, balanced accuracy 0.505) but the **artifact hash differs** (`24ed3c80…` vs `a2c5e42d…`). Fine-tuning is reproducible in outcome, not byte-for-byte (unlike the probe). `MODEL_REGISTRY.md` regenerated (4 models).
* **P5 (real project `Cosmo Expert`, Postgres 17):** baseline run showed 5 real attack paths in the original `0001_core.sql`; `0002_reconcile_and_harden.sql` + `0003_private_helpers.sql` closed them (`docs/eval/supabase_rls_baseline.log` vs `supabase_rls_hardened.log`: 63 passed; advisor 0 findings). Backend verifies real ES256 tokens via JWKS (`supabase_auth.py`, 35 tests, 33 pass offline + 2 live), role comes from `profiles` not the token, a deleted user is locked out. Private Storage backend (`storage.py`, 11 tests, all pass live): per-user folders, mime allow-list refuses HTML, erasure removes objects.
* **Defect found by the live test:** Supabase's CDN edge caches `object/authenticated/*` reads (`cf-cache-status: HIT`, even with `Cache-Control: no-store`); a file read before deletion stayed downloadable at the identical URL after the object was erased. Reads now add a unique query string so they reach the origin; a regression assertion primes the cache first. Residual risk: an already-cached copy can be served for the same URL and credential until the edge evicts it; this is documented in the data-erasure notes and cannot be controlled from this repository.
* **Open for P5:** Postgres data layer for application tables; Supabase sign-in in the Next.js UI; recommended hardening left to the owner (minimum password length >= 12, disable open sign-up, leaked-password protection): not changed without permission. **The access token used for testing appeared in the chat transcript and must be revoked.**

**2026-10-17 (later still): P6 and P10.**
* **P6:** `backend/content/kb_drafts.json` (11 plain-language, non-diagnostic articles, each with the claims a reviewer must verify) and `urgent_policy.draft.json` (10 categories with examples, false-positive notes, open questions, unverified Hinglish candidates) are written as DRAFTS. The application never reads the policy draft; KB drafts are seeded as `draft`. Approval now needs a doctor, a typed source note and an attestation; it is bound to the SHA-256 of the exact text (edits, direct DB changes and legacy approvals disable the article); production requires a verified clinician; admins cannot approve. A clinician-approved urgent policy loads only from `urgent_policy.approved.json` with a matching hash and valid patterns; any defect falls back to the built-in default and is visible at `/api/clinic/content-status`. UI: Review dialog with the claims list. Packet for the reviewer: `docs/clinical_review/REVIEW_PACKET.md` + CSVs + sign-off template + approval procedure. Tests: `test_clinical_content.py` (21; the draft-consistency test found a real gap in my own draft: "pressure in my chest" was not matched). **Still needs:** a qualified clinician's decisions, the real emergency number/clinic contact, Hindi/Devanagari wording by a native-speaker clinician, and a Supabase migration for the new `kb_articles` columns (not yet written: the Postgres schema still has the old KB shape).
* **P10 verified here:** `test_ops.py` (25: production refusal rules, file secrets, memory + Redis-protocol limiter incl. two instances sharing a counter and outage fallback via fakeredis, Prometheus endpoint auth and bounded labels), `test_backup.py` (21: chunk-boundary round trips, bit-flip/truncation/reorder/wrong-key detection, retention, failure status), `test_healthcheck.py` (12, real HTTP server), `test_ops_config.py` (8), `test_scan_secrets.py` (4; it found that my first placeholder filter could hide real tokens, fixed). Tools run on verified downloads (checksums matched): `caddy validate` + live TLS 1.3 run with HSTS, redirect and `/metrics` hidden (`docs/runs/tls_local_check.json`); `promtool check rules` and `promtool test rules` (13 rules, 6 scenario tests); `amtool check-config`; `hadolint` clean; `actionlint` clean. Restore drill: `docs/runs/restore_drill_dev_data.json`.
* **Suite after P6/P10:** 243 tests (242 passed on the full run + 1 order-dependent assertion since fixed, re-run green), 4 skipped without Supabase credentials; coverage 90 %.

**2026-10-18: P11 (browser, device, accessibility, admin workflows).** Suites: `e2e/p11-a11y.mjs` 65/65, `e2e/p11-browsers.mjs` 111/111, `e2e/p11-admin.mjs` 43/43 (evidence in `docs/runs/p11_*.json`, method and limits in `docs/TESTING_P11.md`). Legacy journeys re-run on fresh databases after the UI changes: `clinical-journey` all 36 checks pass, `ui-journey` all checks pass.
* **Real defects found and fixed:** modal dialogs had no focus trap; colour-contrast failure (photo tips); keyboard-unreachable scroll regions; overflow at 320 px (Home, Photo) and at 200% text (header, bottom navigation, action cards, headings, decorative art); **patients could load the clinic/admin page shells** (data was always refused by the API, the pages now never render for patients).
* **Test-side flakes, recorded honestly:** one Firefox sign-in timeout under CPU load (6/6 repeat passes; later full run 111/111); the legacy `ui-journey` audio step used a fixed 3.5 s wait that failed when the first speech synthesis after a cold start took longer (now waits for the outcome; the app itself was verified to play audio). WebKit logs navigation-cancelled fetches as errors; 15 such messages are excluded, with a separate idle-page check that proves none occur at rest.
* **Final regression:** backend 247 passed, 0 skipped with live Supabase credentials, coverage 91 %; frontend `tsc` and `eslint` clean; repository secret scan clean.
* **Not covered:** real devices, screen readers, Hindi UI/speech, forced-colours, 400% desktop zoom, voice control, performance on real phones.

**2026-10-18 (final-brief execution): baseline commit, new modules, final regression.** `git init` + baseline (`28b1def`); all work since is committed. Added and verified: hostile-upload defences and prompt-injection tests (22 tests); in-app notifications (6 tests, browser 9/9); administrator user management (11 tests, browser 15/15); training checkpoint/resume (8 tests); input-quality gate v1.1 with an independent validation (motion-blur detection 93-100 %, 9.5 % false rejection, compression warning); consent mechanism with hash-bound approved notice (8 tests, browser 18/18; **legal text blocked**); Results-over-time screen; quotation validity and KB review-due lapse (8 tests); production refusal of silent lexical retrieval and of an unapproved privacy notice; Supabase migrations 0004-0005 applied to the dev project, live RLS **76/76**, security advisor 0 findings; clean load and soak run (0 errors, no memory growth; single-instance only). `docs/STATUS_REPORT.md` classifies every brief section.
* **Final regression:** backend **323 passed, 0 skipped** with live Supabase credentials, coverage 92 %; frontend `tsc` and `eslint` clean; secret scan clean; browser: legacy `ui-journey` and `clinical-journey` pass on fresh databases, a11y 65/65, browsers/devices 111/111, admin workflows 36/36 (the training Start/Stop step is reported as SKIPPED on a database without the imported PAD-UFES-20 dataset; it passed 43/43 earlier on a copy of the development database and the training panel changed only by one added checkbox), users 15/15, notifications 9/9, consent and results-over-time 18/18.
* **Defects found by this phase's tests and fixed:** dialogs opened from inside the blurred header were positioned relative to the header (now portalled); an unfocused contrast failure on unread notifications; test suites leaving hundreds of MB of temp data on a nearly full C: drive (the test process now removes its data directory on exit; leftover temp folders and obsolete scratch virtualenvs were deleted); several new tests initially polluted the shared test database (cleanup added); two browser tests had races or implicit preconditions (now explicit, with SKIPPED for unmet preconditions).
* **Still open (see `docs/STATUS_REPORT.md` sections 2-3):** Postgres data layer, Supabase sign-in UI, self-registration, non-lesion image tasks, Hindi support, medication parsing, agent tool routing, GPU verification, malware scanning engine, container build / CI run / real Redis / public certificate, real devices and screen readers, and everything that needs clinicians, lawyers, real data or hardware.

**2026-10-18 (Phase 2): production data architecture, security and privacy.** Closed by this phase: application data layer for PostgreSQL (complete and tested; cut-over not performed), Supabase sign-in and account lifecycle (backend tested against a protocol fake, UI built but not browser-tested), forced row-level security and API-level IDOR tests, versioned consent history with withdrawal, private storage hardening with a fail-closed scanner interface, retention framework with legal holds, encrypted PostgreSQL backups with verified restore, API operational security. Still open: applying the migrations to the real database (owner connection string), a running malware scanner, the approved privacy notice and retention schedule (legal), MFA, browser tests of the new screens, container/CI/TLS on real infrastructure. Verification: SQLite 622 passed / 4 skipped; PostgreSQL-mode details in `docs/STATUS_REPORT.md`. Two Supabase management tokens were pasted into the chat during the work and must be revoked.

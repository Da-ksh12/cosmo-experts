# Cosmo Experts AI: status report against the final master brief

Evidence date: 2026-10-18. Every line below is backed by something that was run in this repository; where it was not run, the line says so.
Classifications: **VERIFIED COMPLETE** (implemented and its automated checks were executed here), **IMPLEMENTED BUT UNVERIFIED**, **PARTIALLY IMPLEMENTED**, **MISSING**, **BLOCKED** (needs something only the owner can provide).
"Verified" never means clinically validated, legally approved, or tested on real devices.

## 1. Completed modules (VERIFIED COMPLETE, automated evidence)

| Module | Evidence |
|---|---|
| Ten patient screens, responsive layouts, premium visual system | `e2e/ui-journey.mjs` all checks pass; axe WCAG 2.0-2.2 A/AA: 0 violations on every page and role (`e2e/p11-a11y.mjs`, 65/65); Chromium/Firefox/WebKit and 5 emulated devices (`p11-browsers.mjs`, 111/111) |
| Patient to clinician to patient workflow (photos, reports, review, care plans, quotations) | `e2e/clinical-journey.mjs` 36/36 |
| Clinician and administrator workflows in the browser (KB review with attestation, price list, quotation settings and approval, verification, role isolation, training Start/Stop) | `e2e/p11-admin.mjs` 43/43 |
| Administrator user management (create, role, disable, password reset, lock-out guards, salted hashes for new accounts) | `tests/test_user_admin.py` 11; `e2e/p11-users.mjs` 15/15 |
| In-app notifications (generic, idempotent, private) | `tests/test_notifications.py` 6; `e2e/p11-notifications.mjs` 8/8 |
| Consent: hash-bound approved privacy notice, per-version acceptance, enforcement, gate UI | `tests/test_consent.py` 8; `e2e/p11-consent.mjs` 19/19 (the legal TEXT is blocked, see section 3) |
| Results over time (printed values across reports, no conversion, no interpretation) | backend tests in `test_api.py`; browser check in `p11-consent.mjs` |
| Quotation validity and expiry; KB review-due lapse | `tests/test_quote_expiry.py` 7; `tests/test_clinical_content.py` 22 |
| Clinical content governance (drafts never used, attested hash-bound approval, approved-policy loader) | `tests/test_clinical_content.py`; `docs/clinical_review/` |
| Hostile-upload defences and prompt-injection regression tests | `tests/test_upload_safety.py` 22 |
| Input-quality gate v1.1 (directional blur rule, compression warning) | `docs/eval/quality_features.json`, `docs/eval/quality_gate_eval.json` (independent validation split) |
| Training pipelines, checkpoint/resume with integrity checks | `tests/test_training_resume.py` 8, `tests/test_serving_edges.py`; reproducibility measured (section 5) |
| Dataset licence gate, import/resume/cancel/zip-slip/label conflicts | `tests/test_datasets_ops.py` 21 |
| Research-model serving (admin only, checksum-verified, abstaining) | `tests/test_serving_edges.py`, `eval/serving_consistency.py` identical to the model card |
| Supabase on the real dev project: migrations 0001-0005, RLS, private Storage, ES256 JWT verification | `docs/eval/supabase_rls_0005.log` 76/76; security advisor 0 findings; `tests/test_supabase_auth.py`, `tests/test_storage.py` |
| Encrypted pruned backups, restore drill, health-check alerting | `tests/test_backup.py` 21, `tests/test_healthcheck.py` 12, `docs/runs/restore_drill_dev_data.json` |
| TLS (Caddy, internal CA), Prometheus alert rules (syntax and logic), Alertmanager config | `docs/runs/tls_local_check.json`; `promtool check/test rules`; `amtool check-config` |
| Production start-up refusals (secrets, demo accounts, CORS, rate limits, lexical-retrieval fallback, unapproved notice) | `tests/test_ops.py` 27 |
| Whole backend suite | see section 6 |

## 2. Incomplete modules

| Item | Class | What exists | What is missing |
|---|---|---|---|
| Application data in Postgres (the app still stores clinical data in SQLite) | MISSING / BLOCKED | Schema reconciled and live-tested in Supabase (0001-0005) | A Postgres repository layer; needs the project database connection string (not available over the Management API) and a decision on cut-over |
| Supabase sign-in in the Next.js UI | MISSING | Backend verifies real Supabase tokens and reads roles from `profiles` | Login form against Supabase Auth, token refresh, browser test |
| Patient self-registration and account self-service | MISSING | Administrators create accounts | Sign-up, e-mail verification, password reset flows (e-mail provider not configured) |
| Segmentation, acne, pigmentation, redness, hair/scalp, other body-image models | MISSING / BLOCKED | Tasks are NOT registered as available | Licensed, task-matched datasets and skin-tone-diverse test data |
| Hindi/Hinglish OCR, speech and explanations | BLOCKED | English OCR/STT/TTS | Hindi OCR resources, verified speech data, bilingual clinician review |
| Medication and prescription parsing | MISSING | Prescription is an accepted document type; no medication fields are extracted | Evidence and de-identified real prescriptions to build and evaluate it |
| Agent tool routing and tool permissions | MISSING | Extractive grounded assistant, optional verified-claim LLM mode (off), model abstraction | A tool layer; deliberately not added without a model adequate for it |
| Larger language model | BLOCKED | Qwen2.5-1.5B measured inadequate and kept off | Hardware with 16 GB+ RAM / GPU and re-evaluation |
| GPU / MPS / CUDA training | IMPLEMENTED BUT UNVERIFIED | Device selection code | A GPU machine |
| Automatic model rollback on failure criteria | MISSING | Manual deploy and rollback API | Defined failure criteria; no model is deployed to patients |
| Malware scanning engine | MISSING | Parser hardening only (`documents/safety.py`) | An antivirus engine (e.g. ClamAV) in front of storage |
| Containers and CI | IMPLEMENTED BUT UNVERIFIED | Dockerfiles pass hadolint; workflow passes actionlint; compose structure tested | An image build, `docker compose up`, a real GitHub Actions run (no container runtime here) |
| Real Redis, public certificate, webhook delivery | IMPLEMENTED BUT UNVERIFIED | fakeredis tests; local TLS; local webhook receiver | A Redis server, a real domain, a chat webhook |
| Horizontal scaling | MISSING by design | Single instance; shared rate-limit store ready | Shared queue and shared database |

## 3. Blocked on clinical, legal, data, hardware or credentials

| Dependency | Blocks | Work already done without it | Verification needed when available |
|---|---|---|---|
| **Clinician review** | Any approved knowledge-base article; the urgent-symptom policy and message; Hindi/Hinglish wording; thresholds for model gates; any patient-facing model | 11 draft articles, 10-category policy draft, review packet, attested approval workflow and loader | Signed-off packet; escalation tests updated; install via `make_approved_policy` |
| **Legal counsel** | The privacy notice text, retention schedule, regulatory classification, jurisdictional obligations | Consent mechanism, hash-bound approved-notice loader, draft text marked DRAFT | Approved notice installed via `make_approved_notice`; production start-up passes |
| **Real, consented, de-identified documents and audio** | Evaluating extraction, OCR, STT on real inputs | Synthetic evaluations only (`docs/eval/doc_extraction.md`, `stt.md`) | Report on at least 100 documents |
| **Datasets and licences** | Additional lesion/skin/hair datasets, dark-skin test data | Catalog, licence gate, importer, PAD-UFES-20 imported (CC BY 4.0, checksum-verified) | Per-dataset terms capture and approval, then import and evaluation |
| **GPU / more RAM** | Larger models, faster training, a 7B+ language model | CPU pipelines, 2-core measurements | Re-run training and `eval/llm_eval.py` |
| **Supabase database connection string** | The Postgres data layer | Schema, RLS and Storage live-tested | Repository layer tests against the project |
| **Devices and assistive technology** | Real-device camera/microphone, screen-reader results | Emulation, axe, keyboard tests | Signed checklist (section 8) |
| **Container runtime, CI host, domain, Redis** | Section 2 infrastructure items | Reviewed files and local equivalents | Build, run, issue a certificate, run CI |

## 4. Models, datasets, training and evaluation (real numbers)

Models used by the product are listed with licences and hashes in `MODEL_REGISTRY.md`: Tesseract 5.4.0, BGE-small-en-v1.5 (MIT), faster-whisper base (MIT), Qwen2.5-1.5B-Instruct Q4_K_M (Apache-2.0, off), ResNet18 ImageNet weights, DINOv2 ViT-S/14 (Apache-2.0).
Trained here, all **research-only and failing their release gates**: ResNet18 fine-tune `fc7eef89…` (macro-F1 0.502, CI 0.429-0.576, accuracy 0.637) and its reproduction run `f9396683…` (identical metrics), DINOv2 linear probe `124f81d4…` (macro-F1 0.558, CI 0.471-0.630), plus one earlier CNN. Gate failures: no improvement over the frozen-features baseline, melanoma/SCC sensitivity far below 0.80, 8 test images in Fitzpatrick 4-6 (50 required), near-duplicates, no OOD evidence for the ResNet.
Dataset: PAD-UFES-20, CC BY 4.0, official checksum verified, patient-level splits (train 1621 / val 325 / test 325 images). ISIC, Fitzpatrick17k, DDI, HAM10000, ACNE04, FUNSD are in the catalog as **unreviewed and blocked**.
Reproducibility: DINOv2 probe byte-identical; ResNet fine-tune identical metrics and split, **different artifact bytes** (`24ed3c80…` vs `a2c5e42d…`); resume-vs-uninterrupted run agrees within 1e-4 on identical seeds with one CPU thread.
Input-quality gate v1.1 on 231 untouched validation photos: motion-blur detection 93-100% (v1.0: 53-84%), false rejection of usable close-ups 9.5%; heavy JPEG is a warning (66-69% of re-encoded images flagged, 3% of usable photos flagged). Simulated degradations, lesion close-ups only.

## 5. Security, database, deployment status

* **Supabase (dev project "Cosmo Expert", Postgres 17):** migrations 0001-0005 applied, 76/76 RLS checks (baseline run showed 5 real attack paths in the original schema; all closed), security advisor 0 findings, ES256/JWKS verification, role from `profiles` not from the token, private Storage with mime and size limits. A CDN-cache defect (deleted files still downloadable at the same URL) was found by the live test and fixed in the server read path. Recommended but **not changed without your permission**: minimum password length 12+, disable open sign-up, leaked-password protection. **The management token used in testing was pasted into chat and must be revoked.**
* **Backups:** AES-256-GCM, tamper/truncation/wrong-key detection, retention pruning, executed restore drill on development data (tiny dataset: timings say nothing about production).
* **Load and soak** (this 2-core, 7.9 GB, no-GPU machine, one backend process, persistent connections): see `docs/runs/loadtest.json` and section 7.
* Nothing in this repository is claimed production-ready: see "Production readiness" in `PROJECT_GAP_AUDIT.md`.

## 6. Test results

See `BUILD_STATUS.md` for the current counts and commands; the final regression for this session is recorded at the end of `PROJECT_GAP_AUDIT.md`.

## 7. Load and soak evidence

Run on 2026-10-18 on the development machine (2 CPU cores, 7.9 GB RAM, no GPU), one uvicorn worker, rate limiting off, a fresh database, persistent connections, nothing else running (`ops/loadtest.py`, raw numbers in `docs/runs/loadtest.json`). Each level ran 12 s; error rate was **0 in every scenario and level**.

| Scenario | 1 worker | 4 workers | 8 workers |
|---|---|---|---|
| `GET /api/v1/health/live` | 382 rps, p95 3.7 ms | 365 rps, p95 18.7 ms | 385 rps, p95 27.6 ms |
| `GET /api/history` (database read) | 72 rps, p95 19.5 ms | 94 rps, p95 52 ms | 100 rps, p95 100 ms |
| Assistant: urgent-symptom redirect | 29 rps, p95 40 ms | 28 rps, p95 173 ms | 27 rps, p95 432 ms |
| Assistant: retrieval path (no documents, so it abstains) | 25 rps, p95 48 ms | 25 rps, p95 207 ms | 24 rps, p95 391 ms |

**Soak:** 240 s, 4 workers, mixed scenarios, 8265 requests, 0 errors; backend memory 648 MB throughout (no growth; 641 MB at the end).

What this does and does not show:
* Throughput plateaus after 1-2 workers and latency rises in proportion to concurrency: a single process serialises the work, as expected. **This is a single-instance deployment; it is not evidence of horizontal scalability** and none is claimed.
* The retrieval scenario used a database with no documents for that patient, so it measured the abstention path, not dense retrieval over real reports (an earlier, contaminated run measured about 110 ms per question with documents present). Document upload/OCR, speech-to-text, research inference and training were **not** load-tested.
* 240 s is a short soak. A multi-hour soak and a test through the Caddy proxy were not done. Numbers describe this machine only.

## 8. Commands

```
# backend
cd backend && py -3.11 -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m scripts.fetch_models
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
# frontend
cd frontend && npm ci && npm run build && npm run start
# tests
cd backend && .venv\Scripts\python -m pytest tests            # add SUPABASE_ACCESS_TOKEN and SUPABASE_PROJECT_REF for the live Supabase tests
cd frontend && npx tsc --noEmit && npx eslint . && npm run build
cd e2e && npm ci && npx playwright install chromium firefox webkit && node p11-a11y.mjs && node p11-browsers.mjs && node p11-admin.mjs && node p11-users.mjs && node p11-notifications.mjs
# consent test needs COSMO_REQUIRE_CONSENT=1 on a test database: node p11-consent.mjs
```

## 9. APK-readiness checklist (nothing below is claimed done unless ticked with evidence)

- [x] Backend and web front end pass their automated suites (section 6)
- [x] Patient, clinician and administrator permissions enforced on the server and tested
- [x] Research models cannot reach patients (serving is admin-only, gated, checksummed)
- [ ] **Packaging decision:** the front end is a Next.js web app. An APK needs a wrapper (for example a Trusted Web Activity or Capacitor); none is built or tested
- [ ] Real-device camera and microphone tests on target Android versions
- [ ] Screen-reader pass (TalkBack) on the packaged app
- [ ] Supabase sign-in in the UI, or an agreed alternative, with token storage reviewed for a mobile shell
- [ ] Clinician-approved urgent-symptom message and knowledge base installed
- [ ] Legal-approved privacy notice installed; store listing privacy declarations prepared by the owner
- [ ] Production deployment built and run (containers, TLS, backups, alerts) with a restore drill on production-like data
- [ ] Penetration test and dependency/licence review (PyMuPDF is AGPL/commercial: decide before distribution)

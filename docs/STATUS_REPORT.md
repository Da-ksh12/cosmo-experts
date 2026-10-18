# Cosmo Experts AI: status report against the final master brief

Evidence date: 2026-10-18. Every line below is backed by something that was run in this repository; where it was not run, the line says so.
Classifications: **VERIFIED COMPLETE** (implemented and its automated checks were executed here), **IMPLEMENTED BUT UNVERIFIED**, **PARTIALLY IMPLEMENTED**, **MISSING**, **BLOCKED** (needs something only the owner can provide).
"Verified" never means clinically validated, legally approved, or tested on real devices.

## 0. Phase 2: production data architecture, security and privacy (2026-10-18)

Detailed documents: [PHASE_2_IMPLEMENTATION.md](PHASE_2_IMPLEMENTATION.md), [DATABASE_MIGRATION.md](DATABASE_MIGRATION.md), [SUPABASE_AUTH_AND_RLS.md](SUPABASE_AUTH_AND_RLS.md), [PRIVACY_AND_CONSENT.md](PRIVACY_AND_CONSENT.md), [DATA_RETENTION_AND_DELETION.md](DATA_RETENTION_AND_DELETION.md), [PRIVATE_FILE_STORAGE.md](PRIVATE_FILE_STORAGE.md), [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md), [PRODUCTION_CONFIGURATION.md](PRODUCTION_CONFIGURATION.md).
**Nothing here is production-ready, legally approved or clinically approved.**

Final integration verification (one run, not repeated per batch): SQLite mode, full backend suite (`cd backend && .venv\Scripts\python -m pytest tests -q`): **622 passed, 4 skipped, 0 failed** in 16 min 34 s (the 4 skips are not counted as passed). PostgreSQL mode (`set COSMO_TEST_DB=postgres`, 21 data-touching test files against a disposable PostgreSQL 17, one combined run): **449 passed, 2 skipped, 2 failed**; both failures were test-order pollution caused by the file order chosen for that run (an approved quotation left behind by `test_authz_idor.py`, now cleaned up by its fixture; approved datasets left behind by `test_datasets_ops.py` when it runs before `test_api.py`, not changed). After the fix `test_authz_idor.py` and `test_api.py` pass together on PostgreSQL (67 passed). The default alphabetical order runs `test_api.py` first and is what the SQLite run used. Frontend `tsc`, `eslint` and `next build` clean; repository secret scan clean.

### Implemented and verified (automated evidence in this repository)

| Capability | Evidence |
|---|---|
| Validated, redacted, fail-closed configuration; structured logs with correlation ids | `test_settings_redact.py`, `test_ops.py` |
| PostgreSQL data layer (pooled, `timestamptz`, dialect translation, transactions, no silent SQLite fall-back in production) | `test_pg_backend.py` against a disposable PostgreSQL 17 |
| Ordered, checksummed, transactional migrations 0001-0005 | `test_pg_backend.py`; **dry run on the real Supabase project (PostgreSQL 17.11) succeeded and was rolled back; nothing persisted** |
| SQLite to PostgreSQL copy tool (read-only, resumable, idempotent, validated, counts-only output) | `test_sqlite_to_pg.py` 9 |
| Supabase account lifecycle: sign-in, rotating httpOnly refresh cookie, sign-out, registration (closed by default, no client roles), reset with global sign-out, lockout, disabled accounts, administrator actions | `test_auth_lifecycle.py` 33 against a protocol-level fake with real ES256 keys (no e-mail sent) |
| Server-side authorization and IDOR prevention on every id-taking endpoint; administrators kept out of clinical records by default | `test_authz_idor.py` 53 (SQLite and PostgreSQL; mutation-checked) |
| Row-level security with signed per-transaction identity, forged-identity tests, append-only audit | `test_pg_rls.py` 41 |
| Consent: append-only history bound to notice hash and approval status, withdrawal, visible state, never a consent to train | `test_consent.py` 13 |
| Private storage: opaque keys, atomic 0600 writes, quotas, scanner interface that never reports clean without a scanner and fails closed, short-lived signed links | `test_private_storage.py` 19 (scanner protocol against a local fake) |
| Retention and deletion framework: operator-chosen periods, dry run by default, legal holds, bounded batches, file/row coordination, audit purge function | `test_retention.py` 23 (SQLite and PostgreSQL) |
| Encrypted PostgreSQL backup and verified restore into an empty database | `test_pg_backup.py` 14 against a real PostgreSQL |
| Request-size, concurrency and timeout limits; explicit CORS; API security headers; safe errors; no echo of invalid input; SSRF guard; no dangerous calls in the code | `test_ops_security.py` 38 |

### Awaiting infrastructure

* A running **malware scanner** (ClamAV `clamd`); the real engine was never exercised.
* A container runtime to build and run `docker-compose.yml` (updated for this phase, never run); a public domain and certificate; Redis for shared rate limits.
* **Off-machine storage** for encrypted backups; network egress restrictions for the backend; an egress proxy if DNS-rebinding resistance is required.

### Blocked by credentials

* **Applying the migrations to the real database** and giving `cosmo_app` a login, installing the identity secret: needs the owner connection string (the management token cannot provide it).
* Copying existing development data to PostgreSQL; a **backup and restore drill on the real database**; a live PostgreSQL-mode end-to-end run.
* Live sign-up, confirmation and recovery against the real Supabase project: needs an SMTP/e-mail decision and a test mailbox; no e-mail was sent. Reviewing the Supabase dashboard settings (password length, leaked-password protection, open sign-up, redirect allow-list) is the owner's.
* Two Supabase management tokens were pasted into the chat during the work; **revoke both**.

### Blocked by legal or specialist approval

* The privacy notice text and approval; the retention schedule (every category is "keep" until chosen); breach procedure, DPIA, regulatory classification, minors' consent.
* Whether the chosen scanner and signature updates are adequate; an independent penetration test.
* The choice between `COSMO_ADMIN_CLINICAL_ACCESS=none` and `read` is an organisational decision (the code supports both; the recommended posture is `none`).
* All earlier clinical-content and model blockers (section 3) are unchanged.

### Not implemented

* Multi-factor authentication; device/session management; account self-deletion; deleting the Supabase Auth identity on erasure.
* Per-clinician patient assignment (any verified clinician can read any patient's record, as before).
* Browser (Playwright) tests of the new sign-in, register, reset and privacy screens: they pass `tsc`, `eslint` and `next build` but were not driven in a browser.
* A page-level Content-Security-Policy for the Next.js app; reconciliation of orphaned objects in Supabase Storage; backup of Supabase Storage objects; hard deletion of soft-deleted skeleton rows; an automatic in-app retention scheduler (the worker is a command-line tool).
* Application data is still SQLite until the owner performs the cut-over (the PostgreSQL layer is complete and tested).

### Not applicable

* Row-level security in SQLite (development and tests only; production refuses SQLite).
* Backing up re-downloadable public datasets and model weights.
* Mobile-shell token storage (no packaged app exists yet; see section 9).

### Known limits to keep in mind

* The SSRF guard resolves names twice, so DNS rebinding is not fully closed without egress control.
* The request timeout returns 504 but cannot interrupt a worker thread already running.
* Hosted PostgreSQL may not allow `BYPASSRLS` for the owner role, which migrations and backups need; confirm with the provider before cut-over.
* Backups keep deleted patient data until they rotate out; align `COSMO_BACKUP_KEEP_*` with the retention schedule.

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
| Application data in Postgres | IMPLEMENTED, CUT-OVER BLOCKED | Phase 2: complete PostgreSQL layer, migrations 0001-0005, RLS, copy tool, tests on PostgreSQL 17; dry run on the real project | The owner connection string, a `cosmo_app` login, the identity secret and a cut-over decision (section 0) |
| Supabase sign-in in the Next.js UI | IMPLEMENTED, NOT BROWSER-TESTED | Phase 2: sign-in, silent refresh, sign-out, register, forgot/reset password screens; backend flows tested against a protocol fake | A browser test against the real project (needs credentials and an e-mail decision) |
| Patient self-registration and account self-service | IMPLEMENTED (closed by default) | Phase 2: registration, confirmation handling, password reset; `COSMO_ALLOW_REGISTRATION=1` opens it | E-mail provider and a live test; MFA is not implemented |
| Segmentation, acne, pigmentation, redness, hair/scalp, other body-image models | MISSING / BLOCKED | Tasks are NOT registered as available | Licensed, task-matched datasets and skin-tone-diverse test data |
| Hindi/Hinglish OCR, speech and explanations | BLOCKED | English OCR/STT/TTS | Hindi OCR resources, verified speech data, bilingual clinician review |
| Medication and prescription parsing | MISSING | Prescription is an accepted document type; no medication fields are extracted | Evidence and de-identified real prescriptions to build and evaluate it |
| Agent tool routing and tool permissions | MISSING | Extractive grounded assistant, optional verified-claim LLM mode (off), model abstraction | A tool layer; deliberately not added without a model adequate for it |
| Larger language model | BLOCKED | Qwen2.5-1.5B measured inadequate and kept off | Hardware with 16 GB+ RAM / GPU and re-evaluation |
| GPU / MPS / CUDA training | IMPLEMENTED BUT UNVERIFIED | Device selection code | A GPU machine |
| Automatic model rollback on failure criteria | MISSING | Manual deploy and rollback API | Defined failure criteria; no model is deployed to patients |
| Malware scanning engine | INTERFACE DONE, ENGINE AWAITING INFRASTRUCTURE | Phase 2: clamd protocol client, fail-closed policy, `unscanned` state; parser hardening | A running ClamAV (or equivalent) |
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
| **Supabase database connection string** | Applying Phase 2 to the real database | PostgreSQL layer, migrations, RLS, backup/restore; dry run rolled back on the real project | `migrate apply`, copy of existing data, restore drill on the real database |
| **Devices and assistive technology** | Real-device camera/microphone, screen-reader results | Emulation, axe, keyboard tests | Signed checklist (section 8) |
| **Container runtime, CI host, domain, Redis** | Section 2 infrastructure items | Reviewed files and local equivalents | Build, run, issue a certificate, run CI |

## 4. Models, datasets, training and evaluation (real numbers)

Models used by the product are listed with licences and hashes in `MODEL_REGISTRY.md`: Tesseract 5.4.0, BGE-small-en-v1.5 (MIT), faster-whisper base (MIT), Qwen2.5-1.5B-Instruct Q4_K_M (Apache-2.0, off), ResNet18 ImageNet weights, DINOv2 ViT-S/14 (Apache-2.0).
Trained here, all **research-only and failing their release gates**: ResNet18 fine-tune `fc7eef89…` (macro-F1 0.502, CI 0.429-0.576, accuracy 0.637) and its reproduction run `f9396683…` (identical metrics), DINOv2 linear probe `124f81d4…` (macro-F1 0.558, CI 0.471-0.630), plus one earlier CNN. Gate failures: no improvement over the frozen-features baseline, melanoma/SCC sensitivity far below 0.80, 8 test images in Fitzpatrick 4-6 (50 required), near-duplicates, no OOD evidence for the ResNet.
Dataset: PAD-UFES-20, CC BY 4.0, official checksum verified, patient-level splits (train 1621 / val 325 / test 325 images). ISIC, Fitzpatrick17k, DDI, HAM10000, ACNE04, FUNSD are in the catalog as **unreviewed and blocked**.
Reproducibility: DINOv2 probe byte-identical; ResNet fine-tune identical metrics and split, **different artifact bytes** (`24ed3c80…` vs `a2c5e42d…`); resume-vs-uninterrupted run agrees within 1e-4 on identical seeds with one CPU thread.
Input-quality gate v1.1 on 231 untouched validation photos: motion-blur detection 93-100% (v1.0: 53-84%), false rejection of usable close-ups 9.5%; heavy JPEG is a warning (66-69% of re-encoded images flagged, 3% of usable photos flagged). Simulated degradations, lesion close-ups only.

## 5. Security, database, deployment status

* **Supabase (dev project "Cosmo Expert", Postgres 17):** migrations 0001-0005 applied, 76/76 RLS checks (baseline run showed 5 real attack paths in the original schema; all closed), security advisor 0 findings, ES256/JWKS verification, role from `profiles` not from the token, private Storage with mime and size limits. A CDN-cache defect (deleted files still downloadable at the same URL) was found by the live test and fixed in the server read path. Recommended but **not changed without your permission**: minimum password length 12+, disable open sign-up, leaked-password protection. **The management tokens used in testing were pasted into chat and must be revoked (two were used across the work).**
* **Backups:** AES-256-GCM, tamper/truncation/wrong-key detection, retention pruning, executed restore drill on development data (tiny dataset: timings say nothing about production).
* **Load and soak** (this 2-core, 7.9 GB, no-GPU machine, one backend process, persistent connections): see `docs/runs/loadtest.json` and section 7.
* Nothing in this repository is claimed production-ready: see "Production readiness" in `PROJECT_GAP_AUDIT.md`.

## 6. Test results

See section 0 for the Phase 2 verification (622 passed, 4 skipped on SQLite; PostgreSQL mode as described there), `BUILD_STATUS.md` for earlier counts, and the end of `PROJECT_GAP_AUDIT.md` for earlier regressions.

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

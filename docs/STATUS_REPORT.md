# Cosmo Experts AI: status report against the final master brief

Evidence date: 2026-10-20 (Phase 5); Phase 3 sections keep their 2026-10-19 evidence and Phase 2 sections their 2026-10-18 evidence. Every line below is backed by something that was run in this repository; where it was not run, the line says so.
Classifications: **VERIFIED COMPLETE** (implemented and its automated checks were executed here), **IMPLEMENTED BUT UNVERIFIED**, **PARTIALLY IMPLEMENTED**, **MISSING**, **BLOCKED** (needs something only the owner can provide).
"Verified" never means clinically validated, legally approved, or tested on real devices.

## 000. Phase 5: maximum dataset training and model optimisation (2026-10-20)

Detailed documents: [PHASE_5_TRAINING_REPORT.md](PHASE_5_TRAINING_REPORT.md), [DATASET_AND_TRAINING.md](DATASET_AND_TRAINING.md) section 7, [../DATASET_CATALOG.md](../DATASET_CATALOG.md), [../MODEL_REGISTRY.md](../MODEL_REGISTRY.md), [LICENSES.md](LICENSES.md).

**Nothing in this phase is clinically validated, approved or production-ready. Five research models were trained on public datasets on a 2-core CPU; every one is experimental and administrator-only, none is active, and none is shown to patients. No patient-facing feature uses any of them.**
The earlier sections keep their evidence dates. "Verified" never means clinically validated, legally approved, or tested on real devices.

Final integration verification: run once at the end on the tree that was committed.

* **Backend, SQLite**: `cd backend && .venv\Scripts\python -m pytest tests -q -p no:cacheprovider -rfEs` -> **1080 passed, 4 skipped, 0 failed** in 16 min 15 s (35 new tests: `test_phase5_data.py` 17, `test_phase5_training.py` 18). The 4 skips are the live-Supabase tests (`test_storage.py` 2, `test_supabase_auth.py` 2: "no Supabase development project credentials in the environment"); they did not run and are not counted as passed.
* **Backend, PostgreSQL mode** (disposable PostgreSQL 17, `COSMO_TEST_DB=postgres`): only the four data-touching files this phase changed or added, `test_phase5_data.py`, `test_phase5_training.py`, `test_ai_training.py`, `test_datasets_ops.py`: **78 passed, 0 failed** in 4 min 7 s. The other PostgreSQL files were **not** re-run; Phase 5 added no migration or table.
* **Frontend**: `npx tsc --noEmit`, `npx eslint .` and `npx next build` all exit 0 (19 routes).
* **Serving against evaluation**: `python -m eval.phase5_serving_check clinical-pad-isic skin-condition-scin derm-isic derm-isic-derm12345 --n 60` -> top-1 agreement 60/60 on each model, largest probability difference 0.00005 ([eval/phase5_serving_consistency.json](eval/phase5_serving_consistency.json)).
* **Not re-run**: the browser journeys (`e2e/*.mjs`) and the Phase 3 evaluations. The only screens touched are the administrator model card and the research-inference model list.
* **Secret scan** (`python backend/scripts/scan_secrets.py .`): no credentials found.

### Implemented and tested

| Module | What exists | Evidence (executed in this repository) |
|---|---|---|
| A. Acquisition from official hosts | ISIC API v2, SCIN bucket and Harvard Dataverse clients: https-only through the SSRF guard, resumable listing, back-off on HTTP 429, every file checksum-verified (MD5, S3 ETag) before it is kept, bounded safe extraction | `test_phase5_data.py`; about 15,300 real downloads, 0 failures |
| B. Licence governance | fetch-terms, verify, approve or refuse with hashed evidence and audit entries; a custom licence passes only with a recorded permission document; 9 decisions (4 approved, 5 refused) | `test_phase5_data.py`; [eval/dataset_decisions.json](eval/dataset_decisions.json) |
| C. Resource-aware training | memory-mapped resumable image cache and feature store, thread and batch chosen by measurement and bounded by free RAM, safe pause on low memory, stop request or time limit, CPU unless a verified GPU is requested, fp16 only on CUDA | `test_phase5_training.py` |
| D. Leakage control | subject-level splits per source with the earlier splitter (PAD-UFES-20's test images unchanged), identifier-less images training-only, external sources never used for selection, near-duplicates removed before any metric, PAD-UFES-20's own copies inside ISIC excluded | `test_phase5_training.py`; counts on each card |
| E. Probe training and evaluation | frozen DINOv2 ViT-S/14 plus a balanced logistic head chosen on validation; majority, k-NN and ResNet18-frozen baselines; calibration, bootstrap intervals, per-source, skin-tone and case-level tables, robustness, out-of-distribution; release gates unchanged | five models, [eval/phase5/](eval/phase5/) |
| F. Registry and serving | registered `research_only` and `experimental` through the existing registry and research interface; the card's own input check and warnings; checksums of artifact and backbone verified before loading | `test_phase5_training.py`, `test_serving_edges.py` |
| G. Comparison arm | ResNet18 layer-4 fine-tune on exactly a probe's sources, splits and baselines; resumable | `test_phase5_training.py` on synthetic images; **not run on real data** |
| H. Screens | the model card shows sources and licences, per-source and external results and the robustness reference | `tsc`, `eslint`, `next build` |

### Trained on real data (all experimental; none approved)

| Model | Task | Data | Test n | Macro-F1 (95% CI) | Best baseline | Gates | Fails |
|---|---|---|---|---|---|---|---|
| `c128eb96` `clinical-pad-only` | lesion_classification_research | PAD-UFES-20 | 325 | **0.558** (0.471-0.630) | 0.508 | 7/11 | improvement_supported_by_ci, critical_class_sensitivity, dark_skin_evaluated, ood_detection |
| `0f7e066f` `clinical-pad-isic` | lesion_classification_research | PAD-UFES-20 + ISIC clinical | 374 | **0.559** (0.496-0.614) | 0.476 | 8/11 | critical_class_sensitivity, dark_skin_evaluated, ood_detection |
| `e0ce44b3` `skin-condition-scin` | skin_condition_research | SCIN | 406 | **0.347** (0.287-0.395) | 0.223 | 9/10 | ood_detection |
| `bab527c0` `derm-isic` | dermoscopy_lesion_research | ISIC dermoscopy (external: DERM12345) | 1343 | **0.404** (0.368-0.437) | 0.344 | 8/11 | critical_class_sensitivity, dark_skin_evaluated, ood_detection |
| `0e29da33` `derm-isic-derm12345` | dermoscopy_lesion_research | ISIC dermoscopy + DERM12345 | 2053 | **0.472** (0.444-0.497) | 0.405 | 8/11 | critical_class_sensitivity, dark_skin_evaluated, ood_detection |

### Blocked

* **Data and licences**: acne, pigmentation, redness, dryness, texture/scarring, hair and scalp tasks have no dataset with traceable rights and matching labels (970 candidates surveyed); dark-skin test images for every lesion task. Details and what would unblock each: [PHASE_5_TRAINING_REPORT.md](PHASE_5_TRAINING_REPORT.md) section 8.
* **Legal**: the ISIC contributor licences (CC-0/CC-BY, version of CC-BY not stated) and the custom SCIN licence were accepted by the Phase 5 run on the owner's instruction on a recorded permission document; a lawyer must confirm them before commercial use. The ResNet18-ImageNet weights are unresolved for commercial use.
* **A clinician**: no model has a clinical review, an intended use or a sign-off.
* **Hardware**: a GPU or 16 GB+ of RAM for larger or fine-tuned vision models; the GPU code path has never been run.
* **Credentials**: nothing in this phase touched the real Supabase project. **Two Supabase management tokens were pasted into the chat in earlier phases; revoke both.**

### Approved

None. No model, dataset-derived claim or wording has a recorded clinical approval.

### Known gaps found or kept during this phase

**Defects found and fixed (each with a test or a recorded correction):** PAD-UFES-20's own images were 2,298 of the ISIC clinical records and would have leaked test images into training (excluded by attribution); an ISIC query that required an optional field silently kept 56 images instead of 474
(version invalidated, rebuilt); ISIC's published file sizes are 3,173 bytes off (verification by S3 ETag); HTTP 429 from the ISIC site (back-off, evidence cache, independent decisions); the image cache raised "paused" after its last chunk and held a memory map open so Windows refused the rename; a bookkeeping
layout in the first three cards broke the registry exporter and showed blank rows on screen (fixed, cards corrected, audit entries); refusal reasons were hidden; an empty evidence item. Details: report section 9.

**Still open, measured or known:**

* **Every model fails at least one release gate**, and all are more confident on non-skin pictures than on real test pictures (out-of-distribution AUROC below 0.5 where 0.5 is chance), so an input check must precede any use; the dermoscopy models have none and must never see a phone photograph.
* **The gates measure process, not usefulness**: the skin-condition model passes 9 of 10 gates with 34% accuracy on 11 conditions. No threshold was changed.
* **Serious-class sensitivity** (melanoma, basal and squamous cell carcinoma) is far below the 0.80 gate for phone photographs and is reported per model; darker skin is effectively untested for every lesion model (8 Fitzpatrick IV-VI test images for phone photographs, 20 in all of ISIC dermoscopy).
* **Labels** are from public datasets collected for other purposes (pathology or dermatologist opinion from photographs); skin-tone labels in SCIN are estimates; the same PAD-UFES-20 test images were reused across experiments, so those results are not independent of each other.
* **Not run**: the ResNet18 fine-tune on the new data (unit-tested code only); any GPU path; any real-device or clinic test. The registry's task-compatibility check is loose (it lists unrelated datasets as compatible; the licence gate and explicit source lists decide).

## 00. Phase 3: unified AI engine, medical image intelligence, document AI, agent automation and model lifecycle (2026-10-19)

Detailed documents: [PHASE_3_AI_ENGINE.md](PHASE_3_AI_ENGINE.md), [DATASET_AND_TRAINING.md](DATASET_AND_TRAINING.md), [MEDICAL_DOCUMENT_INTELLIGENCE.md](MEDICAL_DOCUMENT_INTELLIGENCE.md), [AI_AGENT_AND_TOOL_SECURITY.md](AI_AGENT_AND_TOOL_SECURITY.md), [MODEL_EVALUATION_AND_RELEASE.md](MODEL_EVALUATION_AND_RELEASE.md), [VOICE_AND_MULTILINGUAL.md](VOICE_AND_MULTILINGUAL.md).

**Nothing in this phase is clinically validated, clinically approved or production-ready. No learned model is approved or active, none is offered to patients, no real model was trained and no dataset was downloaded in this phase (the automated tests train tiny throw-away models on generated images to exercise the pipeline).** Every evaluation below used inputs written or generated by this project (evidence class `synthetic_fixture`) or, for the four research image models, a public research dataset (PAD-UFES-20). No real patient document, recording or photograph from a clinic was evaluated.

How the words are used here. **Implemented**: the code exists and is wired in. **Tested**: automated checks were executed in this repository and the figures are given. **Experimental**: works, but only for administrators, or only on synthetic evidence. **Blocked**: needs something only the owner, a clinician, a lawyer or hardware can provide. **Approved**: a human with the authority recorded it; **nothing in Phase 3 has been approved by a clinician.**

Final integration verification: run once at the end on the tree that was committed (earlier runs during the phase found the defects listed below; every figure here is from the last run).

* **Backend, SQLite**: `cd backend && .venv\Scripts\python -m pytest tests -q -p no:cacheprovider -rfEs` -> **1045 passed, 4 skipped, 0 failed** in 14 min 56 s. The 4 skips are the live-Supabase tests (`test_storage.py` 2, `test_supabase_auth.py` 2: "no Supabase development project credentials in the environment"); they did not run and are not counted as passed.
* **Backend, PostgreSQL mode** (disposable PostgreSQL 17; `COSMO_TEST_DB=postgres` with the same command naming the 34 data-touching test files, `test_agent.py` ... `test_voice.py`, in their alphabetical order): **761 passed, 2 skipped, 0 failed** in 8 min 13 s (the 2 skips are the storage tests above). The first PostgreSQL run of this phase had 3 failures and 2 errors and a later one 1 failure; each was a real defect or a test-hygiene problem, fixed below. The only test change that loosens anything: one fixture's clean-up now tolerates PostgreSQL's refusal to delete append-only training history (it re-raises any other error).
* **Frontend**: `npx tsc --noEmit`, `npx eslint .` and `npx next build` all exit 0 (19 routes).
* **Browser** (fresh database, real Chromium): `node e2e/phase3-ui.mjs` **39/39**; `node e2e/p11-a11y.mjs` **65/65** (axe WCAG 2.0-2.2 A/AA, no violations on any page or role, desktop and 390 px).
* **Evaluations re-run this phase**: document extraction (`docpipe-2.0.0`, 16 reports x 8 conditions) and the medication parser (`medparse-1.1.1`) after the "Serum" fix, and the voice evaluation (357 s). Code changed afterwards only where they do not measure (clinician-name extraction, model-history timestamps, the download job's audit order, the screens). Records are under `docs/eval/`.
* **Secret scan** (`python backend/scripts/scan_secrets.py .`): no credentials found.

### Implemented and tested

| Module | What exists | Evidence (executed in this repository) |
|---|---|---|
| A. Unified AI engine, capability registry | one registry of capabilities with availability and approval states, one result envelope with provenance, bounded worker pool and queue, cancellation, memory-aware model cache, CPU-unless-verified device policy, no mock or substitute when real inference cannot run | `test_ai_engine.py`, `test_ai_gates.py` |
| B. Medical image intelligence | category routing (face, hair/scalp, body, lesion close-up, photo of a report), quality v2 with rejections only where validated on real photographs, honest "no model available" for every task without a validated model | `test_ai_vision.py`, `test_imaging*.py`, `docs/eval/quality_v2.json` |
| C. Datasets, training orchestration | licence gate, safe importer, versioned splits, resource plan before any run, reusable task pipeline, model cards (no run started in this phase) | `test_ai_training.py`, `test_datasets_ops.py`, `test_training_resume.py` |
| D. Document intelligence | provenance on every item, header facts, medication parser, cited and verified explanations, abstention, contradictions | `test_documents_structured.py`, `test_medication.py`, `test_ai_explain.py`, `docs/eval/medication_parser.json`, `docs/eval/doc_extraction.json` |
| E. Grounded retrieval, injection defence | passage-level approved knowledge with measured abstention thresholds; instruction-like text kept out of answers and prompts | `test_ai_knowledge.py`, `test_ai_injection.py`, `test_grounded.py`, `test_assistant_grounding.py`, `docs/eval/kb_retrieval.json` |
| F. Secure agent, typed tools | 13 typed tools, server-side permissions, confirmation for every change, rule-based routing, model providers gated and off | `test_agent.py`, `docs/eval/agent.json` |
| G. Shared safety and abstention policy | one policy for the assistant, agent, knowledge search, explanations and speech; every fixed sentence a patient can see is itself checked against it | `test_ai_safety_policy.py` |
| H. Voice and multilingual | bounded speech recognition that cannot invent speech from silence; spoken text checked against the screen; protected-token preservation; visible "unreviewed" notice for Hindi and Hinglish | `test_voice.py`, `test_spoken.py`, `test_multilingual.py`, `docs/eval/voice.json` |
| I. Model registry and release control | lifecycle state machine with append-only events, approval needs passed gates plus a verified clinician, checksum-verified activation, known-good fallback, technical rollback kept apart from clinical issues, monitor (automatic rollback off by default) | `test_model_lifecycle.py`; `test_pg_rls.py` (events table, PostgreSQL) |
| J. Performance and reliability | see A; per-capability concurrency and timeouts, temporary files removed, metrics are counts and durations only | `test_ai_engine.py` |
| K. API and frontend integration | assistant-actions card (propose, then confirm or cancel), report sections found by id, unreviewed-translation notice, administrator model cards for models that have never been evaluated, sign-off and lifecycle controls | `tsc --noEmit`, `eslint .`, `next build` (19 routes); `e2e/phase3-ui.mjs`; `e2e/p11-a11y.mjs` |
| L. Evaluation framework | evidence classes stamped on every record, reports carry code revision, configuration hash and environment, first-run figures kept beside later ones | `test_ai_evaluation.py` |

### Experimental or research-only

* Four lesion-classification research models on PAD-UFES-20 (macro-F1 0.502-0.558, intervals 0.43-0.63): administrator-only, **all fail the release gates**.
* Hindi OCR: experimental, the language pack is **not installed** here. Speech recognition: English only, on synthetic speech.
* Language-model answers and a model-based request classifier: implemented behind gates and **off**; Qwen2.5-1.5B fails the answer gates.
* Hindi and Hinglish explanation sentences: carry every number, unit, date and printed text (tested), but **no Hindi-speaking clinician has reviewed them**.

### Blocked

* **A clinician**: approval of knowledge-base articles, the urgent-symptom policy, Hindi and Hinglish wording, and any model.
* **Data and licences**: licensed, task-matched images for acne, pigmentation, redness, scarring, hair and scalp, and dark-skin test images; de-identified real documents and recordings to evaluate extraction, speech and the medication parser for real.
* **Hardware**: a GPU or 16 GB+ of RAM for larger models and faster training; the GPU code path has never been run.
* **Credentials**: applying migrations 0006-0009 to the real Supabase project. They were tested on a disposable PostgreSQL 17 only; the Phase 2 position is unchanged. **Two Supabase management tokens were pasted into the chat during the work; revoke both.**

### Approved

None. No model, article, wording or policy has a recorded clinical approval in this repository.

### Known gaps found or kept during this phase

**Defects the verification itself found, all fixed here, each with a regression test (so they are no longer gaps, but they show how the earlier batches were checked):**

* **Laboratory rows that start with "Serum"** (Serum Iron, Serum Creatinine, Serum Sodium ...) were taken for skincare serums by the medication parser of Batch 4 and **dropped from the results**. Found only by re-running the extraction evaluation (digital recall 0.955 instead of 1.00); the unit tests had no such row. Fixed (`medparse-1.1.1`); the evaluation now equals the `docpipe-1.0.0` record on every accuracy field.
* **Model history order**: two events in the same tick of the system clock could be read back swapped (seen once in the PostgreSQL run). Event times are now strictly increasing within a process.
* **`model_events` foreign key**: in PostgreSQL it blocked removing a registry row, which the history should never do; the history deliberately has no foreign key (migration 0009).
* **Dataset download audit** (older code, exposed by the slower PostgreSQL round trip): the download job set its state to done or failed *before* writing its audit record, so a reader of the final state could look too early and miss it (one test failed once for this reason). The record is now written first.
* **Clinician names**: "Dr. A Example, MD" was recorded as just "Dr", and "Self" or "Lab technician" as clinicians (seen in the browser run). Names are now copied whole; non-names are not recorded.
* **Screens**: a quick-action label overflowed a 390 px screen; the note asking the person to check a transcript was cleared the moment it was set, so a language-mismatch warning from the recogniser could never be seen; a model that had never been evaluated would have crashed the administrator's model list; Hindi and Hinglish wording was labelled unreviewed in the API but not on screen.

**Still open, measured or known:**

* **Urgent-symptom recognition is only as good as the approved policy.** With the built-in default list, 0 of 3 phrasings outside it ("my face and lips are swelling up" ...) were recognised (`docs/eval/agent.json`). A clinician must approve a policy.
* **Request routing**: 35 of 39 (90%, 95% interval 76-96%) on one fresh set written by one author; the hand-written rules alone chose the intended action 54-79% of the time on phrasing they had not seen. English only. Every miss falls to a question or a clarification, never to a change.
* **Everything measured is synthetic or public-research data.** The medication parser's held-out lines were tuned on after a first run (first-run recall 0.939), so its 1.00 is not independent; the knowledge-retrieval query sets are small and written by one author over unreviewed draft articles; speech was synthesized (word error 4.4% clean, 8.9% telephone band; roughly one key term in eight misheard; real-time factor about 1.2 on this CPU).
* **Hindi and Hinglish**: no recognition test, no reviewed wording, no Hindi voice installed, Hindi OCR pack not installed.
* **Tests**: one failure (`test_clinical_content.py::test_approval_is_recorded_and_bound_to_the_exact_text`) appeared once in an earlier full SQLite run and was **not explained**; it did not recur in any later full run, including the two below. 4 tests that need live Supabase credentials were skipped (no credentials were put in the test environment) and are not counted as passed.
* **Browser coverage**: this phase re-ran only `e2e/phase3-ui.mjs` and `e2e/p11-a11y.mjs`. `ui-journey.mjs`, `clinical-journey.mjs`, `p11-admin.mjs`, `p11-browsers.mjs` and `p11-consent.mjs` were not re-run (they need fixtures, datasets or a consent configuration this run did not set up). No screen-reader or real-device test.
* **Model lifecycle**: the monitor keeps recent outcomes in memory (a restart empties them); there is no four-eyes rule between trainer and approver and no automatic notification of an open clinical issue; the whole lifecycle has only been exercised on fixtures because no model is approved.
* **Document handling**: in a document classified as a prescription, an unprefixed line with a concentration and the abbreviation "HS" is still read as a medicine entry; no real patient document, handwriting or multi-column layout was ever read.
* **Not applied to the real database**: migrations 0006-0009 (and the PostgreSQL layer of Phase 2); the Phase 2 position is unchanged.

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
| Segmentation, acne, pigmentation, redness, hair/scalp, other body-image models | BLOCKED (pipeline done in Phase 3) | The reusable task pipeline, licence gate, importer, resource-planned training and readiness check exist; every task is reported `blocked`; none is available to patients; segmentation is deliberately not implemented | Licensed, task-matched datasets and skin-tone-diverse test data |
| Hindi/Hinglish OCR, speech and explanations | PARTIALLY IMPLEMENTED, UNREVIEWED | English OCR/STT/TTS; Phase 3 added fixed Hindi and Hinglish explanation sentences that keep every number, unit, date and printed text (tested) and are shown with an "unreviewed" notice; Hindi OCR is experimental and its pack is not installed here; no Hindi voice is installed; speech recognition was checked on synthetic English speech only | Hindi OCR resources, verified speech data, a bilingual clinician reviewing the wording |
| Medication and prescription parsing | IMPLEMENTED (Phase 3), SYNTHETIC EVIDENCE ONLY | Rule-based parser `medparse-1.1.1`: components exactly as printed with spans, stated ambiguity, nothing recommended or filled in; clinician confirm/reject/correct; measured on 60 written lines (`docs/eval/medication_parser.json`) | De-identified real prescriptions (handwriting, other layouts) to measure it for real |
| Agent tool routing and tool permissions | IMPLEMENTED (Phase 3), TESTED ON SYNTHETIC SCENARIOS | 13 typed tools, server-side permissions, confirmation for every change, rule-based intent routing plus nearest written example (35/39 on one fresh set), model providers gated and off (`docs/eval/agent.json`) | Real-user phrasing; a clinician-approved urgent-symptom policy; an adequate model before any model provider is enabled |
| Larger language model | BLOCKED | Qwen2.5-1.5B measured inadequate and kept off | Hardware with 16 GB+ RAM / GPU and re-evaluation |
| GPU / MPS / CUDA training | IMPLEMENTED BUT UNVERIFIED | Device selection code | A GPU machine |
| Automatic model rollback on failure criteria | IMPLEMENTED (Phase 3), OFF BY DEFAULT | Model lifecycle with known-good fallback, technical rollback, clinical-issue handling, monitor on checksum failure and Wilson lower-bound failure rate; `COSMO_MODEL_AUTO_ROLLBACK=1` turns automatic action on | No model is deployed to patients, so it has only been exercised on fixtures |
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
| **Datasets and licences** | Acne, pigmentation, redness, scarring, hair and scalp data; dark-skin test data | Catalog, licence gate, importer; PAD-UFES-20, two ISIC subsets, SCIN and DERM12345 imported with verified licences; 970 further candidates surveyed, 5 datasets refused with evidence | Datasets with traceable rights and matching labels (Phase 5 report section 8); a lawyer's confirmation of the ISIC and SCIN licences |
| **GPU / more RAM** | Larger models, faster training, a 7B+ language model | CPU pipelines, 2-core measurements | Re-run training and `eval/llm_eval.py` |
| **Supabase database connection string** | Applying Phase 2 to the real database | PostgreSQL layer, migrations, RLS, backup/restore; dry run rolled back on the real project | `migrate apply`, copy of existing data, restore drill on the real database |
| **Devices and assistive technology** | Real-device camera/microphone, screen-reader results | Emulation, axe, keyboard tests | Signed checklist (section 8) |
| **Container runtime, CI host, domain, Redis** | Section 2 infrastructure items | Reviewed files and local equivalents | Build, run, issue a certificate, run CI |

## 4. Models, datasets, training and evaluation (real numbers)

Models used by the product are listed with licences and hashes in `MODEL_REGISTRY.md`: Tesseract 5.4.0, BGE-small-en-v1.5 (MIT), faster-whisper base (MIT), Qwen2.5-1.5B-Instruct Q4_K_M (Apache-2.0, off), ResNet18 ImageNet weights, DINOv2 ViT-S/14 (Apache-2.0).
Phase 5 (2026-10-20) added five DINOv2-probe research models on four more licence-verified sources: see section 000 and [PHASE_5_TRAINING_REPORT.md](PHASE_5_TRAINING_REPORT.md). All are research-only and fail at least one release gate.
Phase 3 models, all **research-only and failing their release gates**: ResNet18 fine-tune `fc7eef89…` (macro-F1 0.502, CI 0.429-0.576, accuracy 0.637) and its reproduction run `f9396683…` (identical metrics), DINOv2 linear probe `124f81d4…` (macro-F1 0.558, CI 0.471-0.630), plus one earlier CNN. Gate failures: no improvement over the frozen-features baseline, melanoma/SCC sensitivity far below 0.80, 8 test images in Fitzpatrick 4-6 (50 required), near-duplicates, no OOD evidence for the ResNet.
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

# Phase 3 -- the data-touching test files against a disposable PostgreSQL 17: set COSMO_TEST_DB=postgres and name the files (the list is in section 00)
# Phase 3 -- evaluations (each rewrites its record under docs/eval; idle machine for the voice one)
cd backend && .venv\Scripts\python -m eval.doc_extraction_eval --docs 16 --seed 7 --out ../docs/eval
.venv\Scripts\python -m eval.medication_eval --heldout-first-run ../docs/eval/medication_parser.json
.venv\Scripts\python eval\kb_retrieval_eval.py && .venv\Scripts\python eval\agent_eval.py && .venv\Scripts\python eval\voice_eval.py
# Phase 3 -- the new screens in a real browser (stack on :3000/:8000 with a FRESH database; the script header lists the optional variables for the voice and administrator parts)
cd e2e && node phase3-ui.mjs && node p11-a11y.mjs
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

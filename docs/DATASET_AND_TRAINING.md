# Datasets and training (Phase 3; Phase 5 update in section 7)

How image datasets are registered, imported and checked, and how a training run is planned, run, recorded and registered. Code: `backend/app/datasets/`, `backend/app/training/{orchestrator,pipeline,task_specs,cards,trainer,probe}.py`.
Tests: `tests/test_ai_training.py` (22), `tests/test_api.py::test_import_split_train_approval_rollback`.

**(Phase 3:) No new model was trained in this phase and no dataset was downloaded. Phase 5 then downloaded and trained: see section 7.** (The automated tests train tiny throw-away models on a few dozen generated images to exercise the pipeline; none is kept, and they are fixtures, not models anyone could use.) The pipeline was finished for every image task; each task is reported `blocked` until a licensed, compatible, imported
dataset exists. Nothing here makes any model available to patients (that is the model lifecycle: [MODEL_EVALUATION_AND_RELEASE.md](MODEL_EVALUATION_AND_RELEASE.md)).

## 1. Dataset registry

`datasets/registry.py` holds a catalogue of candidate public datasets (PAD-UFES-20, HAM10000, DDI, Fitzpatrick17k, ACNE04 ...) with what is known about each: source, licence as claimed, labels, size.
A dataset becomes usable only through a chain of recorded steps, each by a named administrator:

1. **Fetch the official terms** (`POST /admin/datasets/{id}/fetch-terms`): the licence text is snapshotted and hashed.
2. **Verify the licence** (`POST .../verify`): the administrator names the SPDX licence and the permission document; the evidence hash is stored.
3. **Approve** (`POST .../approve`): a separate decision. The *licence gate* needs all three; a dataset that fails it cannot be imported or trained on (`dataset.import_blocked` is audited).
4. **Import** (`POST .../import-folder`, from a folder on the server): files are copied into a versioned directory, checksummed, checked for exact duplicates, labelled through a recorded class mapping and split.

A version records: file list with checksums, class distribution, the split (subject-level: no patient in two splits), near-duplicates found between splits, the importer version (`importer-2.0.0`), the
licence snapshot and the import status (`complete` or not). A training run **refuses an incomplete version**. `POST /admin/datasets/cleanup-partials` removes leftovers of cancelled or failed imports.
`GET /admin/datasets/{id}/card` is a plain-language dataset card generated from what is recorded ("not recorded" where something is missing; paths shown relative to the data directory).

## 2. The importer (`datasets/importer.py`)

* Archives are extracted by `safe_unzip`: no path escapes, no links, bounded member count (200,000), bounded total size (50 GB) and bounded compression ratio (200:1); partial output is swept on failure.
* Downloads (where an administrator starts one) go through the SSRF guard; plain local HTTP only with `COSMO_ALLOW_LOCAL_HTTP`.
* Files are verified after copy (`verify_files`); images that fail to decode are reported, not silently dropped; exact duplicates are detected and near-duplicates are only *flagged* (a perceptual hash over-matches low-texture images, so it never deletes data); the manifest hash is the identity of the version.
* No patient data and no dataset is ever committed to Git (`data/` is ignored; `scan_secrets.py` is run before commits).

## 3. Training orchestration (`training/orchestrator.py`)

* **Configuration**: a validated, hashable `TrainConfig` (task, dataset, version, architecture, seed, epochs, learning rate, batch, image size, device...). The configuration hash, dataset version, seed and
  code revision are what the model card records and what *resume* is keyed on.
* **Resource plan before anything starts** (`POST /admin/train/plan`): free memory (`COSMO_TRAIN_RAM_FRACTION`, default conservative), free disk, estimated duration (calibrated from this machine's earlier
  recorded runs, then a conservative default; limit `COSMO_TRAIN_MAX_SECONDS`, override only with `allow_long_run`) and device. A run that does not fit is **refused with reasons**; on this host (limited
  RAM, 2-4 cores, no GPU) nothing large starts by accident.
* **One run at a time** per process; progress, cancellation and resume pass through to the trainer, whose checkpoints are integrity-checked.
* **Every run is recorded** in `training_runs` (configuration, plan, status, timings, resulting model id, a sanitised error). A run that ends abnormally leaves no half-written model artifact (checkpoints
  stay, for resume); runs left `running` by a crash are marked `interrupted` at start-up.
* **Device policy**: CPU unless a GPU is explicitly requested; a GPU is "verified" only with a recorded `scripts/verify_gpu.py` run on suitable hardware. No GPU exists here, so GPU use is an adapter that has
  **not** been exercised; it is always reported as unverified.
* **Measured on this machine** (4 logical CPUs, no GPU): fine-tuning ResNet18 layer4 at 224 px, 6 epochs over 1,621 images: 1,199 s; the DINOv2-small linear probe over 2,271 images: 755 s.

## 4. The reusable task pipeline (`training/pipeline.py`, `task_specs.py`)

`task_specs.py` states, per task, what a *legitimate* run requires: the task definition, label scheme, unit of analysis, minimum images per class and subjects, whether a skin-type field is needed to
evaluate across skin tones, metrics, critical classes and candidate datasets.

| Task | Output | Min images/class | Min subjects | Skin type needed | Candidate datasets in the catalogue |
|---|---|---|---|---|---|
| `acne_features` | ordinal | 100 | 100 | yes | ACNE04 |
| `pigmentation`, `redness_irritation`, `dryness_scaling`, `texture_scarring` | classification | 100 | 100 | yes | none identified |
| `hair_thinning` | ordinal | 100 | 100 | no | none identified |
| `scalp_findings` | classification | 100 | 100 | no | none identified |
| `lesion_classification_research` | classification | 20 | 200 | yes | PAD-UFES-20, HAM10000, DDI, Fitzpatrick17k |

`GET /admin/tasks/{task}/readiness` answers "can a run start today, and if not, why not?" with explicit blockers (no compatible dataset; no dataset past the licence gate; approved but not imported).
On a fresh installation **every task is `blocked`** (after Phase 5 three research tasks are ready: lesion classification on phone photos, dermoscopy lesion classification, and skin-condition recognition; the other seven stay blocked). `pipeline.validate_dataset` rejects meaningless runs (too few images or subjects, subject-level leakage between splits, missing skin-type data where it is
needed) and records warnings that qualify results. A fixture run on synthetic images is stamped `synthetic_fixture`, carries no licence and therefore **cannot pass the release gates**.
Segmentation is deliberately **not implemented**: it needs pixel-level masks and a real use case, and no catalogued dataset provides masks.

## 5. Model cards and registration

A finished run writes a model card (`training/cards.py`): architecture, classes, dataset and version ids, split hash, seed, device, baselines (majority class and others), test metrics with intervals,
per-class results, calibration, subgroup table, near-duplicate counts, artifact checksum, code revision, dataset licence, limitations and the statement "Research only. Not clinically validated. Not
offered to patients." The model is registered as `research_only` (lifecycle `experimental`, first event `registered`). The release gates (`trainer.GATES`) are computed from the card and never edited.

## 6. What exists and what does not

* **Exists**: four research models for lesion classification trained on PAD-UFES-20 (two ResNet18, two DINOv2 probes), re-evaluated on the held-out split ([MODEL_EVALUATION_AND_RELEASE.md](MODEL_EVALUATION_AND_RELEASE.md)), and the five Phase 5 probes (section 7).
  All fail the release gates; none is offered to patients.
* **Not done**: no pigmentation, acne, redness, scarring, hair or scalp model (no licensed data is present here); no GPU run; no training on real clinic data (none exists and none may be used without
  consent and governance - consent is never a consent to train); no segmentation.
* **Operating**: `POST /admin/train/plan` first, then `POST /admin/train` (returns a job id); `GET /admin/jobs/{job}` for progress, `POST /admin/jobs/{job}/stop` to stop (progress inside an unfinished epoch is lost; the last checkpoint is kept); `GET /admin/training-runs` for history; `GET /admin/train/checkpoints` to see what can be resumed.

## 7. Phase 5 (2026-10-20): real acquisition, probes and resource-aware training

Phase 5 did what the sections above prepared: it downloaded licence-verified datasets and trained real research models. Results, numbers and blockers are in [PHASE_5_TRAINING_REPORT.md](PHASE_5_TRAINING_REPORT.md); this section is the how.
Tests: `tests/test_phase5_data.py` (16), `tests/test_phase5_training.py` (18).

**Acquisition from official hosts** (`datasets/sources.py`): ISIC API v2 (listing with a resumable cursor, a query that can only return CC-0/CC-BY records and a second check that refuses any other licence), the SCIN public
bucket and Harvard Dataverse (DERM12345). Every request is https-only and goes through the SSRF guard; throttling (HTTP 429) is waited out with the server's `Retry-After`; each file is written to `.part` and renamed only after its
checksum matches (MD5 from the bucket or Dataverse, the S3 ETag for ISIC), so an interrupted download resumes and a corrupt file is never kept; archives go through `safe_unzip`. `scripts/phase5_data.py <name>` runs
the chain (licence gate check, build, `importer.ingest_folder`) and prints class and Fitzpatrick distributions.

**Licence decisions** (`datasets/governance.py`, `scripts/phase5_governance.py`): the same fetch-terms, verify, approve steps as the administrator routes, with hashed evidence and audit entries, for approvals and refusals.
A custom licence (ISIC contributor licences, SCIN) passes the gate only through a non-empty recorded permission document. `docs/eval/dataset_decisions.json` is rebuilt from the registry.

**Tasks added** (`training/tasks.py`, `task_specs.py`, `trainer.TASK_SAFETY`): `dermoscopy_lesion_research` (NV, MEL, BCC, SCC, AK, BKL, DF, VASC; critical classes MEL, BCC, SCC) and `skin_condition_research`
(11 conditions, no critical class). Release gates are the Phase 3 gates, unchanged; a task with no critical class simply has no sensitivity gate.

**Training method** (`training/features.py`, `training/featuretrain.py`): a frozen DINOv2 ViT-S/14 backbone (Apache-2.0) gives a 768-value descriptor (class token and mean patch token); the head is a standardised, class-balanced
multinomial logistic regression. The descriptor kind and the regularisation strength are chosen on the validation split only, the temperature is fitted on validation, the test split is scored once. Baselines are always stored beside
the result: the majority class, a cosine k-nearest-neighbour classifier on the same descriptors, and a frozen ResNet18-ImageNet descriptor with the same kind of head (comparison only).

**Why a probe and not a fine-tune** on this machine (2 physical cores, no GPU): the backbone costs about 0.2 s per image, so features are computed once per dataset version and everything after that (any number of heads, selections and
re-evaluations) costs seconds. The earlier ResNet18 fine-tune on PAD-UFES-20 scored lower than the DINOv2 probe on the same test images (macro-F1 0.502 against 0.558), and int8 quantisation was measured and rejected (+10 % speed,
cosine similarity 0.966 to the float model). `training/finetune.py` is a ResNet18 layer-4 fine-tune that can be run on exactly the same splits for a like-for-like comparison; it is unit-tested on synthetic images and was **not** run on the real
datasets in Phase 5.

**Resource handling** (`training/features.py`): the thread count and batch size are chosen by a short measurement on the machine and bounded by the memory that is free right now; the uint8 image cache and the float32
feature store are memory-mapped, written in chunks and resumable; a run that cannot continue (free memory below 120 MB for 90 s, a stop request, a time limit) saves its position and raises `Paused`, recorded as a stopped run
that continues when started again. The device comes from `ai.resources.select_device` (CPU unless a verified GPU is requested); mixed precision (fp16 autocast) is used only on CUDA and has never been run here.

**Leakage control**: each source is split by its own deterministic subject-level splitter (the one the earlier models used, so PAD-UFES-20's test images are the same as before); images without a patient or lesion identifier
are used for training only; an external source is never used for training, selection or calibration; and validation, test and external images whose 32x32 grayscale thumbnail correlates 0.985 or more with a training image are
removed before any metric (the counts are on the card). A SCIN case's photographs always stay together, and case-level scores (averaged logits) are reported beside image-level ones.

**Serving**: the artifact is a small `.npz` (scaler, weights, temperature); `training/serving.py` loads it through the existing registry and research interface, verifies the checksums of the artifact and the backbone, applies
the card's own input gate (`serving.quality_gate`; dermoscopy uses none) and prints the card's own warnings plus the release gates that failed. Predictions remain administrator-only and are labelled research output.

**Commands**
```
cd backend
.venv\Scripts\python -m scripts.phase5_governance            # licence decisions (needs network); --only <name> to repeat one
.venv\Scripts\python -m scripts.phase5_data isic-clinical scin isic-dermoscopy derm12345
.venv\Scripts\python -m scripts.phase5_train list
.venv\Scripts\python -m scripts.phase5_train run clinical-pad-only      # also: clinical-pad-isic, skin-condition-scin, derm-isic, derm-isic-derm12345
.venv\Scripts\python -m scripts.phase5_export                          # docs/eval/phase5/*.json, docs/eval/phase5_jobs.json
.venv\Scripts\python -m scripts.export_registry ../MODEL_REGISTRY.md
```

# Phase 3: the unified AI engine (overview)

What was built in Phase 3, how the parts fit, and what each part is and is not. Detailed documents: [MEDICAL_DOCUMENT_INTELLIGENCE.md](MEDICAL_DOCUMENT_INTELLIGENCE.md), [AI_AGENT_AND_TOOL_SECURITY.md](AI_AGENT_AND_TOOL_SECURITY.md),
[MODEL_EVALUATION_AND_RELEASE.md](MODEL_EVALUATION_AND_RELEASE.md), [DATASET_AND_TRAINING.md](DATASET_AND_TRAINING.md), [VOICE_AND_MULTILINGUAL.md](VOICE_AND_MULTILINGUAL.md). Status and evidence: [STATUS_REPORT.md](STATUS_REPORT.md) section 00.

**Nothing in this phase is clinically validated, clinically approved or production-ready.** No learned model is offered to patients. The patient-facing AI that works is rule-based, retrieval-based or
classical image analysis, every output is labelled as a draft for clinician review, and the system abstains or says "not available" instead of guessing.

## 1. One engine, honest states (`backend/app/ai/`)

Every AI capability is defined once in `ai/registry.py` and resolved against reality on each request: installed tools and language packs, model files, approved knowledge, and the model lifecycle.
`GET /api/v1/ai/capabilities` lists them (patients see whether each can be used and its limits; staff also see why one is unavailable, which model backs it and its approval state).

| Group | Capabilities | Maturity today |
|---|---|---|
| Photo | `image.quality`; eight task capabilities (`acne_features`, `pigmentation`, `redness_irritation`, `dryness_scaling`, `texture_scarring`, `hair_thinning`, `scalp_findings`, `other_uncertain`); `image.lesion_classification_research` | quality: deterministic and available; **the tasks have no validated model and are unavailable**; the lesion model is research-only, administrator-only |
| Documents | `document.extract`, `document.medications`, `document.explain`, `document.ocr.hindi` | deterministic; clinician review required; Hindi OCR experimental and **not installed here** |
| Knowledge and agent | `knowledge.search`, `agent.chat` | deterministic; need approved articles / patient sign-in |
| Language model | `llm.generate` | administrator-only; **fails its gates; off** |
| Voice | `speech.transcribe`, `speech.synthesize` | deterministic wrappers over a local model / OS voices; English only checked |
| Translation | `translation.text` | **planned, unavailable**: no reviewed translation engine exists |

Two orthogonal states are reported: **availability** (`available | degraded | unavailable`) and **approval** (`none | not_required | research_only | review_required | approved_for_defined_use`); the older single
status vocabulary is derived from them. A learned model is patient-facing only when it is approved **and** the task's active model (see MODEL_EVALUATION_AND_RELEASE.md). The engine **never substitutes**
another model or a mock: if the real capability cannot run, the answer is `unavailable`, with the reason for staff.

`engine.run(capability, payload, user=...)` returns one envelope (`ok | abstained | unsupported | unavailable | rejected_input | review_required | forbidden | error | timeout | cancelled |
resource_exhausted`) carrying labels, warnings, the shared safety review, and **provenance**: capability and version, engine version, code revision, model reference (id, architecture, checksum prefix, lifecycle
state), dataset reference, processing version, device. Handlers know nothing of HTTP or permissions; the engine owns availability, limits, safety and provenance; routers own transport.

**Performance and reliability** (`ai/resources.py`): a bounded worker pool with a bounded queue (`COSMO_AI_MAX_CONCURRENCY`, `COSMO_AI_MAX_QUEUE`; a full queue is an explicit `resource_exhausted`), per-capability
concurrency and timeouts, cooperative cancellation, a model cache that loads lazily and refuses a load that would leave less than `COSMO_AI_MIN_FREE_MB` of memory free, device selection (the CPU unless a GPU has a
recorded verification, or one is explicitly requested; always reported verified or unverified), temporary files deleted as soon as they are used, and metrics that are counts and durations only. Patient results are never cached across users.

## 2. One safety policy (`ai/safety.py`)

Used by the assistant, the agent, report explanations, approved-knowledge search, speech and every engine capability. It (a) classifies a request - urgent, diagnosis, medicine advice, education - with the
urgent wording coming only from the approval-controlled policy; (b) reviews outputs: no unsupported diagnostic claim, no medicine start/stop/change recommendation, no certainty language, no emergency
instructions other than the policy message, experimental output always labelled; sentences that are verbatim quotations of the person's document or approved material are allowed and labelled as quotations;
(c) abstains with standard wording; (d) flags contradictory documents instead of choosing. The assistant, agent and knowledge search refuse the same requests with the same wording (`tests/test_ai_safety_policy.py`),
and every fixed sentence a patient can be shown is itself checked against the policy. A blocked output is **withheld** and replaced by an abstention, never edited into something that still looks approved.

## 3. Medical images (`ai/vision.py`, `imaging/quality.py`)

Recognising a category does not mean a validated model exists. `POST /api/v1/ai/images/analyze` checks quality first, then routes by category - face, hair/scalp, body, other, skin-lesion close-up (research-only),
photo-of-a-report (sent to the document pipeline *before* skin-photo rejection) - and runs only tasks that apply to the category and are available to the caller. Patients and clinicians reach approved, active
models only (none exist); research models run for an administrator who asks for research and names the model. When nothing may run the answer is an honest route to clinician review.

**Quality v2** applies a check as a *rejection* only if it was validated on real photographs (`docs/eval/quality_v2.json`; threshold fitted on the train split, false-flag rate ≤3% on val/test, detection ≥80% of
simulated faults at ≥20% coverage). Obstruction passed (false flags 0.3% val, 0% test; 100% detection at ≥20% coverage) and is applied **only to skin-lesion close-ups**. Centre-skin framing did **not** pass
(detection of a shifted subject 1.9%) and is advisory. Resolution (21-23% of close-ups are advisory-small) and compression are advisory. Document-like detection: 0 false positives on 2,271 skin photographs,
12/12 synthetic report photos detected. Lesion-style rules are not applied to faces, hair or scalp, and no rule for them was validated on real photographs; they give guidance, not findings.

## 4. Also in this phase

* **Documents**: spans and provenance for every extracted item, header facts, a medication parser, cited and verified explanations, grounded retrieval and prompt-injection defence
  (MEDICAL_DOCUMENT_INTELLIGENCE.md).
* **Agent**: typed tools, server-side permissions, confirmation for every change, provider gates (AI_AGENT_AND_TOOL_SECURITY.md).
* **Datasets and training**: licence gate, safe importer, resource-planned training, reusable task pipeline, cards (DATASET_AND_TRAINING.md).
* **Evaluation and release**: evidence classes, report formats, the model lifecycle with known-good fallback, rollback and clinical-issue handling (MODEL_EVALUATION_AND_RELEASE.md).
* **Voice and languages**: bounded speech recognition that cannot invent speech from silence, spoken text that matches the screen, per-language statements of what was and was not checked (VOICE_AND_MULTILINGUAL.md).
* **Screens** (minimal and honest): the Ask screen has an assistant-actions card where every change is first shown and waits for "Yes, do this" or "No, don't", and urgent, refused, abstained and error answers
  are labelled as such; the voice note passes on what the recogniser noticed (for example a language mismatch); the report screen finds its sections by `id`, shows printed details, medicines and approved
  background with the page each comes from, and says plainly when Hindi or Hinglish wording is unreviewed; the administrator screen shows each model's lifecycle state, asks for the verified clinician's user id
  when clinical sign-off is claimed, shows why an approval was refused, copes with a model that has never been evaluated, and offers evaluate, deploy, technical rollback, clinical issue and deprecate.
  Checked with `tsc`, `eslint`, `next build` and `e2e/phase3-ui.mjs` in a real browser (results in STATUS_REPORT.md section 00).

## 5. Endpoints added in Phase 3

`GET /api/v1/ai/{capabilities,health(admin),images/categories,speech/languages}`, `POST /api/v1/ai/{images/analyze,knowledge/search}`, `GET /api/v1/ai/documents/{id}/explanation`,
`POST /api/v1/agent/{chat,actions/{id}/confirm|cancel}`, `GET /api/v1/agent/{tools,actions,traces,providers(admin)}`, `POST /api/documents/{id}/request-review`,
`POST /api/clinic/medications/{id}/review` (doctor), and the administrator routes for training plans/runs/readiness, dataset cards, model lifecycle (`/admin/models/{id}/{lifecycle,evaluate,approval,deploy,deprecate,clinical-issue,exposure}`,
`/admin/tasks/{task}/{rollback,deactivate,monitor,readiness}`, `/admin/model-imports`). Migrations 0006 (training runs), 0007 (document intelligence), 0008 (agent), 0009 (model events) were written with row-level
security and tested on a disposable PostgreSQL 17; **they have not been applied to the real Supabase project** (the Phase 2 position is unchanged).

## 6. Configuration added in Phase 3

All optional; every default is the conservative one and a bad value never removes a bound (out-of-range values fall back to the default or are capped).

| Variable | Default | What it controls |
|---|---|---|
| `COSMO_AI_MAX_CONCURRENCY`, `COSMO_AI_MAX_QUEUE` | `min(2, CPU count)`, `8` | AI worker pool size and queue length; a full queue answers `resource_exhausted` |
| `COSMO_AI_MIN_FREE_MB`, `COSMO_AI_MODEL_CACHE` | `200`, `1` | memory that must stay free after a model loads; how many models stay loaded |
| `COSMO_AI_DEVICE` | `auto` | `auto` uses a GPU only when this kind of GPU has a passing verification record (`scripts/verify_gpu.py`), otherwise the CPU; `cpu`; `cuda`/`mps` are explicit requests, reported **unverified** without a record, and an absent device is an error, never a silent switch to the CPU. No GPU exists on this machine, so none of this has been exercised |
| `COSMO_STT_MAX_BYTES`, `COSMO_STT_MAX_SECONDS` | 10 MB, 60 s (caps 25 MB, 300 s) | recording limits for speech recognition |
| `COSMO_WHISPER_MODEL_DIR` | `data/models/stt/faster-whisper-base` | where the local recogniser lives (nothing is downloaded at request time) |
| `COSMO_AGENT_PROVIDER`, `COSMO_AGENT_TOOL_WORKERS`, `COSMO_AGENT_ACTION_TTL_S` | `rule_based`, `2`, `900` | which request classifier may run (an unapproved one falls back to the rules); tool worker pool; how long a proposed change waits for confirmation |
| `COSMO_ASSISTANT_MODE` | extractive | `llm` alone does **not** enable model-written answers; they also need passing evaluation gates and a recorded approval |
| `COSMO_EVAL_DIR` | `docs/eval` | where evaluation records are looked up when gates are checked |
| `COSMO_MODEL_AUTO_ROLLBACK` | unset (off) | `1` lets the monitor roll back by itself on a checksum failure or a clearly high failure rate |
| `COSMO_TRAIN_MAX_SECONDS`, `COSMO_TRAIN_RAM_FRACTION` | `7200`, `0.8` | longest planned training run; share of free memory a run may plan to use |
| `COSMO_OCR_LANGS`, `COSMO_TESSERACT_CMD`, `COSMO_DOC_MAX_SECONDS` | `eng`, auto-detected, `90` | OCR languages (only installed packs are used), the Tesseract binary, the time limit for reading one document |
| `COSMO_ALLOW_LOCAL_HTTP` | unset | lets the dataset downloader use plain local HTTP (otherwise blocked by the SSRF guard) |

## 7. Where to run the checks

`cd backend && .venv\Scripts\python -m pytest tests -q` (SQLite) and `COSMO_TEST_DB=postgres` for the PostgreSQL-backed suites (see STATUS_REPORT.md for the exact figures from the integration run). The evaluation
runners are listed in MODEL_EVALUATION_AND_RELEASE.md section 2. Secrets are scanned with `python backend/scripts/scan_secrets.py .` before every commit.

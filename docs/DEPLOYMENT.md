# Deployment guide (self-hosted)

Tested here: Windows 11, Python 3.11 venv, Node 24, **no containers**. Everything below marked *untested* has not been run.

## Prerequisites
* Python 3.11, Node 20+, Tesseract OCR 5 (`winget install UB-Mannheim.TesseractOCR`, `apt install tesseract-ocr`, `brew install tesseract`).
* CPU is enough for inference (OCR, embeddings, Whisper-base, image checks). Training the research models took ~20 min per run on 2 cores.
* Optional GPU: PyTorch picks CUDA / Apple MPS automatically (`trainer.pick_device`); **not tested here**.

## Backend
```
cd backend
py -3.11 -m venv .venv && .venv\Scripts\pip install -r requirements.txt
# models (explicit, licence-checked downloads; the app never downloads weights at request time)
.venv\Scripts\python -m scripts.fetch_models          # BGE-small (MIT) and faster-whisper-base (MIT); prints licence tags and commit hashes
set COSMO_SECRET=<long random value>                  # required outside local development
set COSMO_DEV_PASSWORD=<strong value>                 # demo accounts only; remove demo users in production
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```
`--workers 1`: the job queue, SQLite database and model caches are per host, so run exactly one backend instance. The rate limiter can use Redis (`COSMO_REDIS_URL`); a shared job queue is not implemented (not needed at one instance).

### Optional language model (off by default; evidence says the 1.5B model is not adequate)
```
pip install llama-cpp-python --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
# place a licensed GGUF in backend/data/models/llm/ then:
set COSMO_LLM_ENABLED=1 & set COSMO_ASSISTANT_MODE=llm
```
or point `COSMO_LLM_URL` at a self-hosted OpenAI-compatible server. Re-run `python -m eval.llm_eval --gguf <file>` and compare with `docs/eval/` before enabling.

## Frontend
```
cd frontend && npm install && npm run build && npm run start     # COSMO_API_URL=http://127.0.0.1:8000 (server-side proxy)
```
Terminate TLS in a reverse proxy (nginx/Caddy) and forward `/` to Next.js. The browser only ever talks to the Next.js origin.

## Health, readiness, metrics
`GET /api/v1/health/live` · `GET /api/v1/health/ready` (503 if database or private storage are down; optional components reported as `degraded`) ·
`GET /api/v1/metrics` (admin).

## Backup and restore
```
.venv\Scripts\python -m scripts.backup backup D:\backups
.venv\Scripts\python -m scripts.backup restore D:\backups\cosmo-backup-<stamp> D:\restore-target
```
Encrypt the backup volume. Test a restore before relying on it.

## Containers, TLS, monitoring, backups
`backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml`, `ops/` (Caddy, Prometheus, Alertmanager, systemd, Windows tasks) and `.github/workflows/ci.yml`.
Secrets are Docker secret files (`docs/SECRETS.md`); operations are in `docs/RUNBOOK.md`.

**Verification status (be exact about this).** Verified here: Dockerfiles pass `hadolint`; the CI workflow passes `actionlint`; the compose file passes structural tests (`tests/test_ops_config.py`);
Caddyfiles pass `caddy validate` and a live TLS run (internal CA); Prometheus rules pass `promtool check` and `promtool test rules`; Alertmanager config passes `amtool check-config`.
**Not verified:** no image was built and `docker compose up` was never run, because no container runtime exists on the development machine; the first CI run on GitHub has not happened.
Treat the container files as a reviewed starting point, not a tested release.

Frontend build note: `next.config.ts` bakes the `/api` proxy destination into the build, so the backend address is the build argument `COSMO_API_URL` (default `http://backend:8000`).
Models are never baked into images: run `docker compose run --rm backend python -m scripts.fetch_models` once (needs outbound access to Hugging Face) or mount a models volume.
Research-model serving needs PyTorch: build with `WITH_TORCH=1`.

## Release procedure for a model
Register (training run) → inspect `Release gates` on the model card → fix or reject → approval record (five requirements) → deploy → monitor → roll back with
`POST /api/admin/tasks/<task>/rollback`. Gates are evaluated by `training/trainer.py:evaluate_gates`.

## Additional production settings (added 2026-10-18)
| Variable | Meaning |
|---|---|
| `COSMO_REQUIRE_CONSENT=1` | Enforce the privacy-notice acceptance for patients (always on when `COSMO_MODE=production`) |
| `COSMO_ALLOW_NO_CONSENT=1` | Start in production without an approved notice only if consent is collected by another documented process |
| `COSMO_ALLOW_LEXICAL=1` | Accept the unvalidated lexical retrieval fallback when the dense embedding model is missing (otherwise production refuses to start) |
| `COSMO_KB_REVIEW_DAYS` | Re-review period for approved knowledge-base articles in days (default 365, 0 = none); lapsed articles are not used |
| `COSMO_CONTENT_DIR` | Directory holding `urgent_policy.approved.json` and `consent_notice.approved.json` (see `docs/clinical_review/APPROVAL_PROCEDURE.md`) |
Quotation validity is a clinic setting (days, default 30, 0 = never) in the clinic workspace.

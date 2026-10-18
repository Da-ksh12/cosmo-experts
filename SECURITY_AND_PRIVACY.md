# Security and Privacy

Scope: what is implemented and tested today, and what is still required before real patient data is processed. This is an engineering
document, not legal advice; HIPAA/GDPR/India DPDP (or other) obligations need qualified review for the intended jurisdiction and use.

## Principles enforced in code
* **No external AI services.** Photos, documents, audio and questions are processed locally. External downloads happen only for model/dataset acquisition by an administrator.
* **Data minimisation.** Photos rejected by the validator are not stored; async job inputs are deleted as soon as the job is terminal; raw audio is never retained (temp file removed after decoding).
* **No training on patient data.** Training reads only datasets that passed the licence gate. There is no code path from patient uploads to a training dataset; clinician corrections are stored separately and are not exported to training.
* **Server-side authorisation everywhere.** Patient data access checks owner or doctor/admin role; staff access to a patient's file/photo is audited (`document.staff_access`, `image.staff_access`).

## Controls (all covered by tests unless marked)
| Control | State |
|---|---|
| Authentication | Local HMAC-signed tokens (8 h), PBKDF2-SHA256 (100k) password hashes. Production should use Supabase Auth/JWT. Demo accounts share a dev password — **must be changed**. |
| Authorisation | Role dependencies per route; patients cannot reach staff/admin routes; admins cannot author or approve clinical plans; only doctors approve plans/quotes/KB. |
| Tenant isolation | Tests: documents, thumbnails, files, images, conversations, jobs, care plans, history, quotes, PDF. |
| Upload validation | Extension + magic bytes, size cap (15 MB), page cap (40), decode checks, encrypted-PDF rejection, image pixel cap; filenames sanitised; stored under generated names. |
| Archive safety | `safe_unzip` rejects path traversal and non-image/csv members. |
| Rate limiting | In-memory: login 8/min/IP, account flows 20/min/IP, submissions 40/min/token, general 600/min/IP, plus per-account sign-in lockout (tested). **Per process** — use a shared store/proxy limiter for multiple instances. |
| Gateway back-pressure | Queue and per-user limits with `429 + Retry-After` (tested). |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store` on API; frontend adds `Permissions-Policy`. |
| Request tracing | `X-Request-ID` on every response; stored on jobs and audit events. |
| Audit log | Logins, uploads, deletions, reviews, approvals, dataset/licence actions, training, model release, staff access. Sensitive content is not logged. |
| Secrets | `COSMO_SECRET` env var; no secrets in frontend code; the same-origin proxy hides the backend URL. |
| LLM prompt-injection | Retrieved text is untrusted: outputs pass the deterministic verifier; 0/3 injected "take 500 mg of iron" instructions reached the patient in the real-model test. Not a guarantee. |
| Research model route | `POST /api/v1/research/predict` is admin-only (tested: patient 403), audited as `research.predict`, verifies artifact + backbone checksums before loading, and never returns output to patients. |
| Backup / recovery | Phase 2: `scripts/pg_backup.py` takes encrypted (AES-256-GCM), snapshot-consistent PostgreSQL backups and restores only into a new empty database with row-count and content-digest verification; SQLite backups remain for development. Owner-chosen RPO/RTO. Not yet run against the real database. See `docs/BACKUP_AND_RECOVERY.md`. |
| Retention / deletion | Phase 2: operator-chosen periods per category (none built in), dry run by default, legal holds, bounded batches, file/row coordination, audit purge through a system-only function; patient erasure `POST /api/me/delete-my-data` honours holds. See `docs/DATA_RETENTION_AND_DELETION.md`. The legal retention schedule is still open. |
| Account lifecycle | Supabase sign-in with httpOnly rotating refresh cookie, lockout, generic failures, registration closed by default, reset with global sign-out, disabled accounts refused per request (`docs/SUPABASE_AUTH_AND_RLS.md`). |
| Row-level security | Forced RLS with a signed per-transaction identity on every application table, plus independent backend checks; cross-user tests at both layers. |
| Consent | Append-only history bound to the notice hash and approval status; withdrawal; never a consent to train (`docs/PRIVACY_AND_CONSENT.md`). |
| Private files | Opaque keys, atomic private writes, quotas, scanner interface that never says "clean" without a scanner, short-lived signed links (`docs/PRIVATE_FILE_STORAGE.md`). |
| API hardening | Request-size limits, concurrency bound, timeouts, explicit CORS, API security headers, safe error bodies, validation errors without echoed input, SSRF guard for data-derived URLs (`docs/PRODUCTION_CONFIGURATION.md`). |

## Gaps that block real-patient deployment
1. Real TLS deployment and HSTS verification, multi-factor authentication (not implemented).
2. The Phase 2 PostgreSQL schema and RLS applied to the real database (dry run succeeded; not applied: needs the owner connection string); until then data lives in SQLite, which has no encryption at rest.
3. A restore drill on the real database and off-machine backup storage; log shipping with PHI scrubbing.
4. A running malware scanner (interface done, no engine deployed) and sandboxing of document parsers (PDF/DOCX/image decoders are an attack surface; PyMuPDF is AGPL — licence review).
5. The approved privacy notice text, data-processing agreements, retention schedule, breach procedure, DPIA (legal; consent mechanism and retention tooling are built).
6. Penetration test; dependency vulnerability scanning in CI; secrets rotation.
7. Distributed rate limiting; abuse monitoring.
8. Formal access reviews for admin/clinician accounts and clinician credential verification workflow beyond the admin flag.

## Data flows
Browser → (same-origin proxy) → API → private storage/SQLite. Models and datasets: downloaded by an admin to `backend/data/`. Nothing leaves the server at inference time.

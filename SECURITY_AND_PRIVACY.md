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
| Rate limiting | In-memory: login 8/min/IP, submissions 40/min/token, general 600/min/IP (tested). **Per process** — use a shared store/proxy limiter for multiple instances. |
| Gateway back-pressure | Queue and per-user limits with `429 + Retry-After` (tested). |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store` on API; frontend adds `Permissions-Policy`. |
| Request tracing | `X-Request-ID` on every response; stored on jobs and audit events. |
| Audit log | Logins, uploads, deletions, reviews, approvals, dataset/licence actions, training, model release, staff access. Sensitive content is not logged. |
| Secrets | `COSMO_SECRET` env var; no secrets in frontend code; the same-origin proxy hides the backend URL. |
| LLM prompt-injection | Retrieved text is untrusted: outputs pass the deterministic verifier; 0/3 injected "take 500 mg of iron" instructions reached the patient in the real-model test. Not a guarantee. |
| Research model route | `POST /api/v1/research/predict` is admin-only (tested: patient 403), audited as `research.predict`, verifies artifact + backbone checksums before loading, and never returns output to patients. |
| Backup / recovery | `backend/scripts/backup.py`: consistent SQLite snapshot + uploads archive + sha256 manifest; restore verifies integrity and refuses to overwrite (tested, including tamper detection). Encryption of backups is **not** implemented — encrypt at rest externally. |
| Retention / deletion | Conversation retention purge (`/admin/retention/purge`, default 30 days); document and photo deletion; **patient erasure workflow** `POST /api/me/delete-my-data` removes uploads, extracted text, photos, conversations and jobs, and reports what is retained (care plans, quotations, audit) pending legal retention rules (tested). |

## Gaps that block real-patient deployment
1. TLS termination, HSTS, secure cookie/session design, MFA, account lockout beyond rate limiting.
2. Supabase (or equivalent) Auth/RLS/Storage actually applied; SQLite has no encryption at rest — use full-disk encryption or move to managed Postgres.
3. Encryption of backups; tested disaster-recovery drill; log shipping with PHI scrubbing.
4. Malware scanning and sandboxing of document parsers (PDF/DOCX/image decoders are an attack surface; PyMuPDF is AGPL — licence review).
5. Consent capture, privacy notice, data-processing agreements, retention schedule, breach procedure, DPIA.
6. Penetration test; dependency vulnerability scanning in CI; secrets rotation.
7. Distributed rate limiting; abuse monitoring.
8. Formal access reviews for admin/clinician accounts and clinician credential verification workflow beyond the admin flag.

## Data flows
Browser → (same-origin proxy) → API → private storage/SQLite. Models and datasets: downloaded by an admin to `backend/data/`. Nothing leaves the server at inference time.

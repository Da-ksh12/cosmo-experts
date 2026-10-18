# Phase 2: production data architecture, security and privacy

Evidence date: 2026-10-18. This is an engineering record of what was built, where it is, and how to run and verify it. **It does not claim the product is production-ready, legally compliant or clinically approved.** Each capability is classified in [STATUS_REPORT.md](STATUS_REPORT.md) as implemented and verified, awaiting infrastructure, blocked by credentials, blocked by legal/specialist approval, not implemented, or not applicable.

## What changed, by batch

| Batch | Commit | What it delivers |
|---|---|---|
| 1 Configuration | `01ada5b` | Typed, validated, redacted settings (`app/settings.py`); production refuses to start on unsafe or missing settings; structured JSON logs with correlation ids and a redaction filter (`app/redact.py`, `app/logging_setup.py`); `.env.example` with placeholders only |
| 2 PostgreSQL | `01ada5b`, `2bc9c5e` | Database façade with SQLite (local/test) and PostgreSQL (psycopg3 pool) backends (`app/db.py`, `app/dbcore/`); ordered, checksummed migrations (`backend/migrations/postgres/`, `scripts/migrate.py`); SQLite to PostgreSQL copy tool (`scripts/sqlite_to_postgres.py`) |
| 3 Account lifecycle | `0d3e8d7`, `23cea48` | Supabase sign-in, refresh rotation, sign-out, registration, password reset, lockout, disabled accounts, administrator account actions (`app/auth_flows.py`, `app/gotrue.py`, `app/routers/auth.py`); Next.js sign-in, register, forgot/reset password screens |
| 4 Authorization and RLS | `2bc9c5e`, IDOR commit | Row-level security on every application table with a signed per-transaction identity; backend authorization that does not depend on it; cross-user tests at both layers |
| 5 Consent | `23cea48` | Append-only consent history bound to the notice hash and approval status; withdrawal; visible state (`/privacy`); consent never covers model training |
| 6 Private storage | `03006c7` | Scanner interface (never "clean" without a scanner, fail closed), per-account quotas, atomic private writes, short-lived signed links |
| 7 Retention | `65d3041` | Operator-chosen retention periods only, dry run by default, legal holds, bounded batches, file/row coordination, audit purge through a system-only function |
| 8 Backups and operations | `68040d9` | Encrypted PostgreSQL backup with restore verification into an empty database; request limits, concurrency bound, timeouts, explicit CORS, security headers, safe errors, SSRF guard |
| 9 Verification and documents | this commit | Integration verification, `docker-compose.yml` for the new requirements, these documents |

## Architecture in one page

```
Browser ──(same-origin /api proxy)──> FastAPI
                                       │  RequestLimits (size, concurrency, timeout)  ← app/limits.py
                                       │  Guard (request id, rate limits, headers)    ← app/middleware.py
                                       │  security.current_user  → verified Supabase JWT → role from public.profiles → disabled check
                                       │  dbcore.context: (user, role) for this request
                                       ▼
                               services / routers (authorization checks in code)
                                       ▼
                               app/db.py façade ── PostgresBackend: pooled connection, per transaction:
                                                    set_config(app.user_id, app.role, app.admin_clinical, app.ctx_sig = HMAC(secret, identity))
                                                    └─ PostgreSQL row-level security (FORCE, default deny) evaluates app_private.cur_user()/cur_role()
                               └─ SqliteBackend (development and tests only; production refuses it)
```

* **Two enforcement layers.** The backend checks ownership and role in code (`security.can_access_patient_data`, `require(...)`). PostgreSQL row-level security independently denies rows the identity may not see. Neither relies on the other: tests exercise each (`tests/test_authz_idor.py` for the API layer, `tests/test_pg_rls.py` for the database layer, including forged identities).
* **Application schema `app`.** Backend-owned tables with text ids, `timestamptz` times and JSON text columns. The earlier Supabase `public.*` tables (see `supabase/migrations/`) are legacy to this layer: `public.profiles` is still the trusted source of a user's role in Supabase mode. See [DATABASE_MIGRATION.md](DATABASE_MIGRATION.md).
* **No per-clinician patient assignment exists in the product**, so any verified clinician can read any patient's clinical record. That is how the product worked before Phase 2; it is documented here rather than changed. Administrators are kept out of clinical records unless `COSMO_ADMIN_CLINICAL_ACCESS=read` is chosen.

## Where things are

| Concern | Files |
|---|---|
| Settings, redaction, logging | `backend/app/settings.py`, `redact.py`, `logging_setup.py`, `prod_checks.py` |
| Database layer | `backend/app/db.py`, `app/dbcore/{sqlite_backend,pg_backend,dialect,context,types,migrate}.py` |
| Migrations | `backend/migrations/postgres/0001`..`0005`, `scripts/migrate.py`, `scripts/sqlite_to_postgres.py` |
| Authentication | `app/auth_flows.py`, `gotrue.py`, `routers/auth.py`, `supabase_auth.py`, `security.py`; frontend `app/login`, `register`, `forgot-password`, `reset-password`, `lib/api.ts` |
| Consent | `app/consent.py`, `routers/patient.py` (`/api/consent*`), frontend `app/privacy`, `components/ConsentGate.tsx` |
| Storage | `app/storage.py`, `malware.py`, `filelinks.py`, `routers/files.py` |
| Retention | `app/retention.py`, `scripts/retention.py`, `routers/staff.py` (`/api/admin/retention/*`, `/api/admin/legal-holds*`) |
| Backup | `scripts/backup.py` (SQLite, encryption primitives), `scripts/pg_backup.py` |
| Operational security | `app/limits.py`, `netguard.py`, `middleware.py`, `main.py` (CORS, error handlers) |

## How to verify

```
cd backend
.venv\Scripts\python -m pytest tests -q                                  # SQLite mode (default)
set COSMO_TEST_DB=postgres && .venv\Scripts\python -m pytest tests\test_pg_rls.py ...   # a disposable PostgreSQL on 127.0.0.1 is created and removed
cd ..\frontend && npx tsc --noEmit && npx eslint app components lib && npx next build
python backend\scripts\scan_secrets.py .
```

PostgreSQL tests need PostgreSQL binaries (`COSMO_PG_BIN` or `F:\pgtools\pg\pgsql\bin`). Without them they **skip with an explicit reason**; they are never silently counted as passed. The exact results of the final run are in [STATUS_REPORT.md](STATUS_REPORT.md).

## Decisions that need the owner

1. `COSMO_ADMIN_CLINICAL_ACCESS` (`none` recommended, or `read`).
2. Retention periods per category (legal decision), see [DATA_RETENTION_AND_DELETION.md](DATA_RETENTION_AND_DELETION.md).
3. A malware scanner (ClamAV `clamd`) or a conscious `COSMO_ALLOW_UNSCANNED_UPLOADS=1`, see [PRIVATE_FILE_STORAGE.md](PRIVATE_FILE_STORAGE.md).
4. RPO and RTO values and where encrypted backups are stored off the machine, see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).
5. The approved privacy notice text (legal), see [PRIVACY_AND_CONSENT.md](PRIVACY_AND_CONSENT.md).
6. Open or keep closed public registration (`COSMO_ALLOW_REGISTRATION`), and the Supabase Auth settings in [SUPABASE_AUTH_AND_RLS.md](SUPABASE_AUTH_AND_RLS.md).

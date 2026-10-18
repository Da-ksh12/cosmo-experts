# Database and migration

## Backends

| `COSMO_DB` | Use | Notes |
|---|---|---|
| `sqlite` (default outside production) | Local development and most tests | Production **refuses** it unless `COSMO_ALLOW_SQLITE_IN_PRODUCTION=1` is set deliberately for a single-node deployment. There is never a silent fall-back from PostgreSQL to SQLite. |
| `postgres` | Production | psycopg 3 with a connection pool (`COSMO_DB_POOL_MIN/MAX`), connect and statement timeouts, bounded retries only for connection-level failures on reads and connection acquisition (a write is never retried after it began). No session state, so a transaction-mode pooler is fine. |

The application connects as the restricted role `cosmo_app` (no `BYPASSRLS`, DML grants only). Schema changes use a separate **owner** credential that the running application never sees.

## Schema

`backend/migrations/postgres/NNNN_name.sql`, applied in order, forward-only:

| File | Purpose |
|---|---|
| `0001_app_schema.sql` | 20 application tables in schema `app` (mirroring the SQLite columns), `timestamptz`, CHECK constraints, indexes, grants; the audit table is append-only for the application role |
| `0002_row_level_security.sql` | Schema `app_private` (identity verification), RLS enabled and **forced** on every table, default deny |
| `0003_consent_events.sql` | Append-only consent history |
| `0004_upload_scan_and_size.sql` | `scan_status` on documents and images, image size for quotas |
| `0005_legal_holds_and_retention.sql` | Legal holds; `app.purge_audit()` callable by the system identity only |

Rules enforced by `app/dbcore/migrate.py`: contiguous versions, SHA-256 checksum of every applied file stored in `app.schema_migrations`, **editing an applied migration is detected and refused** (add a new one), one transaction per migration with its record (a failure rolls back to the previous version), an advisory lock against two runners, and detection of unknown applied versions.

```
set COSMO_MIGRATION_DATABASE_URL=postgresql://OWNER:...@HOST:5432/DB?sslmode=require     (or ..._FILE)
python -m scripts.migrate status
python -m scripts.migrate apply [--target N] [--dry-run]
python -m scripts.migrate verify
```

The application checks the version at start-up (`COSMO_MIGRATION_MODE=check`, the production default) and refuses to run against a database that is behind or has drifted. `apply` at start-up needs `COSMO_ALLOW_STARTUP_MIGRATIONS=1` and is not recommended.

### Roles and the identity secret (one-time, by the database owner)

1. `cosmo_app` is created `NOLOGIN` by migration 0002. The owner gives it a login and a password: `ALTER ROLE cosmo_app LOGIN PASSWORD '...'` (not done by this project; no credential is created for you).
2. The owner role needs `BYPASSRLS` (a superuser locally). On some hosted databases that cannot be granted: then use the provider's tooling, or decide consciously with the provider; migrations and backups need to read all rows.
3. Install the identity secret once: it must equal `COSMO_DB_CONTEXT_SECRET` (at least 32 random characters). `app_private.set_ctx_secret()` is owner-only and refuses to overwrite silently; the application verifies at start-up that its secret matches the database's.

## SQLite to PostgreSQL copy (development data)

```
python -m scripts.migrate apply                      # target must be migrated first
python -m scripts.sqlite_to_postgres --sqlite backend/data/cosmo.sqlite3 --dry-run
python -m scripts.sqlite_to_postgres --sqlite backend/data/cosmo.sqlite3 [--on-conflict skip|update|fail] [--orphans fail|skip] [--batch 500] [--progress progress.json]
```

Read-only on the source (never modified or deleted); each batch commits in its own transaction and the progress file lets an interrupted run resume; idempotent; foreign-key orphans stop the run unless `--orphans skip` (counted); validation compares counts, a hash of all primary keys and a hash of critical fields; output is table names and counts only. Defects found and fixed while testing it: batches were not durable (the connection was not in autocommit), and children of skipped orphan documents were not counted in validation.

## Verification performed

* Disposable PostgreSQL 17 on 127.0.0.1: clean provisioning, ordering, checksum drift, failed-migration rollback, concurrent runners, the table inventory (`tests/test_pg_backend.py`, `test_pg_rls.py`, `test_sqlite_to_pg.py`).
* **Real Supabase project (PostgreSQL 17.11), dry run:** all five migrations were sent in one request that ended in a deliberate exception, so nothing persisted. Result: success up to the final check (22 tables, 47 policies, 9 functions in schemas `app`/`app_private`); a follow-up read-only query confirmed zero schemas and zero `cosmo_app` role left behind. **The migrations have not been applied to the live project**: that needs the owner connection string, which the management token cannot provide.

## Cut-over checklist (not yet done)

1. Take a Supabase backup; decide a maintenance window.
2. Create a staging database, apply migrations, run `sqlite_to_postgres` on a copy of the data, run the test suite with `COSMO_TEST_DB=postgres`.
3. In production: `migrate apply`, give `cosmo_app` a login, install the identity secret, set `COSMO_DB=postgres` and the secrets, start with `COSMO_MIGRATION_MODE=check`.
4. Keep the SQLite file untouched until the retention decision for it is made.

## Recovery from a wrong migration

Fix forward with a new migration, or restore a verified backup into a **new** database ([BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md)). Never edit or delete an applied migration file.

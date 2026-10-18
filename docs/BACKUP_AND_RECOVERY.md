# Backup and recovery

Backups contain patient data. Everything below assumes the encryption key is held in a secret manager and **never beside the backups**; losing the key makes every backup unrecoverable.

## What is backed up

| Data | Covered by | Notes |
|---|---|---|
| Application database (PostgreSQL schema `app`) | `scripts/pg_backup.py` | Data-only `pg_dump` (custom format) from one exported snapshot; the schema is rebuilt from the versioned migrations on restore |
| Local private uploads | `scripts/pg_backup.py` (when `COSMO_STORAGE=local`) | Transient job inputs and partial files are skipped |
| SQLite development database | `scripts/backup.py` | Development only |
| Files in Supabase Storage, Supabase Auth users, `public.profiles` | **Not covered** | Protected only by the Supabase project's own backup plan, which has not been confirmed from here; check the plan, retention and point-in-time-recovery options in the dashboard |
| The identity secret (`COSMO_DB_CONTEXT_SECRET`), keys, `.env` | **Deliberately excluded** | Held in your secret manager. A restored database has no identity secret and denies every request until the operator installs one |

## Taking a backup

```
set COSMO_BACKUP_KEY=<base64 32 bytes>             (python -m scripts.backup genkey; or COSMO_BACKUP_KEY_FILE)
set COSMO_BACKUP_DATABASE_URL=postgresql://BACKUP_ROLE:...@HOST:5432/DB?sslmode=require    (or ..._FILE; falls back to COSMO_MIGRATION_DATABASE_URL)
python -m scripts.pg_backup backup D:\backups
python -m scripts.pg_backup status
```

The backup role must read every row despite row-level security (the owner role with `BYPASSRLS`). Credentials reach `pg_dump` through `PG*` environment variables, never on a command line. The result is `cosmo-backup-<stamp>.cbk` (AES-256-GCM, 1 MiB chunks, authenticated header, chunk index and last-chunk flag, so tampering, truncation, reordering and a wrong key are all detected) plus a small `.cbk.json` (hash, size, key id; no secrets). The plaintext working copy is removed even when the run fails. Pruning keeps every backup newer than `COSMO_BACKUP_KEEP_DAYS` (7), one per ISO week for `COSMO_BACKUP_KEEP_WEEKS` (4), and always the newest; **nothing is pruned except by this rule, and the rule must be aligned with the retention schedule** (deleted patient data persists in older backups until they rotate out). `docker-compose.yml` runs this daily in the `backup` service.

The manifest records the applied migration versions and checksums, a row count and a content digest per table (computed inside the same snapshot as the dump), and a fingerprint of the source.

## Restoring, and proving the restore

```
python -m scripts.pg_backup restore D:\backups\cosmo-backup-<stamp>.cbk postgresql://OWNER:...@localhost:5432/NEW_EMPTY_DB [--files-target D:\restored-uploads]
```

Safety rules enforced by the tool and its tests (`tests/test_pg_backup.py`, 14 tests against a real PostgreSQL):

* **The target must be a new database with no `app` schema.** A live database (or the source itself) is refused before anything is touched, so a restore can never overwrite production.
* A non-local host needs `--allow-remote-target` (for a disposable database you control).
* The wrong key, a flipped byte or a truncated file are refused before any database change.
* The backup's migration checksums must equal this checkout's, otherwise the restore stops ("restore with the code version the backup was taken from").
* After loading, the restore **verifies**: migration records, then per table the row count and the content digest must equal the manifest. Any difference fails the restore. Output is counts and pass/fail only.
* Uploads go to an empty directory only, with path-traversal protection.

After a restore the new database has no identity secret: install `COSMO_DB_CONTEXT_SECRET` (`app_private.set_ctx_secret`), give `cosmo_app` a login, then point the application at it. Test the whole drill on a schedule, not only once.

## RPO and RTO are yours to choose

The system does not assume them. Set `COSMO_BACKUP_RPO_MINUTES` and `COSMO_BACKUP_RTO_MINUTES`; `pg_backup status` reports the age of the last successful backup against the RPO (and says "no RPO chosen" when unset), `restore` reports the measured duration against the RTO, and the existing Prometheus gauge `cosmo_backup_last_success_timestamp_seconds` feeds alerting. A daily backup means an RPO of up to a day; shorter needs more frequent runs or the provider's point-in-time recovery.

## Verified here, and what was not

Verified with the disposable PostgreSQL 17: encrypted round trip with microsecond timestamps preserved, row-level security still forced after restore, consistent snapshot while rows change, refusal cases, content-digest verification catching a changed or deleted row, no credentials in process arguments or error text, upload restore and traversal refusal, RPO/RTO reporting.

**Not verified:** a backup of the **real** Supabase database (needs the owner connection string, and a host that permits `pg_dump` with `BYPASSRLS`-equivalent access); restore at production data volumes and the resulting RTO; off-machine storage of the `.cbk` files (the destination is your choice); the Supabase-managed backups and point-in-time recovery; Supabase Storage object backup (not implemented).

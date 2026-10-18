# Data retention and deletion

## Principle: no invented legal periods

How long a clinic may or must keep conversations, clinical documents, photos, quotations, care plans and audit entries is a legal and clinical-governance decision. **The software ships with no retention period for any category.** Until the operator sets one, that category is kept and reported as "not configured". (An earlier 30-day default for conversations was removed for this reason; the old variable `COSMO_CONV_RETENTION_DAYS` still works as the setting for conversations.)

## Configuration

`COSMO_RETENTION_<CATEGORY>_DAYS`, a whole number from 1 to 36500; anything else is a start-of-run error (never silently ignored):

| Category | Removes | Notes |
|---|---|---|
| `CONVERSATIONS` | conversations and their messages | |
| `NOTIFICATIONS` | notifications | |
| `JOBS` | background-job records and any leftover input file inside the private upload directory | |
| `DOCUMENTS` | the stored file, extracted pages and fields; the row is kept as a deleted skeleton | clinical record: decide with the clinical lead |
| `IMAGES` | the stored file; the row is kept as a deleted skeleton | |
| `QUOTES` | quotations | financial record |
| `CARE_PLANS` | care plans | clinical record |
| `AUDIT` | audit entries older than the period, through `app.purge_audit()` | skipped entirely while any legal hold is active |

Consent history and legal-hold history are never purged by this framework.

## Running it

```
python -m scripts.retention plan                                   # counts per category; changes nothing
python -m scripts.retention run                                    # same: a dry run is the default
python -m scripts.retention run --execute --confirm DELETE         # delete in bounded batches (--batch 100 --max-batches 20 --budget 60)
python -m scripts.retention run --execute --confirm DELETE --loop 3600    # repeat; supervise with your service manager
python -m scripts.retention orphans [--delete]                     # local files no row references (older than 24 h)
```

Administrators have the same through the API: `GET /api/admin/retention/plan`; `POST /api/admin/retention/purge` is a dry run unless the body is `{"dry_run": false, "confirm": "DELETE"}`. **Neither the application nor the tests ever start a purge on real data**; the tests run against disposable databases.

## Behaviour (all covered by `tests/test_retention.py`, 23 tests, run on SQLite and PostgreSQL)

* **Bounded:** batches of at most 1000 rows, a limit on batches and on wall-clock time per run; a run that stops early says so and the next run continues.
* **Idempotent and resumable:** a second run finds nothing left; a crash between steps leaves rows that the next run picks up.
* **Database and files together:** the stored file is removed first with strict error handling (a file already gone counts as done; any other failure keeps the row and counts it as failed, to be retried), then the rows change in one transaction. Supabase Storage objects use the same strict delete.
* **Legal holds:** a patient under an active hold is excluded from every category, and from erasure on request (the patient is told only "we cannot delete your data right now; please contact the clinic"; the deferral is audited). Holds use fixed reason codes (`legal_proceeding`, `regulatory_inquiry`, `clinical_safety_review`, `other`), so no narrative is stored. Place and release: `POST /api/admin/legal-holds`, `POST /api/admin/legal-holds/{id}/release`, `GET /api/admin/legal-holds` (administrators only). Holds are never deleted.
* **Minimal audit:** one entry per category per run with counts only; the CLI prints counts only.
* **Audit purge:** the application role cannot delete audit rows. In PostgreSQL the worker calls `app.purge_audit(cutoff, max_rows)`, a security-definer function that works only for the system identity and only in batches.

## Erasure on request

`POST /api/me/delete-my-data` removes what the patient controls (documents with files, text and fields, photos, conversations and messages, jobs, notifications) and reports what remains: care plans, quotations and the audit trail. Whether those must be kept, and for how long, is the open legal question above.

## Not done

* The retention schedule itself (legal).
* Hard deletion of the "deleted skeleton" rows and of soft-deleted records (a policy decision).
* Reconciliation of **Supabase Storage** orphans (listing objects with no row); only local storage is reconciled.
* Deletion of the person's Supabase Auth identity on erasure (an account-closure policy decision).
* Backups still hold deleted data until they are rotated out; the backup retention (`COSMO_BACKUP_KEEP_*`) must be consistent with the retention schedule, see [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md).

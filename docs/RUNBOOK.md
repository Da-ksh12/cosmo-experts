# Operations runbook

Audience: whoever operates a Cosmo Experts deployment. Everything marked **verified** was executed in the development environment (Windows 11, no container runtime);
anything else is untested and listed under "Not verified" at the end.

## Backups
* Daily, encrypted (AES-256-GCM), pruned (7 daily + 4 weekly, newest always kept): `python -m scripts.backup backup-encrypted <dest>`.
  Scheduling: `ops/systemd/cosmo-backup.timer`, `ops/windows/register-tasks.ps1`, or the `backup` service in `docker-compose.yml`.
* Key: `python -m scripts.backup genkey`. Store it in the secret manager **and** in a second, separate offline location. Without the key every backup is unrecoverable; the key is never kept next to the backups.
* Result of each run is written to `<data dir>/backup_status.json` and exported as `cosmo_backup_*` metrics; alerts: `CosmoBackupStale` (>26 h), `CosmoLastBackupFailed`.
* A backup stored on the same host/volume as the data does not protect against losing the host. Copy the `.cbk` files off-site (they are safe to store with a third party because they are encrypted).
* Scope: the SQLite database and locally stored uploads. Files in Supabase Storage and Supabase Auth users are covered by the Supabase project's own backup plan (confirm the plan and retention in the Supabase dashboard; not verified here).

## Restore drill (run quarterly and after any change to the backup code or key)
1. `python -m scripts.backup restore-encrypted <file.cbk> <empty target dir>` (refuses a non-empty target, wrong key, or any tampering).
2. Check the printed `integrity_check` is `ok` and the row counts are plausible.
3. Start a **separate** instance with `COSMO_DATA_DIR=<target>` and run `python -m scripts.healthcheck --url ... --data-dir <target>`.
4. Record date, backup file, duration and outcome below.

**Drill executed 2026-10-17 on the development data** (`docs/runs/restore_drill_dev_data.json`): backup 0.64 s (1.5 MB database to a 400 KB encrypted file), restore 0.11 s, `integrity_check` ok,
18 tables with identical row counts, 2/2 upload files byte-identical (SHA-256), restore with a wrong key refused with nothing left on disk, restore of a file with one flipped bit refused.
Limits of that drill: tiny dataset (so the timings say nothing about production RTO), same machine, synthetic/development data, key held in memory for the drill only.

## Alerts
Two independent paths so a single failure does not silence alerting:
* Prometheus + Alertmanager (`ops/prometheus/`, `docker compose --profile monitoring up`): 13 rules, **syntax and logic verified** with `promtool check rules` and `promtool test rules`; webhook delivery to a chat channel not verified.
* `scripts/healthcheck.py` every 5 minutes (`ops/systemd/cosmo-healthcheck.timer`): liveness, readiness, queue, backup freshness, disk, with a de-duplicated webhook. Delivery verified against a local test receiver.

## TLS
`ops/Caddyfile` obtains and renews certificates for `COSMO_DOMAIN` (ACME/Let's Encrypt) and redirects HTTP to HTTPS. **Verified locally** with Caddy's internal CA (`docs/runs/tls_local_check.json`):
TLS 1.3, HSTS, security headers, HTTP→HTTPS 301, `/metrics` not reachable through the proxy. **Not verified:** issuance of a public certificate (needs a real domain with DNS and ports 80/443 reachable).

## Rate limiting behind the proxy
The backend must run with `--proxy-headers --forwarded-allow-ips=<proxy network>` (the Dockerfile does) or every client appears as the proxy and the per-IP login limit becomes global.
With several backend instances set `COSMO_REDIS_URL` so limits are shared; if Redis fails the instances fall back to per-process limiting and `CosmoRateLimitStoreFailing` fires.

## Scaling limits (read before adding instances)
The job queue, model caches and the SQLite database are per host. **Run exactly one backend instance** until the application data moves to a shared database and the queue to a broker.
Only the rate limiter has a shared-store implementation. This is a decision, not an oversight: shared queue storage is not needed at single-instance scale and is not built.

## Secrets rotation
See `docs/SECRETS.md`.

## Not verified
Container builds and `docker compose up` (no container runtime available), the GitHub Actions run itself (workflow linted with `actionlint`, not executed), a Redis server (tested with an in-memory fake that implements the same commands),
public-certificate issuance, webhook delivery to a real chat service, Windows/systemd scheduled-task registration, restore time at production data volumes, and any penetration test.

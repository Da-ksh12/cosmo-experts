# Secrets handling

| Secret | Purpose | Where it lives |
|---|---|---|
| `COSMO_SECRET` | signs local-mode session tokens (production uses Supabase tokens, but the value is still required and checked) | secret manager → file → `COSMO_SECRET_FILE` |
| `SUPABASE_SERVICE_ROLE_KEY` | server-side Auth profile lookup and Storage; bypasses row-level security, so **server only** | `SUPABASE_SERVICE_ROLE_KEY_FILE` |
| `COSMO_BACKUP_KEY` | AES-256 key for backups | `COSMO_BACKUP_KEY_FILE`; also stored offline separately |
| `COSMO_METRICS_TOKEN` | bearer token for `/metrics` | `COSMO_METRICS_TOKEN_FILE` |
| `COSMO_REDIS_URL` | shared rate-limit store (may contain a password) | `COSMO_REDIS_URL_FILE` |
| Alert webhook URL | chat alerts | `secrets/alert_webhook_url` (Alertmanager `url_file`) |

Rules enforced by code and tests: only the allow-listed names above can be loaded from `NAME_FILE`; a missing or empty file stops startup; an explicit environment variable wins over the file;
in `COSMO_MODE=production` the process refuses to start with a default or short secret, local demo accounts, wildcard/plain-HTTP CORS, disabled rate limits, or no backup key (`app/prod_checks.py`).
`backend/scripts/scan_secrets.py` runs in CI and fails on credential-shaped text in the tree; the repository was scanned clean on 2026-10-17. `.gitignore` excludes `.env`, `secrets/` and `*.cbk`.

Never put the Supabase **management** access token (`sbp_…`) on a server. It administers the whole Supabase account, is only for migrations from a developer machine, and should be short-lived.
The access token used during development of this project was pasted into a chat transcript and **must be revoked** (Supabase dashboard → Account → Access Tokens).

## Rotation
* `COSMO_SECRET`: replace the file, restart (local-mode sessions end).
* Service-role key: rotate in the Supabase dashboard, update the file, restart the backend (cached profile lookups last ≤ 30 s).
* Backup key: generate a new key, take a new backup with it, keep the old key until the last backup made with it has expired from retention. Each backup records a non-secret key id in its header and sidecar so you can tell which key a file needs.
* Metrics token: replace the file in the backend and Prometheus containers, restart both.

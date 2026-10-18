# Production configuration

Every setting is an environment variable. Secrets may be supplied as files (`NAME_FILE=/run/secrets/...`); only an allow-list of names is honoured (`app/secrets_env.py`). `.env.example` contains placeholders only. **Start-up refuses to run in production (`COSMO_MODE=production`) when a rule below is violated**; the message names the setting and never prints its value. `GET`-style inspection of the effective configuration is redacted (`Settings.describe()`: whether a secret is set, and only host and database name of the database URL).

## Required in production

| Variable | Rule |
|---|---|
| `COSMO_SECRET` | at least 32 characters; signs local tokens and signed file links |
| `COSMO_AUTH=supabase`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | demo accounts are refused; the anon key is public by design, the service-role key is a secret and backend-only |
| `COSMO_DB=postgres`, `COSMO_DATABASE_URL` | `postgresql://`, TLS required for a remote host (`sslmode=require` or `verify-full`); the role is `cosmo_app` |
| `COSMO_DB_CONTEXT_SECRET` | at least 32 characters; must equal the value installed in the database |
| `COSMO_MIGRATION_MODE` | `check` (default); `off` is refused; `apply` needs `COSMO_ALLOW_STARTUP_MIGRATIONS=1` |
| `COSMO_COOKIE_SECURE=1` | the refresh cookie is sent over HTTPS only |
| `COSMO_CORS` | explicit `https://` origins; `*` and plain http are refused |
| `COSMO_ADMIN_CLINICAL_ACCESS` | `none` (recommended) or `read`; there is no default in production |
| `COSMO_STORAGE=supabase` (or `COSMO_ALLOW_LOCAL_STORAGE=1`) | files outside the application host |
| `COSMO_BACKUP_KEY` | base64 of 32 random bytes |
| `COSMO_SCANNER=clamd` or `COSMO_ALLOW_UNSCANNED_UPLOADS=1` | see [PRIVATE_FILE_STORAGE.md](PRIVATE_FILE_STORAGE.md) |
| An approved privacy notice or `COSMO_ALLOW_NO_CONSENT=1` | see [PRIVACY_AND_CONSENT.md](PRIVACY_AND_CONSENT.md) |
| Rate limiting on, dense retrieval model present (or `COSMO_ALLOW_LEXICAL=1`) | earlier phases |

Do not set `COSMO_ALLOW_SQLITE_IN_PRODUCTION=1` unless a single-node SQLite deployment is a conscious decision.

## Optional, with the defaults

| Variable | Default | Meaning |
|---|---|---|
| `COSMO_DB_POOL_MIN` / `MAX` | 1 / 10 | connection pool |
| `COSMO_DB_CONNECT_TIMEOUT` / `COSMO_DB_STATEMENT_TIMEOUT_MS` | 10 s / 30000 | |
| `COSMO_ALLOW_REGISTRATION` | closed | `1` opens public patient self-registration |
| `COSMO_COOKIE_SAMESITE` | `strict` | |
| `COSMO_MAX_UPLOAD_MB` / `COSMO_MAX_JSON_KB` | 15 / 1024 | request-body limits |
| `COSMO_MAX_CONCURRENT_REQUESTS` | 64 | beyond this, `503` with `Retry-After` |
| `COSMO_REQUEST_TIMEOUT_S` | 120 | `504` after this; long work belongs on `/api/v1` jobs |
| `COSMO_USER_STORAGE_MB` / `COSMO_USER_FILE_LIMIT` | 200 / 500 | per-account quotas |
| `COSMO_RETENTION_<CATEGORY>_DAYS` | unset = keep | see [DATA_RETENTION_AND_DELETION.md](DATA_RETENTION_AND_DELETION.md) |
| `COSMO_BACKUP_RPO_MINUTES` / `COSMO_BACKUP_RTO_MINUTES` | unset | owner-chosen targets |
| `COSMO_BACKUP_KEEP_DAYS` / `KEEP_WEEKS` | 7 / 4 | backup pruning |
| `COSMO_REDIS_URL` | in-process | shared rate-limit store for several instances |
| `COSMO_STRUCTURED_LOGS` | on | JSON logs with request ids (`0` disables) |

## Network and platform

* Terminate TLS at the reverse proxy (`ops/Caddyfile`); run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy>` (the Dockerfile does) so per-IP limits see real clients. Disable the access log or keep it behind the redaction filter: signed-link tokens appear in paths.
* **Restrict egress** from the backend container to the database, Supabase and (if used) the scanner and LLM hosts. The SSRF guard (`app/netguard.py`) covers dataset downloads and terms pages but cannot defeat DNS rebinding on its own.
* API responses carry `Content-Security-Policy: default-src 'none'`, `Permissions-Policy`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, `Cross-Origin-Resource-Policy: same-site`, `Cache-Control: no-store`, and HSTS in production. The web app sets its own page headers in `frontend/next.config.ts` and Caddy; a page-level Content-Security-Policy for the Next.js app is **not** configured (needs nonce handling for its inline scripts).
* `docker-compose.yml` was updated for this phase (database URL secrets, `migrate` and `backup` services). It was **never run** (no container runtime in the development environment).

## Pre-go-live checklist (all open)

- [ ] Owner connection string and `cosmo_app` login created; identity secret installed; `scripts.migrate apply` run on the real project
- [ ] SQLite data copied (if any) and validated
- [ ] Supabase dashboard review ([SUPABASE_AUTH_AND_RLS.md](SUPABASE_AUTH_AND_RLS.md))
- [ ] Approved privacy notice installed; retention periods decided and configured
- [ ] Scanner running or unscanned uploads accepted in writing
- [ ] Backup destination off the machine, RPO/RTO chosen, a restore drill performed on the real database
- [ ] Penetration test or independent security review; clinical and legal sign-offs listed in `STATUS_REPORT.md`

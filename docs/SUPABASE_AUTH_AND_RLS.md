# Supabase authentication, account lifecycle and row-level security

## Modes

`COSMO_AUTH=local` is for development and tests only (seeded demo accounts, PBKDF2 hashes in the application database). `COSMO_AUTH=supabase` is the production mode. In production the demo accounts and local sign-in do not exist (`/api/auth/login` delegates to Supabase and ignores local accounts).

## Account lifecycle (Supabase mode)

| Flow | Endpoint | Behaviour |
|---|---|---|
| Config for the sign-in screen | `GET /api/auth/config` | `{mode, registration, password_min}` |
| Sign in | `POST /api/auth/login` (`user_id` carries the e-mail) | Access token in the response body; the **refresh token only in an httpOnly cookie** `cosmo_rt` (Secure per `COSMO_COOKIE_SECURE`, SameSite per `COSMO_COOKIE_SAMESITE`, path `/api/auth`). Role comes from `public.profiles`, never from token claims. |
| Refresh | `POST /api/auth/refresh` | Needs header `X-Requested-With: cosmo-web` and a trusted `Origin` if one is sent; rotates the cookie; a replayed (already used) token fails and clears the cookie; a disabled account's new session is revoked. |
| Sign out | `POST /api/auth/logout` | Revokes at Supabase, clears the cookie; always succeeds locally. |
| Register | `POST /api/auth/register` | **Closed unless `COSMO_ALLOW_REGISTRATION=1`.** Creates a patient only: the role is never accepted from the client (only the name is sent as metadata; the database trigger assigns the default role). Same 202 answer for a new address, an existing one and a provider error. Limits: 5/hour per IP, 3/hour per address. |
| Password reset | `POST /api/auth/password-reset/request`, `/confirm` | Same answer for unknown or malformed addresses; redirect targets restricted to the configured origins; the new password is applied with the recovery token and **all sessions are revoked**. |
| Administrator actions | `/api/admin/users...` | Create (confirmed), change role (`public.profiles` through the service role), disable (Supabase ban + application flag), reset password. Lock-out guards remain. |

Other controls: generic failure text (no account enumeration); lockout after 5 failures in 15 minutes per address hash (a success clears it, the locked attempt never reaches the provider); password policy of 12 to 128 characters plus a few obvious refusals (no composition rules); provider outage gives 503, provider rate limit gives 429; addresses are never stored in the audit trail (a 16-character hash tag only); a **disabled account is refused on every request**, so disabling takes effect before the access token expires.

The service-role key is read only by the backend and is never sent to the browser. The browser holds only the access token (in `sessionStorage`).

### Supabase dashboard settings the owner must review (not changed by this work)

Minimum password length and leaked-password protection, e-mail confirmation required, the SMTP provider (the built-in sender is rate-limited and not for production), redirect URLs allowed list (must include your reset page), and whether open sign-ups are enabled. No live sign-up or recovery was run against the real project, so no e-mail was sent.

## Row-level security

* Enabled **and forced** on every table in schema `app`; an absent policy means deny. The application role has no `BYPASSRLS`.
* Identity per transaction: the backend sets `app.user_id`, `app.role`, `app.admin_clinical` and `app.ctx_sig` = HMAC-SHA256 of those values with `COSMO_DB_CONTEXT_SECRET`. The functions in `app_private` use the values only if the signature verifies; otherwise the role is `none` and nothing is visible. The secret lives in a table readable only by the owner. A statement that forges or omits the identity sees nothing.
* Roles: `patient` (own rows), `doctor` (clinical records of all patients, no private conversations), `admin` (accounts, ML/dataset domain; clinical records only with `COSMO_ADMIN_CLINICAL_ACCESS=read`), `system` (server jobs and start-up, never reachable from a request).
* The audit table is append-only for the application role (no UPDATE/DELETE/TRUNCATE); old entries leave only through `app.purge_audit()` for the system identity ([DATA_RETENTION_AND_DELETION.md](DATA_RETENTION_AND_DELETION.md)).
* Background jobs and threads carry the initiating user's identity (`dbcore.context.spawn` and the job pool), or run explicitly as system.

## Tests

* `tests/fake_supabase.py` is a protocol-level fake of Supabase Auth with real ES256 keys and JWKS, so the production verification path runs; **no e-mail is sent and no real project is touched**. `tests/test_auth_lifecycle.py` (33 tests) covers every flow above including hostile role claims inside a token, replayed refresh tokens, lockout, provider outage, redaction of addresses and secrets in logs and audit.
* `tests/test_authz_idor.py` (53 tests): every endpoint that takes a resource id, against another patient, clinician, administrator, unauthenticated caller, disabled account and forged headers. A mutation check (disabling `can_access_patient_data`) makes these tests fail, confirming they bite.
* `tests/test_pg_rls.py` (41 tests): default deny, per-table policies, forged identity contexts, audit append-only, legal holds, the audit-purge function.

## Not verified here

Sign-up, confirmation and recovery against the **real** Supabase project (would send e-mail; no SMTP decision), refresh behaviour across a real browser session over HTTPS, and the Supabase dashboard settings above.

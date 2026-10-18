# Privacy and consent

## The notice is legal content that this project does not write

`backend/content/consent_notice.draft.md` is a **draft**. A notice becomes active only when `content/consent_notice.approved.json` exists with a reviewer, role, registration number, date, statement and a SHA-256 that matches the text (`scripts/make_approved_notice.py` builds it and refuses drafts). In production only an approved notice can be accepted; start-up refuses to run without one unless `COSMO_ALLOW_NO_CONSENT=1` records that consent is collected by another documented process. **Who approves the text, and what it must say for your jurisdiction, is for qualified legal counsel; that approval has not happened.**

## Consent history

Every decision is an event in `consent_events` (migration 0003): user, `notice_sha256`, `notice_status` (`approved` or `draft`), `action` (`accepted` or `withdrawn`), time, source. The table is **append-only** (SQLite trigger blocks updates; PostgreSQL grants the application role only SELECT and INSERT, with row-level security limiting each person to their own rows). Two properties follow:

* **Unapproved text can never be recorded as approved.** `notice_status` is decided on the server from the notice that was actually offered; a value in the request is ignored, and the column only accepts `approved` or `draft` (`tests/test_consent.py`).
* **Changing the notice text asks everyone again**, because the current state is "the latest event is an acceptance of the notice that is current now".

## Enforcement

Patient endpoints that collect or process personal data (upload a document or photo, start or continue a conversation, transcribe audio, request a quotation, and the asynchronous equivalents) require current consent (`consent.patient_consented`) and answer `403 consent_required` otherwise. Reading one's own account, history, documents and the consent screens is never blocked, so a person can always see and manage what exists. Enforcement is on in production and with `COSMO_REQUIRE_CONSENT=1`.

## Withdrawal

`POST /api/consent/withdraw` (patient only, idempotent) records a `withdrawn` event. It takes effect at once: no new uploads, recordings or messages. It does **not** delete what was already stored: deletion is the separate erasure request (`POST /api/me/delete-my-data`), which removes what the patient controls and keeps care plans, quotations and the audit trail as record-keeping, reporting exactly what remains ([DATA_RETENTION_AND_DELETION.md](DATA_RETENTION_AND_DELETION.md)). The words shown to patients about what withdrawal means are in the UI; the legal wording belongs to the approved notice.

## Visible state

`/privacy` in the web app shows whether the current notice is accepted, withdrawn or not yet accepted, the person's own history (notice fingerprint, draft marker, time) and a withdraw button. `GET /api/consent` returns the same state; `GET /api/consent/history` returns the person's own events (never the notice text, never another person's events).

## No consent to train

Consent in this system covers handling a patient's information to provide the service. It is **never** treated as permission to train or fine-tune models: the state includes `not_covered: ["model_training"]`, the UI says patient information is not used for training, and no training or dataset path reads patient uploads. Patient corrections are only eligible for a training set if an administrator marks them so under the dataset licence gate, which is a separate, auditable decision.

## Data minimisation and logs

Rejected photos are not stored; raw audio is never retained; logs carry request ids, route templates, status and timing, not request bodies, patient text, headers or file contents; `app/redact.py` masks secrets, tokens, connection strings, e-mail addresses and signed-link tokens; validation errors return field names only (the submitted values are not echoed); 500 responses carry no exception text.

## Not done / not decided

The approved notice text; the lawful basis and DPIA; regulatory classification (whether the product is regulated as medical software); parental/guardian consent for minors; cross-border transfer terms with the hosting provider; the breach procedure. These are listed in `SECURITY_AND_PRIVACY.md` and remain open.

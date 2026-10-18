# Private file storage

## Backends

`COSMO_STORAGE=local` (private directory, development) or `supabase` (private buckets `patient-documents` and `patient-images`, service-role key used server-side only; the key is never sent to a browser). A file is identified in the database by a reference string; reads and deletes dispatch on the reference, so a deployment can switch backends without orphaning old files.

## Keys and writes

* **Opaque keys:** `<owner>/<random 128-bit hex>.<ext>`. The original file name is stored only as display text in the database and never forms part of a path, bucket key or URL. References that point outside the private directory are refused for read and delete.
* **Local writes are atomic and private:** a hidden `.part` file created exclusively with mode 0600, `fsync`, then renamed; a failure removes the partial file. The directory is 0700 where the filesystem supports it. Temporary files for audio decoding use `mkstemp` and are removed in `finally`.
* **Row and file stay together:** ingestion writes the file, then the row in one transaction; if the transaction fails the file is deleted.

## Malware scanner interface (`app/malware.py`)

| State | Meaning |
|---|---|
| `clean` | a configured scanner examined the bytes and reported nothing |
| `unscanned` | **no scanner is configured**: nothing is claimed about the file |
| infected / error | never stored: the upload is refused |

* `COSMO_SCANNER=none` (default) or `clamd` (ClamAV INSTREAM over TCP `COSMO_CLAMD_HOST/PORT` or a socket `COSMO_CLAMD_SOCKET`; `COSMO_SCAN_TIMEOUT`).
* **The interface never records `clean` without a scanner.** A scanner that is configured but unreachable, slow, or returns an unusable reply **fails closed** (`503`, "we could not check this file just now"); an infected file is refused (`422`) and the signature name is not shown to the user.
* The scan runs **before any parser** touches the bytes.
* Production refuses to start with no scanner unless `COSMO_ALLOW_UNSCANNED_UPLOADS=1`; with that flag files are stored and marked `unscanned`, visibly different from `clean`. Outside production unscanned uploads are accepted so development works.
* **Awaiting infrastructure:** no ClamAV server exists in this environment, so the real scanner was never exercised; the protocol is tested against a local fake that speaks `INSTREAM` (clean, infected, error, garbage, unreachable, slow). The deployment owner must run `clamd`, keep signatures updated and decide whether its detection quality suffices. The existing parser hardening (`documents/safety.py`: active content, embedded files, zip bombs, entity definitions, path traversal, page and pixel limits) stays and is covered by `tests/test_upload_safety.py`.

## Validation and bounds

Size limit `COSMO_MAX_UPLOAD_MB` (default 15) enforced while reading and again by the request-size middleware (limit plus 1 MiB multipart overhead); content sniffing decides the type, not the extension; PDF page count and image pixel limits. **Quotas** per account: `COSMO_USER_STORAGE_MB` (default 200) and `COSMO_USER_FILE_LIMIT` (default 500); exceeding either gives `413` before anything is stored; deleting files frees room. These are technical protections, not retention rules.

## Signed links

`POST /api/documents/{id}/link` and `POST /api/images/{id}/link` issue a link **only after the normal authorization check**; it lives 60 seconds (never more than 300), names the file by database id (no path, bucket or key appears in it), and is bound to the requesting account. `GET /api/files/{token}` re-checks the signature and expiry, that the account still exists and is not disabled, that the file still exists, and that the account may still read it. Deleting a file or disabling an account therefore kills outstanding links. Responses are `attachment`, `no-store`, `nosniff`, with a sandbox CSP. Link tokens are redacted from logs. The web app currently reads files through authorized API calls and does not need the links; they exist for hand-off to a download or an external viewer.

## Tests

`tests/test_private_storage.py` (19), `tests/test_storage.py`, `tests/test_upload_safety.py`. Real Supabase Storage tests exist in `test_storage.py` and run only with project credentials.

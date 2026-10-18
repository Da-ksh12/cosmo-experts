"""Private file storage for patient uploads: local disk or Supabase Storage, selected by COSMO_STORAGE (local | supabase).

A stored file is identified by a *reference string* kept in the database:
    local:     an absolute file path (the original format, so existing rows keep working)
    supabase:  supabase://<bucket>/<owner-uuid>/<file>
Reads and deletes dispatch on the reference, so deployments can switch backends without orphaning old files.
Supabase access uses the SERVICE ROLE key server-side only; clients never receive it. Buckets are private with size/mime limits (migration 0002);
the folder name is the owner's auth uid, which is what the storage RLS policy checks for direct reads."""
from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .config import UPLOAD_DIR

PREFIX = "supabase://"
IMAGE_EXT = {"jpg", "jpeg", "png"}
MIME = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "txt": "text/plain",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class StorageError(Exception):
    pass


def _safe_owner(owner: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]", "", owner)
    if not s:
        raise StorageError("invalid owner")
    return s


def backend_name() -> str:
    return os.environ.get("COSMO_STORAGE", "local")


# ---------------------------------------------------------------- local
def _local_put(owner: str, data: bytes, ext: str) -> str:
    """Opaque random name (the original file name is never part of the key), private permissions, and an atomic publish: the bytes are written to a hidden
    partial file that is fsynced and renamed, so a crash or a full disk never leaves a truncated file under a real reference."""
    d = UPLOAD_DIR / _safe_owner(owner)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass                                                                                   # not supported on every filesystem (Windows ACLs apply there)
    name = f"{uuid.uuid4().hex}.{re.sub(r'[^a-z0-9]', '', ext.lower())}"
    final, part = d / name, d / f".{name}.part"
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, final)
    except OSError:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageError("the file could not be stored") from None
    return str(final)


def _local_path(ref: str) -> Path:
    p = Path(ref).resolve()
    if UPLOAD_DIR.resolve() not in p.parents:             # a reference can never point outside the private upload directory
        raise StorageError("reference outside storage")
    return p


# ---------------------------------------------------------------- supabase
def _sb() -> tuple[str, str]:
    url, key = os.environ.get("SUPABASE_URL", "").rstrip("/"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise StorageError("Supabase storage is not configured")
    return url, key


def _bucket_for(ext: str) -> str:
    return "patient-images" if ext.lower() in IMAGE_EXT else "patient-documents"


def _sb_request(method: str, path: str, body: bytes | None = None, ctype: str | None = None, timeout=60) -> bytes:
    url, key = _sb()
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(f"{url}/storage/v1/{path}", data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - operator-configured project URL
            return r.read()
    except urllib.error.HTTPError as e:
        raise StorageError(f"storage request failed (HTTP {e.code})") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise StorageError("storage is unreachable") from None


def _sb_parts(ref: str) -> tuple[str, str]:
    rest = ref[len(PREFIX):]
    bucket, _, key = rest.partition("/")
    if bucket not in ("patient-images", "patient-documents") or not key or ".." in key.split("/"):
        raise StorageError("invalid storage reference")
    return bucket, key


def _sb_put(owner: str, data: bytes, ext: str) -> str:
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())
    bucket, key = _bucket_for(ext), f"{owner}/{uuid.uuid4().hex}.{ext}"
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", owner):
        raise StorageError("Supabase storage requires a Supabase user id as the owner")
    _sb_request("POST", f"object/{bucket}/{urllib.parse.quote(key)}", data, MIME.get(ext, "application/octet-stream"))
    return f"{PREFIX}{bucket}/{key}"


# ---------------------------------------------------------------- public API
def put(owner: str, data: bytes, ext: str) -> str:
    return _sb_put(owner, data, ext) if backend_name() == "supabase" else _local_put(owner, data, ext)


def read(ref: str) -> bytes:
    if ref.startswith(PREFIX):
        bucket, key = _sb_parts(ref)
        # A unique query string forces the request to the origin: Supabase's CDN edge caches `object/authenticated/...` GETs (even with
        # `Cache-Control: no-store`), so without it a file read before deletion stays retrievable from the edge after the object is erased.
        return _sb_request("GET", f"object/authenticated/{bucket}/{urllib.parse.quote(key)}?cb={uuid.uuid4().hex}")
    try:
        return _local_path(ref).read_bytes()
    except OSError:
        raise StorageError("the stored file is no longer available") from None


def delete(ref: str) -> None:
    if not ref:
        return
    if ref.startswith(PREFIX):
        bucket, key = _sb_parts(ref)
        try:
            _sb_request("DELETE", f"object/{bucket}/{urllib.parse.quote(key)}")
        except StorageError:
            pass                                                # already gone
        return
    try:
        _local_path(ref).unlink(missing_ok=True)
    except StorageError:
        pass


def healthy() -> tuple[bool, str]:
    if backend_name() == "supabase":
        try:
            _sb_request("GET", "bucket/patient-documents", timeout=10)
            return True, "Supabase Storage reachable (private bucket present)"
        except StorageError as e:
            return False, str(e)
    p = UPLOAD_DIR / ".w"
    p.write_text("x"); p.unlink()
    return True, "private upload directory writable"


# ---------------------------------------------------------------- quotas
def limits() -> tuple[int, int]:
    """(bytes, files) one account may hold. Technical limits to protect the service; the clinic can change them (COSMO_USER_STORAGE_MB, COSMO_USER_FILE_LIMIT)."""
    try:
        mb = int(os.environ.get("COSMO_USER_STORAGE_MB", "200"))
        files = int(os.environ.get("COSMO_USER_FILE_LIMIT", "500"))
    except ValueError:
        raise SystemExit("COSMO_USER_STORAGE_MB and COSMO_USER_FILE_LIMIT must be whole numbers") from None
    return max(mb, 1) * 1024 * 1024, max(files, 1)


def usage(owner_id: str) -> dict:
    from . import db
    d = db.q1("SELECT COALESCE(SUM(size_bytes),0) b, COUNT(*) n FROM documents WHERE owner_id=? AND deleted=0 AND stored_path!=''", (owner_id,))
    i = db.q1("SELECT COALESCE(SUM(size_bytes),0) b, COUNT(*) n FROM images WHERE owner_id=? AND deleted=0 AND stored_path!=''", (owner_id,))
    return {"bytes": int(d["b"]) + int(i["b"]), "files": int(d["n"]) + int(i["n"])}


def check_quota(owner_id: str, incoming: int) -> None:
    """Raises malware.UploadRefused (413) if storing `incoming` bytes would pass the account's limits."""
    from .malware import UploadRefused
    max_bytes, max_files = limits()
    u = usage(owner_id)
    if u["files"] + 1 > max_files or u["bytes"] + incoming > max_bytes:
        raise UploadRefused("You have reached the storage limit for your account. Please delete files you no longer need and try again.", 413)

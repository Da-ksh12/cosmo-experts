"""Backup and restore for the local deployment.

Plaintext (development only):
    python -m scripts.backup backup <dest_dir>
    python -m scripts.backup restore <backup_dir> <target_data_dir>
A backup is a consistent SQLite snapshot (online backup API), a zip of private uploads and model artifacts' manifest, and a
manifest.json with sha256 of every file. Restore verifies the hashes first and refuses to overwrite a non-empty target.

Backups contain patient data. Scheduled production backups use the ENCRYPTED commands:
    python -m scripts.backup genkey                                  (prints a new base64 32-byte key; keep it in a secret manager)
    python -m scripts.backup backup-encrypted <dest_dir>             (key: COSMO_BACKUP_KEY; also prunes: COSMO_BACKUP_KEEP_DAYS=7, COSMO_BACKUP_KEEP_WEEKS=4)
    python -m scripts.backup restore-encrypted <file.cbk> <target_data_dir>
    python -m scripts.backup prune <dest_dir>
Format: AES-256-GCM, 1 MiB chunks, per-chunk nonce; header, chunk index and a last-chunk flag are authenticated, so tampering, truncation, reordering and
a wrong key are all detected. The key is never stored beside the backups; losing it makes every backup unrecoverable. This covers application data only:
files held in Supabase Storage and Supabase Auth users are protected by Supabase's own backups and are not copied by this script."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def backup(data_dir: Path, dest: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = dest / f"cosmo-backup-{stamp}"
    out.mkdir(parents=True)
    src = sqlite3.connect(data_dir / "cosmo.sqlite3")
    dst = sqlite3.connect(out / "cosmo.sqlite3")
    with dst:
        src.backup(dst)                      # consistent even while the app is writing
    src.close(); dst.close()
    uploads = data_dir / "private_uploads"
    with zipfile.ZipFile(out / "private_uploads.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(uploads.rglob("*")) if uploads.exists() else []:
            if p.is_file() and "_jobs" not in p.parts:       # transient job inputs are not backed up
                z.write(p, p.relative_to(uploads).as_posix())
    models = [{"path": p.relative_to(data_dir).as_posix(), "sha256": sha256(p)} for p in sorted((data_dir / "models").rglob("*.pt"))] if (data_dir / "models").exists() else []
    manifest = {"created": stamp, "files": {f.name: sha256(f) for f in out.iterdir() if f.is_file()}, "model_artifacts": models,
                "note": "Dataset files are re-downloadable from their official sources and are not included; model .pt artifacts are listed with hashes."}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


def restore(backup_dir: Path, target: Path) -> dict:
    m = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in m["files"].items():
        if sha256(backup_dir / name) != digest:
            raise SystemExit(f"Integrity check failed for {name}; refusing to restore.")
    if target.exists() and any(target.iterdir()):
        raise SystemExit("Target directory is not empty; refusing to overwrite.")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_dir / "cosmo.sqlite3", target / "cosmo.sqlite3")
    with zipfile.ZipFile(backup_dir / "private_uploads.zip") as z:
        root = (target / "private_uploads").resolve()
        for info in z.infolist():
            t = (root / info.filename).resolve()
            if root not in t.parents:
                raise SystemExit(f"Unsafe path in archive: {info.filename}")
        z.extractall(root)
    c = sqlite3.connect(target / "cosmo.sqlite3")
    ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("users", "documents", "images", "care_plans", "audit")}
    c.close()
    return {"integrity_check": ok, "counts": counts}


# ---------------------------------------------------------------- encryption
MAGIC = b"COSMOBK1"
CHUNK = 1 << 20
HEADER_LEN = 8 + 8 + 4 + 4


class BackupError(Exception):
    pass


def load_key(value: str | None = None) -> bytes:
    raw = value if value is not None else os.environ.get("COSMO_BACKUP_KEY", "")
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError:
        raise BackupError("COSMO_BACKUP_KEY is not valid base64") from None
    if len(key) != 32:
        raise BackupError("COSMO_BACKUP_KEY must decode to exactly 32 bytes (python -m scripts.backup genkey)")
    return key


def key_id(key: bytes) -> bytes:
    return hashlib.sha256(b"cosmo-backup-key-id" + key).digest()[:8]


def _nonce(prefix: bytes, i: int) -> bytes:
    return prefix + struct.pack(">Q", i)


def encrypt_file(src: Path, dst: Path, key: bytes) -> None:
    prefix = os.urandom(4)
    header = MAGIC + key_id(key) + prefix + struct.pack(">I", CHUNK)
    aes = AESGCM(key)
    with open(src, "rb") as f, open(dst, "wb") as out:
        out.write(header)
        i, cur = 0, f.read(CHUNK)
        while True:
            nxt = f.read(CHUNK)
            last = not nxt
            ct = aes.encrypt(_nonce(prefix, i), cur, header + struct.pack(">QB", i, 1 if last else 0))
            out.write(struct.pack(">I", len(ct)) + ct)
            if last:
                break
            i, cur = i + 1, nxt


def decrypt_file(src: Path, dst: Path, key: bytes) -> None:
    aes = AESGCM(key)
    with open(src, "rb") as f, open(dst, "wb") as out:
        header = f.read(HEADER_LEN)
        if len(header) != HEADER_LEN or header[:8] != MAGIC:
            raise BackupError("not a Cosmo encrypted backup")
        if header[8:16] != key_id(key):
            raise BackupError("wrong key: this backup was encrypted with a different key")
        prefix = header[16:20]

        def read_chunk():
            ln = f.read(4)
            if not ln:
                return None
            if len(ln) != 4:
                raise BackupError("backup is truncated or corrupt")
            (n,) = struct.unpack(">I", ln)
            if n < 16 or n > CHUNK + 16:
                raise BackupError("backup is truncated or corrupt")
            ct = f.read(n)
            if len(ct) != n:
                raise BackupError("backup is truncated or corrupt")
            return ct

        cur, i = read_chunk(), 0
        if cur is None:
            raise BackupError("backup is truncated or corrupt")
        while cur is not None:
            nxt = read_chunk()
            try:
                out.write(aes.decrypt(_nonce(prefix, i), cur, header + struct.pack(">QB", i, 1 if nxt is None else 0)))
            except InvalidTag:
                raise BackupError("backup failed authentication: it was modified, truncated, reordered or the key is wrong") from None
            cur, i = nxt, i + 1


# ---------------------------------------------------------------- encrypted, retained, monitored backups
def _write_status(data_dir: Path, **kw):
    p = data_dir / "backup_status.json"
    try:
        old = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        old = {}
    old.update(kw)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(old, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def backup_encrypted(data_dir: Path, dest: Path, key: bytes) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    started = time.time()
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=dest))
    try:
        plain_dir = backup(data_dir, work)
        stamp = plain_dir.name.removeprefix("cosmo-backup-")
        archive = work / "archive.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(plain_dir.iterdir()):
                z.write(p, p.name)
        final = dest / f"cosmo-backup-{stamp}.cbk"
        part = final.with_suffix(".cbk.part")
        encrypt_file(archive, part, key)
        os.replace(part, final)
        final.with_suffix(".cbk.json").write_text(json.dumps({"created": stamp, "bytes": final.stat().st_size, "sha256": sha256(final), "key_id": key_id(key).hex(),
                                                              "format": "COSMOBK1 AES-256-GCM", "seconds": round(time.time() - started, 2)}, indent=2), encoding="utf-8")
        _write_status(data_dir, last_ok=True, last_success_ts=time.time(), last_file=final.name, last_bytes=final.stat().st_size, last_error="")
        return final
    except Exception as e:
        _write_status(data_dir, last_ok=False, last_error=f"{type(e).__name__}: {str(e)[:200]}", last_attempt_ts=time.time())
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)          # the plaintext working copy never outlives the run


def restore_encrypted(cbk: Path, target: Path, key: bytes) -> dict:
    parent = target.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".restore-", dir=parent))
    try:
        archive = work / "archive.zip"
        decrypt_file(cbk, archive, key)
        bdir = work / "backup"
        bdir.mkdir()
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if Path(info.filename).name != info.filename:
                    raise BackupError(f"unsafe path in archive: {info.filename}")
            z.extractall(bdir)
        return restore(bdir, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)


_STAMP = re.compile(r"^cosmo-backup-(\d{8}-\d{6})\.cbk$")


def prune(dest: Path, keep_days: int = 7, keep_weeks: int = 4, now: datetime | None = None) -> dict:
    """Keep every backup newer than keep_days, plus the newest backup of each of the previous keep_weeks ISO weeks, plus always the newest overall."""
    now = now or datetime.now()
    found = sorted(((datetime.strptime(m.group(1), "%Y%m%d-%H%M%S"), p) for p in dest.glob("*.cbk") if (m := _STAMP.match(p.name))), reverse=True)
    keep: set[Path] = set()
    if found:
        keep.add(found[0][1])
    weeks: dict[tuple, Path] = {}
    for ts, p in found:                                          # newest first
        if ts >= now - timedelta(days=keep_days):
            keep.add(p)
        elif ts >= now - timedelta(days=keep_days + 7 * keep_weeks):
            weeks.setdefault(tuple(ts.isocalendar()[:2]), p)
    keep |= set(weeks.values())
    removed = []
    for _, p in found:
        if p not in keep:
            p.unlink()
            p.with_suffix(".cbk.json").unlink(missing_ok=True)
            removed.append(p.name)
    return {"kept": sorted(p.name for p in keep), "removed": sorted(removed)}


def main(argv: list[str]) -> int:
    from app.config import DATA_DIR
    cmd = argv[1] if len(argv) > 1 else ""
    days, weeks = int(os.environ.get("COSMO_BACKUP_KEEP_DAYS", "7")), int(os.environ.get("COSMO_BACKUP_KEEP_WEEKS", "4"))
    if cmd == "genkey":
        print(base64.b64encode(os.urandom(32)).decode())
    elif cmd == "backup-encrypted" and len(argv) >= 3:
        out = backup_encrypted(DATA_DIR, Path(argv[2]), load_key())
        print(json.dumps({"backup": out.name, "bytes": out.stat().st_size, "pruned": prune(Path(argv[2]), days, weeks)["removed"]}))
    elif cmd == "restore-encrypted" and len(argv) >= 4:
        print(json.dumps(restore_encrypted(Path(argv[2]), Path(argv[3]), load_key())))
    elif cmd == "prune" and len(argv) >= 3:
        print(json.dumps(prune(Path(argv[2]), days, weeks)))
    elif cmd == "backup" and len(argv) >= 3:
        print(backup(DATA_DIR, Path(argv[2])), "(PLAINTEXT: use backup-encrypted for anything stored off this machine)")
    elif cmd == "restore" and len(argv) >= 4:
        print(restore(Path(argv[2]), Path(argv[3])))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except BackupError as e:
        print(f"backup error: {e}", file=sys.stderr)
        sys.exit(1)

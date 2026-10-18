"""Encrypted PostgreSQL backups and disposable-environment restore verification.

    python -m scripts.pg_backup backup <dest_dir>                       # needs COSMO_BACKUP_KEY and COSMO_BACKUP_DATABASE_URL (or COSMO_MIGRATION_DATABASE_URL)
    python -m scripts.pg_backup restore <file.cbk> <target_database_url> [--files-target DIR] [--allow-remote-target]
    python -m scripts.pg_backup status                                  # last backup age against the owner-chosen RPO
    (prune / genkey are shared with scripts.backup)

What a backup contains: a consistent pg_dump (custom format) of the DATA in schema `app` taken from one exported snapshot, a manifest with the applied migration
versions and checksums, per-table row counts and content digests, and (for local storage) the private uploads. The key material of the row-level-security layer
(schema app_private) is NOT dumped: it is installed from your secret manager, never from a backup. The schema itself is rebuilt from the versioned migrations on restore.
The archive is encrypted with AES-256-GCM (shared with scripts.backup); the key lives in your secret manager, never beside the backups.

Restore is only ever into a NEW, EMPTY database: it refuses any target that already has the `app` schema (so a live database can never be overwritten), refuses non-local
hosts unless --allow-remote-target is given, applies exactly the migration versions the backup was taken at (checksums must match this checkout), loads the data and then
VERIFIES it: migration records, per-table row counts and a content digest per table must equal the manifest. Output is counts and pass/fail only.
The backup role must be able to read every row despite row-level security (the owner role with BYPASSRLS, as for migrations). Credentials are passed to the PostgreSQL tools
through environment variables, never on a command line. RPO and RTO are numbers the owner chooses (COSMO_BACKUP_RPO_MINUTES, COSMO_BACKUP_RTO_MINUTES); this tool measures
against them and does not invent them. Supabase-hosted databases: use this against a connection the project permits, or the platform's own backups; see docs/BACKUP_AND_RECOVERY.md."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dbcore import migrate  # noqa: E402
from app.redact import redact, safe_exc  # noqa: E402
from scripts import backup as sbk  # noqa: E402
from scripts.backup import BackupError  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
CANDIDATE_DIRS = [os.environ.get("COSMO_PG_BIN") or "", r"F:\pgtools\pg\pgsql\bin", "/usr/lib/postgresql/17/bin", "/usr/lib/postgresql/16/bin"]


def tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for d in CANDIDATE_DIRS:
        if d and (Path(d) / exe).is_file():
            return str(Path(d) / exe)
    found = shutil.which(name)
    if not found:
        raise BackupError(f"{name} was not found: install the PostgreSQL client tools or set COSMO_PG_BIN")
    return found


def conn_env(url: str) -> dict:
    """PG* environment for the client tools, so no credential appears in a process listing."""
    u = urlparse(url)
    env = dict(os.environ)
    for k in [k for k in env if k.startswith("PG")]:
        del env[k]
    env.update({"PGHOST": u.hostname or "", "PGPORT": str(u.port or 5432), "PGDATABASE": unquote(u.path.lstrip("/")), "PGCONNECT_TIMEOUT": "15"})
    if u.username:
        env["PGUSER"] = unquote(u.username)
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    q = dict(p.split("=", 1) for p in (u.query or "").split("&") if "=" in p)
    if q.get("sslmode"):
        env["PGSSLMODE"] = q["sslmode"]
    return env


def run_tool(args: list[str], env: dict, what: str, timeout: int = 3600) -> None:
    r = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        tail = redact(r.stderr.decode("utf-8", "replace")[-400:])
        raise BackupError(f"{what} failed (exit {r.returncode}): {tail}")


def fingerprint(url: str) -> str:
    u = urlparse(url)
    return hashlib.sha256(f"{u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}".encode()).hexdigest()[:16]


def table_fingerprint(conn) -> dict:
    """Row count and content digest per application table. Rows are hashed as text in UTC, ordered by that text, so the digest is independent of physical order and of the
    server time zone. No row content leaves the database."""
    conn.execute("set time zone 'UTC'")
    tables = [r[0] for r in conn.execute("select table_name from information_schema.tables where table_schema='app' and table_type='BASE TABLE' and table_name<>'schema_migrations' order by 1")]
    out = {}
    for t in tables:
        n, d = conn.execute(f'select count(*), md5(coalesce(string_agg(x::text, \',\' order by x::text), \'\')) from app."{t}" x').fetchone()
        out[t] = {"rows": n, "digest": d}
    return out


def _dir_zip(src: Path, dest: Path) -> int:
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")) if src.exists() else []:
            if p.is_file() and "_jobs" not in p.parts and not p.name.endswith(".part"):
                z.write(p, p.relative_to(src).as_posix())
                n += 1
    return n


def backup(url: str, dest: Path, key: bytes, uploads_dir: Path | None = None, status_dir: Path | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    started = time.time()
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=dest))
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        with psycopg.connect(url, connect_timeout=15) as snap:                         # the snapshot stays valid while this transaction is open
            snap.execute("begin isolation level repeatable read read only")
            snapshot_id = snap.execute("select pg_export_snapshot()").fetchone()[0]
            applied = [{"version": r[0], "name": r[1], "checksum": r[2]} for r in snap.execute("select version, name, checksum from app.schema_migrations order by version")]
            if not applied:
                raise BackupError("the database has no applied migrations: refusing to back up an unknown schema")
            tables = table_fingerprint(snap)
            dump = work / "data.pgdump"
            run_tool([tool("pg_dump"), "--format=custom", "--data-only", "--schema=app", "--exclude-table=app.schema_migrations", "--no-owner", "--no-privileges",
                      f"--snapshot={snapshot_id}", f"--file={dump}"], conn_env(url), "pg_dump")
            snap.execute("rollback")
        files = 0
        if uploads_dir is not None and uploads_dir.exists():
            files = _dir_zip(uploads_dir, work / "private_uploads.zip")
        manifest = {"kind": "postgres", "created": stamp, "migrations": applied, "tables": tables, "source_fingerprint": fingerprint(url), "upload_files": files,
                    "files": {f.name: sbk.sha256(f) for f in sorted(work.iterdir()) if f.is_file()},
                    "note": "Data only; schema comes from the versioned migrations. The row-level-security secret is not included. Files in Supabase Storage are not included."}
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        archive = work / "archive.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as z:
            for p in sorted(work.iterdir()):
                if p.name != "archive.zip":
                    z.write(p, p.name)
        final = dest / f"cosmo-backup-{stamp}.cbk"
        part = final.with_suffix(".cbk.part")
        sbk.encrypt_file(archive, part, key)
        os.replace(part, final)
        final.with_suffix(".cbk.json").write_text(json.dumps({"created": stamp, "kind": "postgres", "bytes": final.stat().st_size, "sha256": sbk.sha256(final), "key_id": sbk.key_id(key).hex(),
                                                              "format": "COSMOBK1 AES-256-GCM", "seconds": round(time.time() - started, 2), "migrations": len(applied), "tables": len(tables)}, indent=2), encoding="utf-8")
        if status_dir:
            sbk._write_status(status_dir, last_ok=True, last_success_ts=time.time(), last_file=final.name, last_bytes=final.stat().st_size, last_error="", last_seconds=round(time.time() - started, 2))
        return final
    except Exception as e:
        if status_dir:
            sbk._write_status(status_dir, last_ok=False, last_error=f"{type(e).__name__}: {redact(str(e))[:200]}", last_attempt_ts=time.time())
        if isinstance(e, psycopg.Error):
            raise BackupError("database error: " + safe_exc(e)) from None
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)                                       # the plaintext working copy never outlives the run


def _check_target(target_url: str, allow_remote: bool) -> None:
    host = (urlparse(target_url).hostname or "")
    if host not in LOCAL_HOSTS and not allow_remote:
        raise BackupError("the restore target is not a local host; pass --allow-remote-target only for a disposable database you control")
    with psycopg.connect(target_url, connect_timeout=15) as c:
        if c.execute("select 1 from information_schema.schemata where schema_name='app'").fetchone():
            raise BackupError("the target database already has the app schema: restore only ever goes into a new, empty database (a live database is never overwritten)")


def restore(cbk: Path, target_url: str, key: bytes, files_target: Path | None = None, allow_remote: bool = False) -> dict:
    try:
        return _restore(cbk, target_url, key, files_target, allow_remote)
    except psycopg.Error as e:
        raise BackupError("database error: " + safe_exc(e)) from None
    except migrate.MigrationError as e:
        raise BackupError("migration failed on the target: " + str(e)) from None


def _restore(cbk: Path, target_url: str, key: bytes, files_target: Path | None, allow_remote: bool) -> dict:
    started = time.time()
    _check_target(target_url, allow_remote)
    work = Path(tempfile.mkdtemp(prefix=".restore-"))
    try:
        archive = work / "archive.zip"
        sbk.decrypt_file(cbk, archive, key)
        bdir = work / "b"
        bdir.mkdir()
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if Path(info.filename).name != info.filename:
                    raise BackupError(f"unsafe path in archive: {info.filename}")
            z.extractall(bdir)
        m = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
        if m.get("kind") != "postgres":
            raise BackupError("this is not a PostgreSQL backup")
        for name, digest in m["files"].items():
            if sbk.sha256(bdir / name) != digest:
                raise BackupError(f"integrity check failed for {name}")
        found = {x.version: x for x in migrate.discover()}
        for a in m["migrations"]:
            if a["version"] not in found or found[a["version"]].checksum != a["checksum"]:
                raise BackupError(f"migration {a['version']} in the backup does not match this checkout: restore with the code version the backup was taken from")
        top = max(a["version"] for a in m["migrations"])
        migrate.apply(target_url, target=top)                                          # schema from the versioned migrations (needs an owner credential on the target)
        env = conn_env(target_url)
        run_tool([tool("pg_restore"), "--data-only", "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction", f"--dbname={env['PGDATABASE']}", str(bdir / "data.pgdump")], env, "pg_restore")
        restored_files = 0
        if files_target is not None and (bdir / "private_uploads.zip").exists():
            if files_target.exists() and any(files_target.iterdir()):
                raise BackupError("the files target directory is not empty; refusing to overwrite")
            files_target.mkdir(parents=True, exist_ok=True)
            root = files_target.resolve()
            with zipfile.ZipFile(bdir / "private_uploads.zip") as z:
                for info in z.infolist():
                    dest = (root / info.filename).resolve()
                    if root not in dest.parents:
                        raise BackupError(f"unsafe path in uploads archive: {info.filename}")
                    if not info.is_dir():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(info) as s, open(dest, "wb") as d:
                            shutil.copyfileobj(s, d)
                        restored_files += 1
        report = verify(target_url, m)
        report.update(restored_upload_files=restored_files, seconds=round(time.time() - started, 2), backup=cbk.name)
        rto = os.environ.get("COSMO_BACKUP_RTO_MINUTES")
        if rto:
            report["rto_minutes"] = float(rto)
            report["within_rto"] = report["seconds"] <= float(rto) * 60
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def verify(target_url: str, manifest: dict) -> dict:
    with psycopg.connect(target_url, connect_timeout=15) as c:
        migrations = [{"version": r[0], "name": r[1], "checksum": r[2]} for r in c.execute("select version, name, checksum from app.schema_migrations order by version")]
        got = table_fingerprint(c)
    problems = []
    if migrations != manifest["migrations"]:
        problems.append("migration records differ")
    for t, exp in manifest["tables"].items():
        if t not in got:
            problems.append(f"{t}: missing")
        elif got[t]["rows"] != exp["rows"]:
            problems.append(f"{t}: row count {got[t]['rows']} != {exp['rows']}")
        elif got[t]["digest"] != exp["digest"]:
            problems.append(f"{t}: content digest differs")
    for t in got:
        if t not in manifest["tables"]:
            problems.append(f"{t}: not in the backup")
    result = {"ok": not problems, "tables_checked": len(manifest["tables"]), "rows_total": sum(v["rows"] for v in got.values()), "problems": problems}
    if problems:
        raise BackupError("restore verification FAILED: " + "; ".join(problems))
    return result


def status(dest_dir: Path | None, data_dir: Path) -> dict:
    p = data_dir / "backup_status.json"
    s = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    rpo = os.environ.get("COSMO_BACKUP_RPO_MINUTES")
    out = {"last_ok": s.get("last_ok"), "last_success_ts": s.get("last_success_ts"), "rpo_minutes": float(rpo) if rpo else None, "rpo_chosen_by_owner": bool(rpo)}
    if s.get("last_success_ts"):
        age = (time.time() - s["last_success_ts"]) / 60
        out["age_minutes"] = round(age, 1)
        out["within_rpo"] = (age <= float(rpo)) if rpo else None
    else:
        out["within_rpo"] = False if rpo else None
    return out


def main(argv: list[str]) -> int:
    from app.config import DATA_DIR, UPLOAD_DIR
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "backup" and len(argv) >= 3:
        url = os.environ.get("COSMO_BACKUP_DATABASE_URL") or os.environ.get("COSMO_MIGRATION_DATABASE_URL")
        if not url:
            raise BackupError("set COSMO_BACKUP_DATABASE_URL (or COSMO_MIGRATION_DATABASE_URL)")
        uploads = UPLOAD_DIR if os.environ.get("COSMO_STORAGE", "local") == "local" else None
        out = backup(url, Path(argv[2]), sbk.load_key(), uploads, DATA_DIR)
        pr = sbk.prune(Path(argv[2]), int(os.environ.get("COSMO_BACKUP_KEEP_DAYS", "7")), int(os.environ.get("COSMO_BACKUP_KEEP_WEEKS", "4")))
        print(json.dumps({"backup": out.name, "bytes": out.stat().st_size, "pruned": pr["removed"]}))
    elif cmd == "restore" and len(argv) >= 4:
        ft = Path(argv[argv.index("--files-target") + 1]) if "--files-target" in argv else None
        print(json.dumps(restore(Path(argv[2]), argv[3], sbk.load_key(), ft, "--allow-remote-target" in argv)))
    elif cmd == "status":
        print(json.dumps(status(None, DATA_DIR)))
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

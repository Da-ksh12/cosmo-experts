"""PostgreSQL backups: encrypted, consistent, verified restore into a disposable database, and the safety refusals (never overwrite, never leak)."""
import json
import os
import subprocess
import zipfile
from pathlib import Path

import psycopg
import pytest

from app.dbcore import migrate
from scripts import backup as sbk
from scripts import pg_backup as pb
from tests.test_pg_backend import CTX_SECRET, pg_server  # noqa: F401  (fixture)

MARK = "SENSITIVE-MARKER-9c1d"
KEY = os.urandom(32)


def find_tools():
    try:
        pb.tool("pg_dump"); pb.tool("pg_restore")
    except sbk.BackupError as e:
        pytest.skip(f"PostgreSQL client tools not available: {e}")


@pytest.fixture
def source(pg_server, tmp_path):  # noqa: F811
    find_tools()
    name = pg_server.new_database("src")
    url = pg_server.url(name)
    migrate.apply(url)
    migrate.install_ctx_secret(url, CTX_SECRET)
    with psycopg.connect(url, autocommit=True) as c:
        for uid, role in (("u1", "patient"), ("u2", "patient"), ("d1", "doctor")):
            c.execute("insert into app.users(id,name,role,password_hash,credentials,verified,disabled) values(%s,%s,%s,'', '',0,0)", (uid, f"Name {uid}", role))
        for i in range(4):
            c.execute("insert into app.documents(id,owner_id,filename,stored_path,status,created_at,deleted,size_bytes) values(%s,'u1',%s,'/p','processed','2026-03-04 05:06:07.123456+00',0,10)", (f"doc{i}", f"{MARK}-{i}.pdf"))
            c.execute("insert into app.doc_pages(id,document_id,page_no,text) values(%s,%s,1,%s)", (f"pg{i}", f"doc{i}", f"{MARK} text {i}"))
        c.execute("insert into app.consent_events(id,user_id,notice_sha256,notice_status,action,at) values('ce1','u1','h','approved','accepted','2026-01-01 00:00:00.654321+00')")
        c.execute("insert into app.audit(id,ts,actor,action,target,detail) values('a1','2026-01-01 00:00:00+00','u1','x.y','','{}')")
        c.execute("insert into app.legal_holds(id,patient_id,reason_code,placed_by,placed_at) values('lh','u2','other','d1','2026-02-02 02:02:02+00')")
    yield pg_server, name, url
    pg_server.drop_database(name)


@pytest.fixture
def target(pg_server):  # noqa: F811
    name = pg_server.new_database("rst")
    yield pg_server.url(name)
    pg_server.drop_database(name)


def manifest_of(cbk, tmp_path):
    arch = tmp_path / "m.zip"
    sbk.decrypt_file(cbk, arch, KEY)
    with zipfile.ZipFile(arch) as z:
        return json.loads(z.read("manifest.json")), arch


# ---------------------------------------------------------------- the round trip
def test_backup_is_encrypted_and_restores_into_an_empty_database_with_verified_contents(source, target, tmp_path):
    _, _, url = source
    dest = tmp_path / "bk"
    cbk = pb.backup(url, dest, KEY, status_dir=tmp_path)
    raw = cbk.read_bytes()
    assert MARK.encode() not in raw and b"PGDMP" not in raw[:64] and raw[:8] == sbk.MAGIC                       # encrypted, not a plain dump
    assert not [p for p in dest.iterdir() if p.name.startswith(".work-")]                                         # no plaintext working copy left behind
    meta = json.loads(cbk.with_suffix(".cbk.json").read_text())
    assert meta["kind"] == "postgres" and meta["sha256"] == sbk.sha256(cbk)
    rep = pb.restore(cbk, target, KEY)
    assert rep["ok"] is True and rep["problems"] == [] and rep["tables_checked"] >= 20 and rep["rows_total"] >= 13
    with psycopg.connect(target) as c:
        assert c.execute("select count(*) from app.documents").fetchone()[0] == 4
        assert c.execute("select text from app.doc_pages where id='pg2'").fetchone()[0] == f"{MARK} text 2"
        assert c.execute("select to_char(at at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') from app.consent_events").fetchone()[0] == "2026-01-01 00:00:00.654321"     # microseconds survive
        assert c.execute("select count(*) from app.legal_holds").fetchone()[0] == 1
        assert c.execute("select bool_and(relrowsecurity and relforcerowsecurity) from pg_class where relnamespace='app'::regnamespace and relkind='r' and relname<>'schema_migrations'").fetchone()[0]
        assert [r[0] for r in c.execute("select version from app.schema_migrations order by 1")] == [m.version for m in migrate.discover()]
    st = json.loads((tmp_path / "backup_status.json").read_text())
    assert st["last_ok"] is True and st["last_file"] == cbk.name


def test_the_row_level_security_secret_is_never_part_of_a_backup(source, tmp_path):
    _, _, url = source
    cbk = pb.backup(url, tmp_path / "bk", KEY)
    man, arch = manifest_of(cbk, tmp_path)
    blob = arch.read_bytes() + b"".join(zipfile.ZipFile(arch).read(n) for n in zipfile.ZipFile(arch).namelist())
    assert CTX_SECRET.encode() not in blob and b"ctx_secret" not in blob and "app_private" not in json.dumps(man)


def test_a_restored_database_has_no_secret_until_the_operator_installs_one(source, target, tmp_path):
    _, _, url = source
    pb.restore(pb.backup(url, tmp_path / "bk", KEY), target, KEY)
    with psycopg.connect(target) as c:
        assert c.execute("select count(*) from app_private.ctx_secret").fetchone()[0] == 0                          # the restored database denies everything until set_ctx_secret is run


def test_the_snapshot_is_consistent_even_while_rows_change(source, tmp_path, monkeypatch):
    _, _, url = source
    real = pb.run_tool

    def with_writes(args, env, what, timeout=3600):
        if what == "pg_dump":                                                                                    # a write that commits AFTER the snapshot was exported
            with psycopg.connect(url, autocommit=True) as c:
                c.execute("insert into app.users(id,name,role,password_hash) values('late','Late','patient','')")
        return real(args, env, what, timeout)
    monkeypatch.setattr(pb, "run_tool", with_writes)
    cbk = pb.backup(url, tmp_path / "bk", KEY)
    man, _ = manifest_of(cbk, tmp_path)
    assert man["tables"]["users"]["rows"] == 3                                                                   # manifest and dump agree: the late row is in neither


# ---------------------------------------------------------------- safety refusals
def test_restore_never_overwrites_a_database_that_already_has_the_app_schema(source, tmp_path):
    _, _, url = source
    cbk = pb.backup(url, tmp_path / "bk", KEY)
    with psycopg.connect(url) as c:
        before = c.execute("select count(*) from app.documents").fetchone()[0]
    with pytest.raises(sbk.BackupError, match="already has the app schema"):
        pb.restore(cbk, url, KEY)                                                                                # the live source itself
    with psycopg.connect(url) as c:
        assert c.execute("select count(*) from app.documents").fetchone()[0] == before


def test_remote_targets_need_an_explicit_flag(source, tmp_path):
    _, _, url = source
    cbk = pb.backup(url, tmp_path / "bk", KEY)
    with pytest.raises(sbk.BackupError, match="not a local host"):
        pb.restore(cbk, "postgresql://u:p@db.example.org:5432/x", KEY)


def test_wrong_key_tampering_and_truncation_are_detected_before_anything_is_restored(source, target, tmp_path):
    _, _, url = source
    cbk = pb.backup(url, tmp_path / "bk", KEY)
    with pytest.raises(sbk.BackupError, match="wrong key"):
        pb.restore(cbk, target, os.urandom(32))
    bad = tmp_path / "flipped.cbk"
    data = bytearray(cbk.read_bytes()); data[len(data) // 2] ^= 0xFF; bad.write_bytes(bytes(data))
    with pytest.raises(sbk.BackupError):
        pb.restore(bad, target, KEY)
    short = tmp_path / "short.cbk"; short.write_bytes(cbk.read_bytes()[:-200])
    with pytest.raises(sbk.BackupError):
        pb.restore(short, target, KEY)
    with psycopg.connect(target) as c:
        assert c.execute("select 1 from information_schema.schemata where schema_name='app'").fetchone() is None   # nothing was created


def test_a_backup_from_different_migrations_is_refused_before_the_target_is_touched(source, target, tmp_path, monkeypatch):
    _, _, url = source
    cbk = pb.backup(url, tmp_path / "bk", KEY)
    real = migrate.discover

    def altered(*a, **k):
        ms = real(*a, **k)
        return [type(ms[0])(**{**ms[0].__dict__, "checksum": "0" * 64})] + ms[1:] if hasattr(ms[0], "__dict__") else ms
    monkeypatch.setattr(migrate, "discover", altered)
    with pytest.raises(sbk.BackupError, match="does not match this checkout"):
        pb.restore(cbk, target, KEY)
    with psycopg.connect(target) as c:
        assert c.execute("select 1 from information_schema.schemata where schema_name='app'").fetchone() is None


def test_verification_catches_content_that_differs_from_the_backup(source, target, tmp_path):
    _, _, url = source
    cbk = pb.backup(url, tmp_path / "bk", KEY)
    pb.restore(cbk, target, KEY)
    man, _ = manifest_of(cbk, tmp_path)
    assert pb.verify(target, man)["ok"]
    with psycopg.connect(target, autocommit=True) as c:
        c.execute("update app.doc_pages set text='changed' where id='pg1'")
    with pytest.raises(sbk.BackupError, match="doc_pages: content digest differs"):
        pb.verify(target, man)
    with psycopg.connect(target, autocommit=True) as c:
        c.execute("delete from app.doc_pages where id='pg0'")
    with pytest.raises(sbk.BackupError, match="doc_pages: row count"):
        pb.verify(target, man)


def test_credentials_are_never_on_a_command_line_or_in_errors(source, tmp_path, monkeypatch):
    _, _, url = source
    secret_url = url.replace("postgres@", "postgres:Sup3rS3cret-pw@")
    seen = []
    real = subprocess.run

    def spy(args, **kw):
        seen.append((list(args), kw.get("env", {})))
        return real(args, **kw)
    monkeypatch.setattr(subprocess, "run", spy)
    pb.backup(secret_url, tmp_path / "bk", KEY)
    assert seen and all("Sup3rS3cret-pw" not in " ".join(a) for a, _ in seen) and all(e.get("PGPASSWORD") == "Sup3rS3cret-pw" for _, e in seen)
    with pytest.raises(sbk.BackupError) as e:
        pb.backup(secret_url.replace("/" + secret_url.rsplit("/", 1)[1], "/does_not_exist"), tmp_path / "bk2", KEY, status_dir=tmp_path)
    assert "Sup3rS3cret-pw" not in str(e.value) and "Sup3rS3cret-pw" not in (tmp_path / "backup_status.json").read_text()
    assert json.loads((tmp_path / "backup_status.json").read_text())["last_ok"] is False


def test_command_line_output_contains_counts_only(source, target, tmp_path, monkeypatch, capsys):
    _, _, url = source
    monkeypatch.setenv("COSMO_BACKUP_DATABASE_URL", url); monkeypatch.setenv("COSMO_BACKUP_KEY", __import__("base64").b64encode(KEY).decode())
    assert pb.main(["pg_backup", "backup", str(tmp_path / "bk")]) == 0
    cbk = next((tmp_path / "bk").glob("*.cbk"))
    assert pb.main(["pg_backup", "restore", str(cbk), target]) == 0
    out = capsys.readouterr().out
    assert MARK not in out and "Name u1" not in out and json.loads(out.strip().splitlines()[-1])["ok"] is True


# ---------------------------------------------------------------- files
def test_uploads_are_restored_into_an_empty_directory_and_path_traversal_is_refused(source, target, tmp_path, monkeypatch):
    _, _, url = source
    up = tmp_path / "up"; (up / "u1").mkdir(parents=True); (up / "u1" / "a.pdf").write_bytes(b"file-a"); (up / "u1" / ".b.part").write_bytes(b"partial")
    cbk = pb.backup(url, tmp_path / "bk", KEY, uploads_dir=up)
    rep = pb.restore(cbk, target, KEY, files_target=tmp_path / "restored")
    assert rep["restored_upload_files"] == 1 and (tmp_path / "restored" / "u1" / "a.pdf").read_bytes() == b"file-a" and not (tmp_path / "restored" / "u1" / ".b.part").exists()

    def evil(src, dest):
        with zipfile.ZipFile(dest, "w") as z:
            z.writestr("../evil.txt", "x")
        return 1
    monkeypatch.setattr(pb, "_dir_zip", evil)
    bad = pb.backup(url, tmp_path / "bk2", KEY, uploads_dir=up)
    name = pb.__dict__["psycopg"].connect  # keep a second empty database for this restore
    from tests.pg_harness import get_server
    s = get_server(); n2 = s.new_database("rst2")
    try:
        with pytest.raises(sbk.BackupError, match="unsafe path"):
            pb.restore(bad, s.url(n2), KEY, files_target=tmp_path / "r2")
        assert not (tmp_path / "evil.txt").exists()
        assert name
    finally:
        s.drop_database(n2)
    full = tmp_path / "full"; full.mkdir(); (full / "x").write_text("x")
    s2 = get_server(); n3 = s2.new_database("rst3")
    try:
        monkeypatch.undo()
        with pytest.raises(sbk.BackupError, match="not empty"):
            pb.restore(cbk, s2.url(n3), KEY, files_target=full)
    finally:
        s2.drop_database(n3)


# ---------------------------------------------------------------- RPO / RTO are the owner's numbers
def test_status_reports_against_the_owner_chosen_rpo_and_does_not_invent_one(source, tmp_path, monkeypatch):
    _, _, url = source
    monkeypatch.delenv("COSMO_BACKUP_RPO_MINUTES", raising=False)
    assert pb.status(None, tmp_path) == {"last_ok": None, "last_success_ts": None, "rpo_minutes": None, "rpo_chosen_by_owner": False, "within_rpo": None}
    monkeypatch.setenv("COSMO_BACKUP_RPO_MINUTES", "60")
    assert pb.status(None, tmp_path)["within_rpo"] is False                                                      # no backup at all breaches any RPO
    pb.backup(url, tmp_path / "bk", KEY, status_dir=tmp_path)
    s = pb.status(None, tmp_path)
    assert s["within_rpo"] is True and s["rpo_minutes"] == 60.0 and s["rpo_chosen_by_owner"] is True and s["age_minutes"] < 5


def test_restore_reports_the_measured_time_against_the_owner_chosen_rto(source, target, tmp_path, monkeypatch):
    _, _, url = source
    cbk = pb.backup(url, tmp_path / "bk", KEY)
    assert "within_rto" not in pb.restore(cbk, target, KEY)                                                      # no RTO chosen: nothing is claimed
    from tests.pg_harness import get_server
    s = get_server(); n = s.new_database("rto")
    try:
        monkeypatch.setenv("COSMO_BACKUP_RTO_MINUTES", "30")
        rep = pb.restore(cbk, s.url(n), KEY)
        assert rep["rto_minutes"] == 30.0 and rep["within_rto"] is True and rep["seconds"] > 0
    finally:
        s.drop_database(n)

"""SQLite -> PostgreSQL development-data migration: read-only source, idempotent, resumable, validated, no sensitive output."""
import hashlib
import re
import json
import sqlite3
import time

import psycopg
import pytest

from app import db
from app.dbcore import migrate
from scripts import sqlite_to_postgres as m
from tests.test_pg_backend import pg_server  # noqa: F401  (fixture)

MARK = "SENSITIVE-MARKER-4f9a"
T0 = 1_790_000_000.123456


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "dev.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(db.SCHEMA)
    for table, col, decl in db.COLUMNS:
        if col not in [r[1] for r in con.execute(f"pragma table_info({table})")]:
            con.execute(f"alter table {table} add column {col} {decl}")
    con.execute("create table jobs(id TEXT PRIMARY KEY, owner_id TEXT, task TEXT, status TEXT, request_id TEXT, idempotency_key TEXT, params TEXT, input_path TEXT, result TEXT, error TEXT, "
                "created_at REAL, started_at REAL, finished_at REAL, cancel_requested INTEGER DEFAULT 0)")
    for uid, role in (("u1", "patient"), ("u2", "patient"), ("d1", "doctor")):
        con.execute("insert into users(id,name,role,password_hash,credentials,verified,disabled) values(?,?,?,?,?,?,?)", (uid, f"Name {uid}", role, "pbkdf2$ab$cd", "", 0, 0))
    for i in range(5):
        con.execute("insert into documents(id,owner_id,filename,stored_path,mime,sha256,doc_type,page_count,status,created_at,deleted,size_bytes,user_category) values(?,?,?,?,?,?,?,?,?,?,0,?,?)",
                    (f"doc{i}", "u1", f"{MARK}-{i}.pdf", f"/private/{i}", "application/pdf", f"sha{i}", "laboratory_report", 1, "processed", T0 + i, 100 + i, ""))
        con.execute("insert into doc_pages(id,document_id,page_no,text,method,ocr_confidence,layout,script) values(?,?,?,?,?,?,?,?)", (f"pg{i}", f"doc{i}", 1, f"{MARK} page text {i}", "embedded", 1.0, None, "latin"))
        con.execute("insert into doc_facts(id,document_id,kind,label,value,source_page,source_text,confidence,provenance,normalized,ambiguity,field_no) values(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"fa{i}", f"doc{i}", "report_date", "Report date", "12/03/2025", 1, "Reported: 12/03/2025", 0.9, "{}", "{}", '["day_month_order_unclear"]', 0))
        con.execute("insert into doc_medications(id,document_id,item_no,source_page,raw,name,entry,ambiguity,inferences,confidence,parser) values(?,?,?,?,?,?,?,?,?,?,?)",
                    (f"md{i}", f"doc{i}", 0, 1, "Tab Metformin 500 mg BD", "Metformin", "{}", "[]", "[]", 0.8, "medparse-1.1.0"))
        for n in range(3):
            con.execute("insert into doc_fields(id,document_id,name,value,unit,ref_range,source_page,source_text,confidence,flag,review_status,corrected_by,processing_version,patient_flag,field_no)"
                        " values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"f{i}-{n}", f"doc{i}", "Ferritin", "8", "ng/mL", "15-150", 1, "line", 0.93, "L", "pending", "", "v", "", n))
    con.execute("insert into images(id,owner_id,region,stored_path,verdict,report,created_at,deleted,clinician_review,reviewed_by,reviewed_at) values('i1','u1','face','/p/i1','accepted','{}',?,0,'reviewed','d1',?)", (T0, T0 + 5))
    con.execute("insert into conversations values('c1','u1',?)", (T0,))
    con.execute("insert into messages values('m1','c1','patient',?, '[]', ?)", (f"{MARK} question", T0))
    con.execute("insert into quotes(id,patient_id,status,lines,total,currency,notes,created_by,approved_by,created_at,updated_at,valid_until,subtotal,tax_percent,tax_amount) values('q1','u1','approved','[]',112.5,'INR','',?,?,?,?,?,100,12,12.5)", ("u1", "d1", T0, T0, T0 + 86400))
    con.execute("insert into care_plans values('cp1','u1','d1','approved','a','[]','[]','n','[]',?,?)", (T0, T0))
    con.execute("insert into consents values('u1','shaX',?)", (T0,))
    con.execute("insert into notifications(id,user_id,kind,title,body,link,dedupe_key,created_at,read_at) values('n1','u1','k','t','b','/l','dk',?,NULL)", (T0,))
    con.execute("insert into kb_articles(id,title,body,language,review_status,reviewed_by,version,reviewed_at,review_due,content_sha256,attestation,source_note,content_origin,claims_to_verify) "
                "values('k1','T','B','en','approved','d1',2,?,?,'h','{}','src note here','clinic','[]')", (T0, T0 + 1000))
    con.execute("insert into settings values('quote_config','{}')")
    con.execute("insert into datasets values('ds1','{}','approved',?)", (T0,))
    con.execute("insert into dataset_versions values('dv1','ds1','{}',?)", (T0,))
    con.execute("insert into model_versions values('mv1','t','{}','research_only',?)", (T0,))
    con.execute("insert into audit values('a1',?,'u1','x','t','{}')", (T0,))
    con.execute("insert into jobs(id,owner_id,task,status,created_at,cancel_requested) values('j1','u1','t','done',?,0)", (T0,))
    con.commit(); con.close()
    return path


@pytest.fixture
def target(pg_server):
    name = pg_server.new_database("mig")
    migrate.apply(pg_server.url(name))
    yield pg_server.url(name)
    pg_server.drop_database(name)


def count(url, table):
    with psycopg.connect(url) as c:
        return c.execute(f"select count(*) from app.{table}").fetchone()[0]


def file_hash(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_dry_run_reads_everything_and_writes_nothing(source, target):
    rep = m.migrate_data(source, target, dry_run=True)
    assert rep["dry_run"] is True and rep["tables"]["documents"]["source"] == 5 and rep["tables"]["documents"]["inserted"] == 5
    assert count(target, "documents") == 0 and count(target, "users") == 0


def test_full_copy_is_validated_and_the_source_is_untouched(source, target):
    before = (file_hash(source), source.stat().st_mtime_ns)
    rep = m.migrate_data(source, target, batch=2)
    assert rep["validation"]["ok"] is True, rep["validation"]
    assert {t: v["target_rows"] for t, v in rep["validation"]["tables"].items()}["doc_fields"] == 15
    assert {t: v["target_rows"] for t, v in rep["validation"]["tables"].items()}["doc_facts"] == 5 and {t: v["target_rows"] for t, v in rep["validation"]["tables"].items()}["doc_medications"] == 5
    assert all(v["count_ok"] and v.get("key_set_ok", True) and v.get("critical_fields_ok", True) for v in rep["validation"]["tables"].values())
    assert (file_hash(source), source.stat().st_mtime_ns) == before                                        # read-only: byte-identical and not even touched
    with psycopg.connect(target) as c:
        r = c.execute("select created_at, valid_until from app.quotes where id='q1'").fetchone()
        assert r[0].tzinfo is not None and abs(r[0].timestamp() - T0) < 1e-5 and abs(r[1].timestamp() - (T0 + 86400)) < 1e-5     # timezone-aware, microsecond-exact
        assert c.execute("select pg_typeof(created_at)::text from app.documents limit 1").fetchone()[0] == "timestamp with time zone"
        assert c.execute("select count(*) from app.notifications where read_at is null").fetchone()[0] == 1                       # NULL stays NULL
        assert c.execute("select role, password_hash from app.users where id='d1'").fetchone() == ("doctor", "pbkdf2$ab$cd")


def test_rerunning_is_idempotent_and_conflict_modes_work(source, target):
    m.migrate_data(source, target)
    again = m.migrate_data(source, target)
    assert again["tables"]["documents"]["inserted"] == 0 and again["tables"]["documents"]["skipped_existing"] == 5 and again["validation"]["ok"]
    assert count(target, "documents") == 5 and count(target, "doc_fields") == 15
    con = sqlite3.connect(source); con.execute("update documents set status='reviewed' where id='doc1'"); con.commit(); con.close()
    skip = m.migrate_data(source, target)
    with psycopg.connect(target) as c:
        assert c.execute("select status from app.documents where id='doc1'").fetchone()[0] == "processed"        # skip keeps the target as it was
    assert skip["tables"]["documents"]["skipped_existing"] == 5
    upd = m.migrate_data(source, target, on_conflict="update")
    with psycopg.connect(target) as c:
        assert c.execute("select status from app.documents where id='doc1'").fetchone()[0] == "reviewed"
    assert upd["tables"]["documents"]["updated"] == 5
    with pytest.raises(m.MigrationAbort, match="already exist"):
        m.migrate_data(source, target, on_conflict="fail")


def test_an_interrupted_run_resumes_from_the_progress_file(source, target, tmp_path, monkeypatch):
    progress = tmp_path / "progress.json"
    real = m.save_progress
    calls = {"n": 0}

    def dying(path, data):
        calls["n"] += 1
        real(path, data)
        if calls["n"] == 4:
            raise KeyboardInterrupt("simulated crash")
    monkeypatch.setattr(m, "save_progress", dying)
    with pytest.raises(KeyboardInterrupt):
        m.migrate_data(source, target, batch=1, progress_path=progress)
    partial = {t: count(target, t) for t in ("users", "documents")}
    assert sum(partial.values()) > 0 and not all(json.loads(progress.read_text()).get(t, {}).get("complete") for t in m.ORDER)
    monkeypatch.setattr(m, "save_progress", real)
    rep = m.migrate_data(source, target, batch=1, progress_path=progress)
    assert rep["validation"]["ok"] and count(target, "documents") == 5 and count(target, "doc_pages") == 5 and count(target, "users") == 3          # no duplicates, nothing missing
    assert all(v.get("complete") for v in json.loads(progress.read_text()).values())


def test_foreign_key_orphans_stop_the_run_unless_explicitly_skipped(source, target):
    con = sqlite3.connect(source)
    con.execute("insert into documents(id,owner_id,filename,stored_path,mime,sha256,doc_type,status,created_at,deleted) values('orph','ghost','x','/p','a','s','t','processed',?,0)", (T0,))
    con.execute("insert into doc_pages(id,document_id,page_no,text,method,ocr_confidence) values('orphpg','orph',1,'t','embedded',1.0)")
    con.execute("insert into doc_medications(id,document_id,item_no,raw,entry) values('orphmd','orph',0,'Tab X','{}')")
    con.commit(); con.close()
    with pytest.raises(m.MigrationAbort, match="foreign-key orphan"):
        m.migrate_data(source, target)
    rep = m.migrate_data(source, target, orphans="skip")
    assert rep["orphans_skipped"] == {"documents": 1} and rep["validation"]["ok"] is True
    with psycopg.connect(target) as c:
        assert c.execute("select count(*) from app.documents where id='orph'").fetchone()[0] == 0 and c.execute("select count(*) from app.doc_pages where id='orphpg'").fetchone()[0] == 0
        assert c.execute("select count(*) from app.doc_medications where id='orphmd'").fetchone()[0] == 0           # a child of a skipped orphan is skipped with it


def test_a_schema_mismatch_or_an_unmigrated_target_is_refused(source, target, pg_server, tmp_path):
    con = sqlite3.connect(source); con.execute("alter table documents add column surprise TEXT"); con.commit(); con.close()
    with pytest.raises(m.MigrationAbort, match="schema mismatch"):
        m.migrate_data(source, target)
    empty = pg_server.new_database("empty")
    try:
        with pytest.raises(m.MigrationAbort, match="not at the expected version"):
            m.migrate_data(source, pg_server.url(empty))
    finally:
        pg_server.drop_database(empty)
    with pytest.raises(m.MigrationAbort, match="does not exist"):
        m.migrate_data(tmp_path / "missing.sqlite3", target)


def test_validation_detects_a_tampered_target(source, target):
    m.migrate_data(source, target)
    with psycopg.connect(target) as c:
        c.execute("update app.users set role='admin' where id='u1'"); c.commit()                           # same row count, same keys, different critical field
    src = sqlite3.connect(source)
    with psycopg.connect(target, row_factory=psycopg.rows.dict_row) as pg:
        v = m.validate(src, pg, {r[0] for r in src.execute("select name from sqlite_master where type='table'")}, {})
    assert v["ok"] is False and v["tables"]["users"]["critical_fields_ok"] is False and v["tables"]["users"]["count_ok"] is True


def test_nothing_sensitive_reaches_stdout_or_the_progress_file(source, target, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("COSMO_MIGRATION_DATABASE_URL", target)
    progress = tmp_path / "p.json"
    assert m.main(["--sqlite", str(source), "--progress", str(progress)]) == 0
    out = capsys.readouterr().out
    assert MARK not in out and MARK not in progress.read_text() and "pbkdf2" not in out and "u1" not in out
    assert json.loads(out)["validation"]["ok"] is True
    assert m.main(["--sqlite", str(source / "nope")]) == 1
    monkeypatch.delenv("COSMO_MIGRATION_DATABASE_URL")
    assert m.main(["--sqlite", str(source)]) == 2


def test_a_corrupt_progress_file_is_refused_not_ignored(source, target, tmp_path):
    p = tmp_path / "p.json"; p.write_text("{nope")
    with pytest.raises(m.MigrationAbort, match="not valid JSON"):
        m.migrate_data(source, target, progress_path=p)
    assert count(target, "users") == 0
    time.sleep(0)


def test_a_table_the_tool_does_not_copy_stops_the_run_instead_of_leaving_data_behind(source, target):
    con = sqlite3.connect(source); con.execute("create table brand_new_table(id text, secret text)"); con.execute("insert into brand_new_table values('1','x')"); con.commit(); con.close()
    with pytest.raises(m.MigrationAbort, match="brand_new_table"):
        m.migrate_data(source, target)


def test_every_application_table_in_the_sqlite_schema_is_covered_by_the_copy_order():
    from app import db as appdb
    declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", appdb.SCHEMA))
    assert declared <= set(m.ORDER) | m.IGNORED, sorted(declared - set(m.ORDER))

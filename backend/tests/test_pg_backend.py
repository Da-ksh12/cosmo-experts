"""PostgreSQL data layer against a REAL disposable PostgreSQL 17 server (tests/pg_harness.py). Skipped, with an explicit reason, when no server binaries exist."""
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import psycopg
import pytest

from app import db
from app.dbcore import context, migrate
from app.dbcore.dialect import DialectError, to_pg
from app.dbcore.pg_backend import DatabaseUnavailable, PostgresBackend
from app.dbcore.sqlite_backend import SqliteBackend
from app.dbcore.types import Epoch
from tests import pg_harness


@pytest.fixture(scope="session")
def pg_server():
    s = pg_harness.get_server()
    if s is None:
        pytest.skip("no PostgreSQL server distribution found (set COSMO_PG_BIN); PostgreSQL tests did not run")
    return s


@pytest.fixture
def pgdb(pg_server):
    name = pg_server.new_database("b")
    migrate.apply(pg_server.url(name))
    be = PostgresBackend(pg_server.url(name, "cosmo_app"), pool_min=1, pool_max=8, connect_timeout=3, statement_timeout_ms=5000, retries=1)
    be.open()
    sysctx = context.make("", "system")
    be.x("insert into users(id,name,role) values('u1','U One','patient'),('u2','U Two','patient'),('d1','Doc','doctor')", (), sysctx)
    yield be, pg_server.url(name), sysctx
    be.close()
    pg_server.drop_database(name)


@pytest.fixture
def sqlite_backend(tmp_path):
    be = SqliteBackend(tmp_path / "t.sqlite3")
    with be.tx() as t:
        t.script(db.SCHEMA)
    return be


def use(monkeypatch, backend):
    monkeypatch.setattr(db, "_backend", backend)


# ---------------------------------------------------------------- dialect (no server needed)
@pytest.mark.parametrize("src,out", [
    ("SELECT * FROM t WHERE a=? AND b=?", "SELECT * FROM t WHERE a=%s AND b=%s"),
    ("SELECT 1 FROM t WHERE title LIKE '%reference range%'", "SELECT 1 FROM t WHERE title ILIKE '%%reference range%%'"),
    ("SELECT '?' q, ? p", "SELECT '?' q, %s p"),                                                 # a question mark inside a string literal is data
    ("SELECT 'it''s ?' a, ? b", "SELECT 'it''s ?' a, %s b"),
    ('SELECT "odd?col" FROM t WHERE x=?', 'SELECT "odd?col" FROM t WHERE x=%s'),
    ("SELECT a LIKE 'x%' FROM t WHERE detail NOT LIKE ?", "SELECT a ILIKE 'x%%' FROM t WHERE detail NOT ILIKE %s"),
    ("SELECT likelihood FROM t", "SELECT likelihood FROM t"),                                      # LIKE only as a whole word
])
def test_dialect_translation(src, out):
    assert to_pg(src) == out


@pytest.mark.parametrize("bad", ["INSERT OR IGNORE INTO t VALUES(1)", "insert or replace into t values(1)", "PRAGMA table_info(t)", "SELECT 'x"])
def test_unsupported_constructs_are_refused_not_guessed(bad):
    with pytest.raises(DialectError):
        to_pg(bad)


# ---------------------------------------------------------------- migrations
def test_a_clean_database_is_provisioned_and_reapplying_is_a_noop(pg_server):
    name = pg_server.new_database("m")
    try:
        url = pg_server.url(name)
        assert migrate.apply(url, dry_run=True) == [1] and migrate.status(url)["applied"] == []          # a dry run changes nothing
        assert migrate.apply(url) == [1] and migrate.apply(url) == []
        st = migrate.status(url)
        assert st["applied"] == [1] and st["pending"] == [] and st["drift"] == []
        with psycopg.connect(url) as c:
            have = {r[0] for r in c.execute("select table_name from information_schema.tables where table_schema='app'")}
            assert migrate.expected_tables() <= have and "schema_migrations" in have
            assert c.execute("select 1 from pg_roles where rolname='cosmo_app' and not rolbypassrls and not rolsuper").fetchone()
    finally:
        pg_server.drop_database(name)


def test_migration_files_must_be_contiguous_and_well_named(tmp_path):
    (tmp_path / "0001_a.sql").write_text("select 1;"); (tmp_path / "0003_c.sql").write_text("select 1;")
    with pytest.raises(migrate.MigrationError, match="contiguous"):
        migrate.discover(tmp_path)
    (tmp_path / "0003_c.sql").unlink(); (tmp_path / "bad name.sql").write_text("select 1;")
    with pytest.raises(migrate.MigrationError, match="NNNN_name"):
        migrate.discover(tmp_path)


def test_a_modified_applied_migration_is_detected_and_refused(pg_server, tmp_path):
    name = pg_server.new_database("d")
    try:
        d = tmp_path / "m"; d.mkdir()
        (d / "0001_t.sql").write_text("create schema if not exists app; create table app.t1(id int);")
        migrate.apply(pg_server.url(name), directory=d)
        (d / "0001_t.sql").write_text("create schema if not exists app; create table app.t1(id int, extra int);")     # edited after it was applied
        assert migrate.status(pg_server.url(name), directory=d)["drift"] == [1]
        with pytest.raises(migrate.MigrationError, match="modified after being applied"):
            migrate.apply(pg_server.url(name), directory=d)
    finally:
        pg_server.drop_database(name)


def test_a_failing_migration_rolls_back_completely_and_leaves_the_previous_version(pg_server, tmp_path):
    name = pg_server.new_database("f")
    try:
        d = tmp_path / "m"; d.mkdir()
        (d / "0001_ok.sql").write_text("create schema if not exists app; create table app.keep(id int);")
        (d / "0002_bad.sql").write_text("create table app.half(id int); insert into app.keep values ('not-a-number');")
        with pytest.raises(migrate.MigrationError, match="rolled back"):
            migrate.apply(pg_server.url(name), directory=d)
        with psycopg.connect(pg_server.url(name)) as c:
            tables = {r[0] for r in c.execute("select table_name from information_schema.tables where table_schema='app'")}
            assert "keep" in tables and "half" not in tables                                              # no half-applied migration
            assert [r[0] for r in c.execute("select version from app.schema_migrations")] == [1]
        (d / "0002_bad.sql").write_text("create table app.fixed(id int);")                                 # fixing it is NOT editing an applied file: it never applied
        assert migrate.apply(pg_server.url(name), directory=d) == [2]
    finally:
        pg_server.drop_database(name)


def test_a_database_ahead_of_the_code_is_refused(pg_server, tmp_path):
    name = pg_server.new_database("a")
    try:
        d = tmp_path / "m"; d.mkdir()
        (d / "0001_a.sql").write_text("create schema if not exists app;"); (d / "0002_b.sql").write_text("select 1;")
        migrate.apply(pg_server.url(name), directory=d)
        (d / "0002_b.sql").unlink()
        with pytest.raises(migrate.MigrationError, match="ahead of the application"):
            migrate.apply(pg_server.url(name), directory=d)
    finally:
        pg_server.drop_database(name)


def test_concurrent_runners_do_not_double_apply(pg_server, tmp_path):
    name = pg_server.new_database("c")
    try:
        d = tmp_path / "m"; d.mkdir()
        (d / "0001_a.sql").write_text("create schema if not exists app; create table app.once(id int);")
        results, errs = [], []

        def run():
            try:
                results.append(migrate.apply(pg_server.url(name), directory=d))
            except Exception as e:                                                                        # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=run) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert not errs and sorted(map(tuple, results)) == [(), (), (), (1,)]                               # exactly one runner applied it
    finally:
        pg_server.drop_database(name)


def test_startup_check_modes(pg_server, monkeypatch):
    from app.settings import Settings
    name = pg_server.new_database("s")
    try:
        be = PostgresBackend(pg_server.url(name, "cosmo_app"), retries=0, connect_timeout=2)
        env = {"COSMO_MODE": "production", "COSMO_DB": "postgres", "COSMO_DATABASE_URL": pg_server.url(name, "cosmo_app")}
        check = Settings.from_env({**env, "COSMO_MIGRATION_MODE": "check"})
        with pytest.raises(migrate.MigrationError, match="not been migrated"):
            migrate.startup_check(be, check)                                                              # empty database: refuse to run
        monkeypatch.setenv("COSMO_MIGRATION_DATABASE_URL", pg_server.url(name))
        migrate.startup_check(be, Settings.from_env({**env, "COSMO_MIGRATION_MODE": "apply"}))             # explicit apply mode migrates with the owner credential
        migrate.startup_check(be, check)                                                                  # now check passes
        migrate.startup_check(be, Settings.from_env({**env, "COSMO_MIGRATION_MODE": "off"}))
        with psycopg.connect(pg_server.url(name)) as c:
            c.execute("delete from app.schema_migrations"); c.commit()
        with pytest.raises(migrate.MigrationError, match="pending"):
            migrate.startup_check(be, check)
        be.close()
    finally:
        pg_server.drop_database(name)


def test_the_application_role_cannot_change_the_schema_or_the_audit_trail(pgdb):
    be, url, sysctx = pgdb
    for ddl in ("create table app.evil(id int)", "drop table app.users", "alter table app.users add column x int", "create index zz on app.users(name)"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            be.x(ddl, (), sysctx)
    be.x("insert into audit values('a1', now(), 'u1', 'act', 't', '{}')", (), sysctx)
    for stmt in ("update audit set action='x'", "delete from audit", "truncate audit"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            be.x(stmt, (), sysctx)
    assert be.q("select count(*) c from audit", (), sysctx)[0]["c"] == 1


# ---------------------------------------------------------------- schema parity with SQLite
def test_every_sqlite_table_exists_in_postgres_with_the_same_columns_in_the_same_order(pgdb, tmp_path):
    from app.gateway import jobs
    be, url, sysctx = pgdb
    sq = tmp_path / "parity.sqlite3"
    sbe = SqliteBackend(sq)
    with sbe.tx() as t:
        t.script(db.SCHEMA)
        for table, col, decl in db.COLUMNS:
            if col not in [r["name"] for r in t.q(f"PRAGMA table_info({table})")]:
                t.x(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    con = sqlite3.connect(sq)
    con.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner_id TEXT, task TEXT, status TEXT, request_id TEXT, idempotency_key TEXT, params TEXT, input_path TEXT, "
                "result TEXT, error TEXT, created_at REAL, started_at REAL, finished_at REAL, cancel_requested INTEGER DEFAULT 0)")
    sqlite_tables = {r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
    assert jobs and sqlite_tables
    with psycopg.connect(url) as c:
        for t in sorted(sqlite_tables):
            s_cols = [r[1] for r in con.execute(f"pragma table_info({t})")]
            p_cols = [r[0] for r in c.execute("select column_name from information_schema.columns where table_schema='app' and table_name=%s order by ordinal_position", (t,))]
            assert s_cols == p_cols, (t, s_cols, p_cols)
        # every time-valued column is timezone-aware
        time_cols = c.execute("select table_name, column_name from information_schema.columns where table_schema='app' and (column_name like '%\\_at' or column_name in ('ts','review_due','valid_until'))").fetchall()
        for tname, cname in time_cols:
            if tname == "schema_migrations":
                continue
            typ = c.execute("select data_type from information_schema.columns where table_schema='app' and table_name=%s and column_name=%s", (tname, cname)).fetchone()[0]
            assert typ == "timestamp with time zone", (tname, cname, typ)


# ---------------------------------------------------------------- values and semantics
def test_timestamps_round_trip_as_epoch_and_are_timezone_aware(pgdb, monkeypatch):
    be, url, sysctx = pgdb
    use(monkeypatch, be)
    with context.as_system():
        t0 = db.now()
        assert isinstance(t0, Epoch)
        db.x("insert into conversations(id, owner_id, created_at) values(?,?,?)", ("c1", "u1", t0))
        row = db.q1("select created_at from conversations where id=?", ("c1",))
        assert isinstance(row["created_at"], Epoch) and abs(row["created_at"] - t0) < 1e-5                    # microsecond round trip
        assert db.q1("select pg_typeof(created_at)::text t from conversations where id='c1'")["t"] == "timestamp with time zone"
        # Epoch arithmetic and comparisons keep working in SQL parameters
        assert db.q1("select count(*) c from conversations where created_at > ?", (t0 - 3600,))["c"] == 1
        assert db.q1("select count(*) c from conversations where created_at > ?", (t0 + 3600,))["c"] == 0
        assert isinstance(t0 + 5, Epoch) and isinstance(t0 - 5, Epoch) and isinstance(t0 - Epoch(t0 - 7), float) and abs((t0 - Epoch(t0 - 7)) - 7) < 1e-9
        assert db.q1("select extract(epoch from created_at)::float8 e from conversations where id='c1'")["e"] == pytest.approx(float(t0), abs=1e-5)


def test_plain_floats_stay_floats_and_text_is_preserved(pgdb, monkeypatch):
    be, url, sysctx = pgdb
    use(monkeypatch, be)
    with context.as_system():
        db.x("insert into price_items(id,name,unit_price,currency,active) values(?,?,?,?,?)", ("p1", "Peel — हिन्दी é", 1234.5678, "INR", 1))
        r = db.q1("select * from price_items where id='p1'")
        assert r["unit_price"] == 1234.5678 and type(r["unit_price"]) is float and r["name"] == "Peel — हिन्दी é" and r["sessions"] == 1


def test_like_is_case_insensitive_like_sqlite_and_percent_is_literal_safe(pgdb, monkeypatch):
    be, url, sysctx = pgdb
    use(monkeypatch, be)
    with context.as_system():
        db.x("insert into kb_articles(id,title,body,language,review_status,version) values('k1','What is a Reference Range?','b','en','draft',1)")
        assert db.q1("select id from kb_articles where title LIKE '%reference range%'")["id"] == "k1"
        assert db.q1("select id from kb_articles where title LIKE ?", ("%RANGE%",))["id"] == "k1"
        db.x("insert into audit values('a1', ?, 'u1', 'x', 't', ?)", (db.now(), '{"abstained": true}'))
        assert db.q1("select count(*) c from audit where detail LIKE '%\"abstained\": true%'")["c"] == 1


def test_transactions_commit_atomically_and_roll_back_on_error(pgdb, monkeypatch):
    be, url, sysctx = pgdb
    use(monkeypatch, be)
    with context.as_system():
        with pytest.raises(RuntimeError):
            with db.tx() as t:
                t.x("insert into conversations(id, owner_id, created_at) values('tx1','u1', now())")
                t.x("insert into messages(id, conversation_id, role, content, created_at) values('m1','tx1','patient','hi', now())")
                raise RuntimeError("boom after two writes")
        assert db.q1("select count(*) c from conversations where id='tx1'")["c"] == 0 and db.q1("select count(*) c from messages where id='m1'")["c"] == 0
        with db.tx() as t:
            t.x("insert into conversations(id, owner_id, created_at) values('tx2','u1', now())")
            t.x("insert into messages(id, conversation_id, role, content, created_at) values('m2','tx2','patient','hi', now())")
            assert t.q1("select count(*) c from messages where conversation_id='tx2'")["c"] == 1        # visible inside the transaction
        assert db.q1("select count(*) c from messages where id='m2'")["c"] == 1
        with pytest.raises(db.integrity_errors()):                                                        # a constraint violation inside tx rolls everything back
            with db.tx() as t:
                t.x("insert into conversations(id, owner_id, created_at) values('tx3','u1', now())")
                t.x("insert into conversations(id, owner_id, created_at) values('tx3','u1', now())")
        assert db.q1("select count(*) c from conversations where id='tx3'")["c"] == 0


@pytest.mark.parametrize("which", ["sqlite", "postgres"])
def test_insert_ignore_and_upsert_behave_identically_on_both_backends(which, pgdb, sqlite_backend, monkeypatch):
    be = pgdb[0] if which == "postgres" else sqlite_backend
    use(monkeypatch, be)
    with context.as_system():
        assert db.is_postgres() == (which == "postgres")
        if which == "postgres":
            db.x("insert into users(id,name,role) values('n1','N','patient') on conflict do nothing")
        else:
            db.x("insert into users(id,name,role,password_hash) values('n1','N','patient','')")
        assert db.insert_ignore("notifications", {"id": "a", "user_id": "n1", "kind": "k", "title": "t", "dedupe_key": "same", "created_at": db.now()}) is True
        assert db.insert_ignore("notifications", {"id": "b", "user_id": "n1", "kind": "k", "title": "t", "dedupe_key": "same", "created_at": db.now()}) is False    # unique dedupe key
        assert db.q1("select count(*) c from notifications where user_id='n1'")["c"] == 1
        db.set_setting("k", "v1"); db.set_setting("k", "v2")
        assert db.q("select value from settings where key='k'") == [{"value": "v2"}]
        db.upsert("datasets", "id", {"id": "d1", "data": "{}", "approval_status": "unreviewed", "updated_at": db.now()})
        db.upsert("datasets", "id", {"id": "d1", "data": "{\"x\":1}", "approval_status": "approved", "updated_at": db.now()})
        assert db.q1("select data, approval_status from datasets where id='d1'") == {"data": "{\"x\":1}", "approval_status": "approved"}
        with pytest.raises(ValueError):
            db.upsert("settings; drop table users", "key", {"key": "a", "value": "b"})
        with pytest.raises(ValueError):
            db.insert_ignore("settings", {"key; --": "x"})


def test_many_threads_share_the_pool_without_losing_writes(pgdb, monkeypatch):
    be, url, sysctx = pgdb
    use(monkeypatch, be)
    errs = []

    def work(i):
        try:
            with context.as_system():
                for j in range(15):
                    db.x("insert into audit values(?,?,?,?,?,?)", (f"t{i}-{j}", db.now(), f"u{i}", "load", "t", "{}"))
        except Exception as e:                                                                          # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs, errs[:1]
    assert be.q("select count(*) c from audit where action='load'", (), sysctx)[0]["c"] == 16 * 15


# ---------------------------------------------------------------- failure behaviour
def test_an_unreachable_database_fails_after_bounded_retries_with_a_redacted_message():
    be = PostgresBackend("postgresql://cosmo_app:TopSecretPw@127.0.0.1:1/none", pool_min=0, pool_max=1, connect_timeout=1, retries=1)
    t0 = time.time()
    with pytest.raises(DatabaseUnavailable) as e:
        be.q("select 1")
    assert time.time() - t0 < 30 and "TopSecretPw" not in str(e.value)
    assert be.healthy()[0] is False


def test_reads_are_retried_on_connection_errors_but_writes_are_not(pgdb, monkeypatch):
    be, url, sysctx = pgdb
    calls = {"read": 0, "write": 0}
    real_tx = be.tx

    def flaky(kind):
        from contextlib import contextmanager

        @contextmanager
        def tx(ctx=None):
            calls[kind] += 1
            if calls[kind] == 1:
                raise psycopg.OperationalError("connection lost")
            with real_tx(ctx) as t:
                yield t
        return tx
    monkeypatch.setattr(be, "tx", flaky("read"))
    assert be.q("select 1 as x", (), sysctx) == [{"x": 1}] and calls["read"] == 2                         # retried once and succeeded
    monkeypatch.setattr(be, "tx", flaky("write"))
    with pytest.raises(psycopg.OperationalError):
        be.x("insert into settings values('w','1')", (), sysctx)
    assert calls["write"] == 1                                                                             # never repeated


def test_slow_statements_are_cancelled_by_the_statement_timeout(pg_server):
    name = pg_server.new_database("to")
    try:
        migrate.apply(pg_server.url(name))
        be = PostgresBackend(pg_server.url(name, "cosmo_app"), statement_timeout_ms=300, retries=0)
        with pytest.raises(psycopg.errors.QueryCanceled):
            be.q("select pg_sleep(3)", (), context.make("", "system"))
        be.close()
    finally:
        pg_server.drop_database(name)


# ---------------------------------------------------------------- identity context
def test_the_identity_context_is_visible_inside_each_transaction_and_defaults_to_none(pgdb):
    be, url, sysctx = pgdb
    q = "select current_setting('app.user_id', true) u, current_setting('app.role', true) r, current_setting('app.admin_clinical', true) a"
    assert be.q(q, (), None)[0] == {"u": "", "r": "none", "a": "none"}                                      # no context given: role none
    assert be.q(q, (), context.make("u1", "patient"))[0]["r"] == "patient" and be.q(q, (), context.make("u1", "patient"))[0]["u"] == "u1"
    assert be.q(q, (), None)[0]["r"] == "none"                                                              # nothing leaks onto the next transaction of a pooled connection
    with pytest.raises(ValueError):
        context.make("u1", "superuser")


def test_context_is_taken_from_the_current_task_and_threads_start_empty(pgdb, monkeypatch):
    be, url, sysctx = pgdb
    use(monkeypatch, be)
    seen = {}
    with context.as_user("u2", "doctor"):
        seen["main"] = db.q1("select current_setting('app.role', true) r")["r"]
        t = threading.Thread(target=lambda: seen.update(thread=db.q1("select current_setting('app.role', true) r")["r"]))
        t.start(); t.join()
        t2 = threading.Thread(target=context.run_as_system(lambda: seen.update(system=db.q1("select current_setting('app.role', true) r")["r"])))
        t2.start(); t2.join()
    assert seen == {"main": "doctor", "thread": "none", "system": "system"}

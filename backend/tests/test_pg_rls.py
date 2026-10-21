"""Database-level isolation (row-level security) against a real PostgreSQL. The application role has no BYPASSRLS; every statement here runs as cosmo_app with an identity
published exactly as the application does it (signed, transaction-local)."""
import hashlib
import hmac as pyhmac

import psycopg
import pytest

from app.dbcore import context, migrate
from app.dbcore.context import DbContext
from app.dbcore.pg_backend import PostgresBackend
from tests.pg_harness import get_server  # noqa: F401
from tests.test_pg_backend import CTX_SECRET, pg_server  # noqa: F401  (fixtures)

T = "2026-10-18T10:00:00Z"
TABLES = ["users", "settings", "documents", "doc_pages", "doc_fields", "doc_facts", "doc_medications", "images", "conversations", "messages", "price_items", "quotes", "kb_articles", "datasets",
          "dataset_versions", "model_versions", "audit", "field_corrections", "care_plans", "consents", "consent_events", "legal_holds", "training_runs", "agent_traces", "agent_actions", "model_events", "notifications", "jobs", "appointment_requests"]


def C(uid, role, admin="none"):
    return DbContext(uid, role, admin)


SYS, P1, P2, DOC, ADM, ADMR = C("", "system"), C("p1", "patient"), C("p2", "patient"), C("d1", "doctor"), C("a1", "admin"), C("a1", "admin", "read")


@pytest.fixture(scope="module")
def rls(pg_server):
    name = pg_server.new_database("rls")
    migrate.apply(pg_server.url(name))
    migrate.install_ctx_secret(pg_server.url(name), CTX_SECRET)
    be = PostgresBackend(pg_server.url(name, "cosmo_app"), pool_max=4, connect_timeout=3, retries=0, context_secret=CTX_SECRET)
    be.open()
    x = lambda sql, a=(): be.x(sql, a, SYS)  # noqa: E731
    for uid, role in (("p1", "patient"), ("p2", "patient"), ("d1", "doctor"), ("a1", "admin")):
        x("insert into users(id,name,role) values(%s,%s,%s)".replace("%s", "?"), (uid, uid.upper(), role))
    for p in ("p1", "p2"):
        x("insert into documents(id,owner_id,filename,status,created_at,deleted) values(?,?,?,?,now(),0)", (f"doc-{p}", p, f"{p}.pdf", "processed"))
        x("insert into doc_pages(id,document_id,page_no,text) values(?,?,1,?)", (f"pg-{p}", f"doc-{p}", f"secret text of {p}"))
        x("insert into doc_fields(id,document_id,name,value) values(?,?,'Ferritin','8')", (f"f-{p}", f"doc-{p}"))
        x("insert into doc_facts(id,document_id,kind,label,value) values(?,?,'report_date','Report date','12/03/2025')", (f"fa-{p}", f"doc-{p}"))
        x("insert into doc_medications(id,document_id,item_no,raw,name,entry) values(?,?,0,?,'Metformin','{}')", (f"md-{p}", f"doc-{p}", f"Tab Metformin 500 mg BD for {p}"))
        x("insert into images(id,owner_id,region,verdict,created_at,deleted) values(?,?,'face','accepted',now(),0)", (f"img-{p}", p))
        x("insert into conversations(id,owner_id,created_at) values(?,?,now())", (f"conv-{p}", p))
        x("insert into messages(id,conversation_id,role,content,created_at) values(?,?,'patient',?,now())", (f"msg-{p}", f"conv-{p}", f"private question of {p}"))
        x("insert into quotes(id,patient_id,status,lines,total,created_at) values(?,?,'draft','[]',1,now())", (f"q-{p}", p))
        x("insert into consents(user_id,notice_sha256,accepted_at) values(?,?,now())", (p, "sha"))
        x("insert into consent_events(id,user_id,notice_sha256,notice_status,action,at) values(?,?,'sha','approved','accepted',now())", (f"ce-{p}", p))
        x("insert into notifications(id,user_id,kind,title,dedupe_key,created_at) values(?,?,'k','t',?,now())", (f"n-{p}", p, f"k-{p}"))
        x("insert into jobs(id,owner_id,task,status,created_at) values(?,?,'t','done',now())", (f"j-{p}", p))
        x("insert into appointment_requests(id,patient_id,reason,contact_method,status,created_at,updated_at) values(?,?,?,'app','requested',now(),now())", (f"ap-{p}", p, f"private reason of {p}"))
        x("insert into agent_traces(id,user_id,request_id,created_at,intent,provider,outcome) values(?,?,'r',now(),'ask','intents-2.0.0','answered')", (f"at-{p}", p))
        x("insert into agent_actions(id,user_id,trace_id,tool,args,args_sha256,summary,state,expires_at) values(?,?,?,'request_quote','{}','h','summary','proposed',now() + interval '15 minutes')",
          (f"aa-{p}", p, f"at-{p}"))
    x("insert into care_plans(id,patient_id,doctor_id,status,assessment,updated_at) values('cp-draft','p1','d1','draft','secret draft',now()),('cp-ok','p1','d1','approved','approved plan',now())")
    x("insert into legal_holds(id,patient_id,reason_code,placed_by,placed_at) values('lh1','p1','other','a1',now())")
    x("insert into training_runs(id,task,dataset_id,version_id,config,config_hash,plan,status,started_at,actor,device) values('tr1','t','d','v','{}','h','{}','done',now(),'a1','cpu')")
    x("insert into kb_articles(id,title,body,review_status,version) values('kb-ok','A','a','approved',1),('kb-dr','D','d','draft',1)")
    x("insert into price_items(id,name,unit_price,active) values('pi1','Peel',100,1)")
    x("insert into settings values('quote_config','{}')")
    x("insert into datasets values('ds1','{}','approved',now())")
    x("insert into dataset_versions values('dv1','ds1','{}',now())")
    x("insert into model_versions values('mv1','t','{}','research_only',now())")
    x("insert into model_events(id,model_id,task,at,actor,event,to_state) values('me1','mv1','t',now(),'a1','registered','experimental')")
    x("insert into field_corrections(id,field_id,document_id,before,after,status,reviewer,ts) values('fc1','f-p1','doc-p1','8','9','corrected','d1',now())")
    x("insert into audit values('au1',now(),'p1','login','','{}')")
    yield be, pg_server.url(name)
    be.close()
    pg_server.drop_database(name)


def ids(be, ctx, table, col="id"):
    return sorted(r[col] for r in be.q(f"select {col} from {table}", (), ctx))


def writes(be, ctx, sql, args=()):
    return be.x(sql, args, ctx)


# ---------------------------------------------------------------- default deny
@pytest.mark.parametrize("table", TABLES)
def test_no_identity_means_no_rows_and_no_writes_on_any_table(rls, table):
    be, _ = rls
    for ctx in (None, DbContext(), C("p1", "none"), C("p1", "bogus-role")):
        assert be.q(f"select count(*) c from {table}", (), ctx)[0]["c"] == 0


def test_an_unauthenticated_insert_is_refused_by_policy(rls):
    be, _ = rls
    for sql, args in (("insert into documents(id,owner_id,created_at,deleted) values('x','p1',now(),0)", ()), ("insert into users(id,role) values('evil','admin')", ()),
                      ("insert into audit values('z',now(),'p1','a','','{}')", ()), ("insert into notifications(id,user_id,created_at) values('zn','p1',now())", ())):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            be.x(sql, args, None)


def test_every_application_table_has_forced_row_level_security_and_at_least_one_policy(rls):
    be, url = rls
    with psycopg.connect(url) as c:
        rows = c.execute("select c.relname, c.relrowsecurity, c.relforcerowsecurity, (select count(*) from pg_policies p where p.schemaname='app' and p.tablename=c.relname) "
                         "from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='app' and c.relkind='r' and c.relname <> 'schema_migrations'").fetchall()
        assert {r[0] for r in rows} == set(TABLES), "a table was added without being covered here: give it RLS and add it to TABLES"
        assert all(r[1] and r[2] and r[3] >= 1 for r in rows), [r for r in rows if not (r[1] and r[2] and r[3] >= 1)]
        role = c.execute("select rolsuper, rolbypassrls, rolcreaterole, rolcreatedb from pg_roles where rolname='cosmo_app'").fetchone()
        assert role == (False, False, False, False)


# ---------------------------------------------------------------- patients
def test_a_patient_sees_only_their_own_records_everywhere(rls):
    be, _ = rls
    for t in ("documents", "images", "conversations", "quotes", "notifications", "jobs"):
        assert len(ids(be, P1, t)) == 1 and ids(be, P1, t)[0].endswith("p1"), t
    assert ids(be, P1, "doc_pages") == ["pg-p1"] and ids(be, P1, "doc_fields") == ["f-p1"] and ids(be, P1, "messages") == ["msg-p1"]
    assert ids(be, P1, "doc_facts") == ["fa-p1"] and ids(be, P1, "doc_medications") == ["md-p1"] and ids(be, P2, "doc_medications") == ["md-p2"]
    assert ids(be, P1, "consents", "user_id") == ["p1"] and ids(be, P1, "consent_events") == ["ce-p1"] and ids(be, P1, "users") == ["p1"]
    assert ids(be, P2, "documents") == ["doc-p2"] and ids(be, P2, "messages") == ["msg-p2"] and ids(be, P2, "care_plans") == []
    for t in ("audit", "datasets", "dataset_versions", "model_versions", "field_corrections"):
        assert be.q(f"select count(*) c from {t}", (), P1)[0]["c"] == 0, t


def test_a_patient_sees_only_approved_care_plans_and_approved_articles(rls):
    be, _ = rls
    assert ids(be, P1, "care_plans") == ["cp-ok"]
    assert ids(be, P1, "kb_articles") == ["kb-ok"] and ids(be, P1, "price_items") == ["pi1"] and ids(be, P1, "settings", "key") == ["quote_config"]


def test_a_patient_cannot_write_into_or_change_another_patients_records(rls):
    be, _ = rls
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into documents(id,owner_id,created_at,deleted) values('forged','p2',now(),0)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into messages(id,conversation_id,role,content,created_at) values('mx','conv-p2','patient','injected',now())")
    assert writes(be, P1, "update documents set filename='pwned' where id='doc-p2'") == 0                 # other rows are invisible: nothing matches
    assert writes(be, P1, "delete from documents where id='doc-p2'") == 0
    assert writes(be, P1, "delete from images where id='img-p2'") == 0
    assert writes(be, P1, "update notifications set read_at=now() where id='n-p2'") == 0
    assert writes(be, P1, "update documents set filename='mine' where id='doc-p1'") == 1               # their own are fine
    assert be.q("select filename from documents where id='doc-p2'", (), SYS)[0]["filename"] == "p2.pdf"


def test_a_patient_cannot_change_roles_accounts_or_other_privileged_data(rls):
    be, _ = rls
    assert writes(be, P1, "update users set role='admin' where id='p1'") == 0                           # not even their own role
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into users(id,role) values('mallory','admin')")
    assert writes(be, P1, "delete from users where id='p2'") == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into notifications(id,user_id,created_at) values('selfnote','p1',now())")  # notifications are created by the server only
    assert writes(be, P1, "delete from notifications where id='n-p1'") == 0
    assert writes(be, P1, "update notifications set read_at=now() where id='n-p1'") == 1                # but may mark their own read
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into quotes(id,patient_id,status,lines,total,created_at) values('qa','p1','approved','[]',1,now())")   # cannot self-approve
    assert writes(be, P1, "insert into quotes(id,patient_id,status,lines,total,created_at) values('qd','p1','draft','[]',1,now())") == 1
    assert writes(be, P1, "update quotes set status='approved' where id='qd'") == 0
    for sql in ("update kb_articles set review_status='approved' where id='kb-dr'", "insert into price_items(id,name) values('px','x')", "insert into care_plans(id,patient_id,status) values('cx','p1','approved')",
                "insert into datasets values('dx','{}','approved',now())", "update settings set value='x' where key='quote_config'"):
        try:
            assert writes(be, P1, sql) == 0
        except psycopg.errors.InsufficientPrivilege:
            pass


def test_appointment_requests_belong_to_the_patient_and_only_the_clinic_can_agree_a_time(rls):
    be, _ = rls
    assert ids(be, P1, "appointment_requests") == ["ap-p1"] and ids(be, P2, "appointment_requests") == ["ap-p2"]               # a patient sees only their own requests
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into appointment_requests(id,patient_id,reason,contact_method,status,created_at) values('ap-forged','p2','x','app','requested',now())")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into appointment_requests(id,patient_id,reason,contact_method,status,created_at) values('ap-self','p1','x','app','confirmed',now())")   # not a confirmed request
    assert writes(be, P1, "update appointment_requests set status='cancelled' where id='ap-p2'") == 0                          # another patient's request is invisible
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "update appointment_requests set status='confirmed', confirmed_for='tomorrow 10:00' where id='ap-p1'")   # a patient cannot confirm a time for themselves
    assert writes(be, P1, "delete from appointment_requests where id='ap-p1'") == 0                                             # and cannot erase the record
    assert writes(be, DOC, "update appointment_requests set status='confirmed', confirmed_for='Tuesday 4:30 pm' where id='ap-p1'") == 1   # the clinic agrees a time
    assert ids(be, DOC, "appointment_requests") == ["ap-p1", "ap-p2"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "update appointment_requests set status='cancelled' where id='ap-p1'")                                    # a confirmed request is no longer the patient's to rewrite
    assert writes(be, P2, "update appointment_requests set status='cancelled' where id='ap-p2'") == 1                          # but an open one can be withdrawn
    assert be.q("select count(*) c from appointment_requests", (), ADM)[0]["c"] == 0                                           # administrators read them only with the clinical flag
    assert ids(be, ADMR, "appointment_requests") == ["ap-p1", "ap-p2"]


def test_audit_is_append_only_and_cannot_be_forged_for_someone_else(rls):
    be, _ = rls
    assert writes(be, P1, "insert into audit values('a-own',now(),'p1','x','','{}')") == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into audit values('a-forged',now(),'p2','x','','{}')")
    for stmt in ("update audit set action='x'", "delete from audit"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            writes(be, SYS, stmt)                                                                         # not even the system can rewrite history through the app role
    assert be.q("select count(*) c from audit", (), P1)[0]["c"] == 0                                    # and a patient cannot read the trail


# ---------------------------------------------------------------- staff
def test_clinicians_read_clinical_records_but_not_private_conversations_or_other_domains(rls):
    be, _ = rls
    assert ids(be, DOC, "documents") == ["doc-p1", "doc-p2"] and ids(be, DOC, "images") == ["img-p1", "img-p2"] and ids(be, DOC, "doc_fields") == ["f-p1", "f-p2"]
    assert ids(be, DOC, "doc_facts") == ["fa-p1", "fa-p2"] and ids(be, DOC, "doc_medications") == ["md-p1", "md-p2"]
    assert ids(be, DOC, "care_plans") == ["cp-draft", "cp-ok"] and len(ids(be, DOC, "users")) == 4 and {"q-p1", "q-p2"} <= set(ids(be, DOC, "quotes"))
    for t in ("conversations", "messages", "consents", "consent_events", "notifications", "datasets", "dataset_versions", "model_versions", "audit"):
        assert be.q(f"select count(*) c from {t}", (), DOC)[0]["c"] == 0, t
    assert writes(be, DOC, "update documents set review_status='reviewed_by_clinician' where id='doc-p2'") == 1
    assert writes(be, DOC, "update quotes set status='approved' where id='q-p1'") == 1
    assert writes(be, DOC, "insert into care_plans(id,patient_id,doctor_id,status,updated_at) values('cp-new','p2','d1','draft',now())") == 1
    assert writes(be, DOC, "update users set role='admin' where id='p1'") == 0                          # clinicians cannot change accounts


def test_administrators_manage_accounts_and_the_ml_domain_but_do_not_read_clinical_records_by_default(rls):
    be, _ = rls
    assert len(ids(be, ADM, "users")) == 4 and be.q("select count(*) c from audit", (), ADM)[0]["c"] >= 1
    assert ids(be, ADM, "datasets") == ["ds1"] and ids(be, ADM, "model_versions") == ["mv1"] and len(ids(be, ADM, "jobs")) == 2
    for t in ("documents", "images", "doc_pages", "doc_fields", "doc_facts", "doc_medications", "care_plans", "conversations", "messages", "consents", "consent_events", "notifications"):
        assert be.q(f"select count(*) c from {t}", (), ADM)[0]["c"] == 0, t
    assert writes(be, ADM, "update users set disabled=1 where id='p2'") == 1                            # administrative function: allowed
    assert writes(be, ADM, "update users set disabled=0 where id='p2'") == 1
    assert writes(be, ADM, "update documents set review_status='x' where id='doc-p1'") == 0
    assert writes(be, ADM, "update kb_articles set review_status='approved' where id='kb-dr'") == 0   # administrators cannot approve clinical content


def test_the_admin_clinical_flag_is_the_only_way_for_administrators_to_read_records(rls):
    be, _ = rls
    assert ids(be, ADMR, "documents") == ["doc-p1", "doc-p2"] and {"cp-draft", "cp-ok"} <= set(ids(be, ADMR, "care_plans")) and ids(be, ADMR, "images") == ["img-p1", "img-p2"]
    assert be.q("select count(*) c from messages", (), ADMR)[0]["c"] == 0                              # conversations stay private even then


def test_the_system_identity_can_do_server_work(rls):
    be, _ = rls
    assert len(ids(be, SYS, "documents")) == 2 and len(ids(be, SYS, "users")) == 4
    assert writes(be, SYS, "insert into notifications(id,user_id,created_at) values('sys-n','p1',now())") == 1
    assert writes(be, SYS, "delete from notifications where id='sys-n'") == 1


# ---------------------------------------------------------------- forging attempts
def test_an_application_bug_or_injection_cannot_promote_itself_by_setting_the_identity_settings(rls):
    be, url = rls
    with be.tx(P1) as t:
        t.x("select set_config('app.role', 'system', true), set_config('app.user_id', '', true)")      # what an injected statement would try
        assert t.q("select count(*) c from documents")[0]["c"] == 0                                    # signature no longer matches: role none
        assert t.q("select app_private.cur_role() r")[0]["r"] == "none"
    with be.tx(P1) as t:
        t.x("select set_config('app.user_id', 'p2', true)")                                            # impersonate another patient with p1's signature
        assert t.q("select count(*) c from documents")[0]["c"] == 0 and t.q("select app_private.cur_user() u")[0]["u"] is None
    with be.tx(P1) as t:
        t.x("select set_config('app.admin_clinical', 'read', true)")
        assert t.q("select app_private.cur_role() r")[0]["r"] == "none"
    with psycopg.connect(url.replace("postgres@", "cosmo_app@"), autocommit=False) as raw:             # a raw connection that sets settings but has no signature
        raw.execute("select set_config('app.role','system',true), set_config('app.user_id','',true), set_config('app.ctx_sig','deadbeef',true)")
        assert raw.execute("select count(*) from app.documents").fetchone()[0] == 0


def test_a_backend_with_the_wrong_secret_is_denied_and_start_up_detects_it(rls, pg_server):
    be, url = rls
    wrong = PostgresBackend(url.replace("postgres@", "cosmo_app@"), retries=0, connect_timeout=3, context_secret="some-other-secret-" + "z" * 32)
    assert wrong.q("select count(*) c from documents", (), SYS)[0]["c"] == 0
    with pytest.raises(migrate.MigrationError, match="rejected the application's identity signature"):
        migrate.check_ctx_ready(wrong)
    wrong.close()
    migrate.check_ctx_ready(be)                                                                         # the right secret passes


def test_a_database_without_an_installed_secret_denies_everything(pg_server):
    name = pg_server.new_database("nosecret")
    try:
        migrate.apply(pg_server.url(name))
        be = PostgresBackend(pg_server.url(name, "cosmo_app"), retries=0, context_secret=CTX_SECRET)
        assert be.q("select app_private.ctx_ready() r", (), SYS)[0]["r"] is False
        with pytest.raises(migrate.MigrationError):
            migrate.check_ctx_ready(be)
        assert migrate.install_ctx_secret(pg_server.url(name), CTX_SECRET) is True and migrate.install_ctx_secret(pg_server.url(name), "another-" + "q" * 32) is False   # never replaced implicitly
        migrate.check_ctx_ready(be)
        assert migrate.install_ctx_secret(pg_server.url(name), "another-" + "q" * 32, replace=True) is True
        with pytest.raises(migrate.MigrationError):
            migrate.check_ctx_ready(be)                                                                 # rotation requires the application to be updated too
        be.close()
    finally:
        pg_server.drop_database(name)


def test_the_application_role_cannot_read_the_secret_or_replace_it(rls):
    be, _ = rls
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        be.q("select secret from app_private.ctx_secret", (), SYS)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        be.q("select app_private.set_ctx_secret('attacker-chosen-secret-0123456789abcdef')", (), SYS)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        be.x("insert into app_private.ctx_secret values (1, 'x')", (), SYS)


def test_the_sql_hmac_matches_the_standard_implementation_for_all_key_lengths(rls):
    be, _ = rls
    for key in (b"k", b"x" * 31, b"y" * 64, b"z" * 65, b"w" * 200, bytes(range(1, 100))):
        for msg in (b"", b"a", "u1\x1fpatient\x1fnone".encode(), b"\x00\xff" * 70):
            sql = be.q("select encode(app_private.hmac_sha256(%s::bytea, %s::bytea), 'hex') h".replace("%s", "?"), (key, msg), SYS)[0]["h"]
            assert sql == pyhmac.new(key, msg, hashlib.sha256).hexdigest(), (key[:3], msg[:3])


def test_consent_history_is_append_only_for_the_application_role_and_cannot_be_written_for_someone_else(rls):
    be, _ = rls
    assert writes(be, P1, "insert into consent_events(id,user_id,notice_sha256,notice_status,action,at) values('ce-new','p1','sha','approved','withdrawn',now())") == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, P1, "insert into consent_events(id,user_id,notice_sha256,notice_status,action,at) values('ce-forged','p2','sha','approved','withdrawn',now())")
    with pytest.raises(Exception):
        writes(be, P1, "update consent_events set action='accepted' where id='ce-new'")
    with pytest.raises(Exception):
        writes(be, P1, "delete from consent_events where id='ce-new'")


def test_legal_holds_are_visible_to_administrators_and_the_system_only_and_are_never_deleted(rls):
    be, _ = rls
    for ctx in (P1, P2, DOC):
        assert be.q("select count(*) c from legal_holds", (), ctx)[0]["c"] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            writes(be, ctx, "insert into legal_holds(id,patient_id,reason_code,placed_by,placed_at) values('lh-x','p1','other','x',now())")
    assert ids(be, ADM, "legal_holds") == ["lh1"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, ADM, "delete from legal_holds where id='lh1'")
    with pytest.raises(psycopg.errors.CheckViolation):
        writes(be, ADM, "insert into legal_holds(id,patient_id,reason_code,placed_by,placed_at) values('lh-y','p1','free text','a1',now())")


def test_audit_entries_can_only_be_purged_through_the_system_only_function(rls):
    be, _ = rls
    cutoff = "2999-01-01"
    for ctx in (P1, DOC, ADM):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            be.q("select app.purge_audit(?::timestamptz, 10)", (cutoff,), ctx)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, ADM, "delete from audit")
    assert be.q("select app.purge_audit(?::timestamptz, 10000) n", (cutoff,), SYS)[0]["n"] >= 0


def test_training_runs_are_visible_to_administrators_and_the_system_only_and_never_deleted(rls):
    be, _ = rls
    for ctx in (P1, P2, DOC):
        assert be.q("select count(*) c from training_runs", (), ctx)[0]["c"] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            writes(be, ctx, "insert into training_runs(id,task,config,config_hash,status,started_at) values('tr-x','t','{}','h','done',now())")
    assert ids(be, ADM, "training_runs") == ["tr1"]
    assert writes(be, ADM, "update training_runs set status='failed' where id='tr1'") == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writes(be, ADM, "delete from training_runs where id='tr1'")
    with pytest.raises(psycopg.errors.CheckViolation):
        writes(be, ADM, "update training_runs set status='whatever' where id='tr1'")


def test_extracted_facts_and_medicines_follow_the_document_they_were_read_from(rls):
    be, _ = rls
    # a patient cannot plant extracted content on another patient's document, nor change or remove it
    for sql in ("insert into doc_facts(id,document_id,kind,value) values('fa-forged','doc-p2','report_date','1 Jan 2020')",
                "insert into doc_medications(id,document_id,item_no,raw,entry) values('md-forged','doc-p2',9,'Tab Evil 1 g','{}')"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            writes(be, P1, sql)
    assert writes(be, P1, "update doc_medications set raw='changed' where id='md-p2'") == 0
    assert writes(be, P1, "delete from doc_medications where id='md-p2'") == 0
    assert writes(be, P1, "delete from doc_facts where id='fa-p2'") == 0
    # the review state is a closed vocabulary
    with pytest.raises(psycopg.errors.CheckViolation):
        writes(be, DOC, "update doc_medications set review_status='approved_by_magic' where id='md-p1'")
    assert writes(be, DOC, "update doc_medications set review_status='confirmed', reviewed_by='d1' where id='md-p1'") == 1
    assert be.q("select review_status from doc_medications where id='md-p1'", (), P1)[0]["review_status"] == "confirmed"
    # deleting a document removes what was read from it (nothing is left behind)
    assert writes(be, SYS, "insert into documents(id,owner_id,filename,status,created_at,deleted) values('doc-tmp','p1','t.pdf','processed',now(),0)") == 1
    assert writes(be, SYS, "insert into doc_facts(id,document_id,kind,value) values('fa-tmp','doc-tmp','report_date','x')") == 1
    assert writes(be, SYS, "insert into doc_medications(id,document_id,item_no,raw,entry) values('md-tmp','doc-tmp',0,'Tab X','{}')") == 1
    assert writes(be, SYS, "delete from documents where id='doc-tmp'") == 1
    assert be.q("select count(*) c from doc_facts where id='fa-tmp'", (), SYS)[0]["c"] == 0 and be.q("select count(*) c from doc_medications where id='md-tmp'", (), SYS)[0]["c"] == 0


def test_agent_traces_and_actions_belong_to_the_person_they_concern(rls):
    be, _ = rls
    assert ids(be, P1, "agent_traces") == ["at-p1"] and ids(be, P1, "agent_actions") == ["aa-p1"] and ids(be, P2, "agent_actions") == ["aa-p2"]
    assert ids(be, SYS, "agent_traces") == ["at-p1", "at-p2"] and ids(be, SYS, "agent_actions") == ["aa-p1", "aa-p2"]
    # clinicians see neither; administrators audit traces (they hold no message or record text) but never the proposed actions (their arguments are the person's own words)
    for t in ("agent_traces", "agent_actions"):
        assert be.q(f"select count(*) c from {t}", (), DOC)[0]["c"] == 0, t
    assert ids(be, ADM, "agent_traces") == ["at-p1", "at-p2"] and be.q("select count(*) c from agent_actions", (), ADM)[0]["c"] == 0
    # a person can record their own trace and propose their own action, never for somebody else
    assert writes(be, P1, "insert into agent_traces(id,user_id,created_at) values('at-new','p1',now())") == 1
    assert writes(be, P1, "insert into agent_actions(id,user_id,tool,args,args_sha256,state) values('aa-new','p1','request_quote','{}','h','proposed')") == 1
    for sql in ("insert into agent_traces(id,user_id,created_at) values('at-forged','p2',now())", "insert into agent_actions(id,user_id,tool,args,args_sha256,state) values('aa-forged','p2','t','{}','h','proposed')"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            writes(be, P1, sql)
    for ctx in (DOC, ADM):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            writes(be, ctx, "insert into agent_traces(id,user_id,created_at) values('at-staff','p1',now())")
    # the state of one's own action can move (within the closed vocabulary); another person's cannot, and nobody edits a trace or deletes through the application role
    assert writes(be, P1, "update agent_actions set state='cancelled' where id='aa-p1'") == 1
    assert writes(be, P1, "update agent_actions set state='executed' where id='aa-p2'") == 0
    with pytest.raises(psycopg.errors.CheckViolation):
        writes(be, P1, "update agent_actions set state='approved_by_magic' where id='aa-new'")
    assert writes(be, P1, "update agent_traces set outcome='x' where id='at-p1'") == 0
    assert writes(be, P1, "delete from agent_traces where id='at-p1'") == 0 and writes(be, P1, "delete from agent_actions where id='aa-p1'") == 0
    assert writes(be, SYS, "delete from agent_traces where id in ('at-new')") == 1 and writes(be, SYS, "delete from agent_actions where id in ('aa-new')") == 1      # erasure and retention are server actions


def test_model_history_is_visible_to_administrators_and_the_server_and_can_never_be_rewritten(rls):
    be, _ = rls
    assert ids(be, ADM, "model_events") == ["me1"] and ids(be, SYS, "model_events") == ["me1"]
    for ctx in (P1, DOC):
        assert be.q("select count(*) c from model_events", (), ctx)[0]["c"] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            writes(be, ctx, "insert into model_events(id,model_id,task,event) values('me-x','mv1','t','approved')")
    assert writes(be, ADM, "insert into model_events(id,model_id,task,at,actor,event) values('me2','mv1','t',now(),'a1','evaluation_started')") == 1
    for ctx in (ADM, SYS):
        for sql in ("update model_events set event='rewritten' where id='me1'", "delete from model_events where id='me1'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                writes(be, ctx, sql)                                                                  # not even the server can edit or remove history through the application role

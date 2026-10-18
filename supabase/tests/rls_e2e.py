"""End-to-end authentication / row-level-security / private-storage / isolation tests against a REAL Supabase project.

Needs two environment variables (never committed, never printed):
    SUPABASE_ACCESS_TOKEN   a personal/project access token
    SUPABASE_PROJECT_REF    the project ref (a DEVELOPMENT project; this script creates and deletes its own test users and rows)

    python supabase/tests/rls_e2e.py            # auto-detects the migration state
Exit code 0 only if every expectation for the detected state holds.

State 'baseline' (0001 only): the script reports which ATTACKS succeed (these are the flaws 0002 fixes) and checks isolation basics.
State 'hardened' (0002 applied): every attack must be blocked, every legitimate action must work."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

TOKEN, REF = os.environ.get("SUPABASE_ACCESS_TOKEN"), os.environ.get("SUPABASE_PROJECT_REF")
if not TOKEN or not REF:
    sys.exit("Set SUPABASE_ACCESS_TOKEN and SUPABASE_PROJECT_REF.")
URL = f"https://{REF}.supabase.co"
results: list[tuple[str, bool, str]] = []


def http(method, url, headers=None, body=None, raw=False):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
            return r.status, (b if raw else (json.loads(b) if b else None))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, (b if raw else json.loads(b))
        except Exception:
            return e.code, b.decode("utf-8", "replace")[:200]


def mgmt(path, method="GET", body=None):
    return http(method, "https://api.supabase.com" + path, {"Authorization": f"Bearer {TOKEN}"}, body)


def sql(q):
    s, b = mgmt(f"/v1/projects/{REF}/database/query", "POST", {"query": q})
    if s >= 300:
        raise RuntimeError(f"SQL failed: {str(b)[:300]}")
    return b


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


s, keys = mgmt(f"/v1/projects/{REF}/api-keys?reveal=true")
ANON = next(k["api_key"] for k in keys if k["name"] == "anon" and k["type"] == "legacy")
SERVICE = next(k["api_key"] for k in keys if k["name"] == "service_role" and k["type"] == "legacy")


def rest(method, path, jwt=None, body=None, prefer="return=representation"):
    h = {"apikey": ANON, "Authorization": f"Bearer {jwt or ANON}", "Prefer": prefer}
    return http(method, f"{URL}/rest/v1/{path}", h, body)


def admin_api(method, path, body=None):
    return http(method, f"{URL}/auth/v1/admin/{path}", {"apikey": SERVICE, "Authorization": f"Bearer {SERVICE}"}, body)


def login(email, pw):
    s, b = http("POST", f"{URL}/auth/v1/token?grant_type=password", {"apikey": ANON}, {"email": email, "password": pw})
    assert s == 200, f"login failed {s}"
    return b["access_token"]


def storage(method, path, jwt, body=None, ctype="application/pdf", service=False):
    key = SERVICE if service else ANON
    h = {"apikey": key, "Authorization": f"Bearer {SERVICE if service else jwt or ANON}", "Content-Type": ctype}
    return http(method, f"{URL}/storage/v1/{path}", h, body, raw=True)


def ok_rows(resp):
    s, b = resp
    return s in (200, 201) and isinstance(b, list) and len(b) > 0


def blocked(resp):
    s, b = resp
    return s in (400, 401, 403, 404, 409, 422) or (s in (200, 201, 204) and (b == [] or b is None))      # 400/409: rejected by a CHECK or FK constraint


# ------------------------------------------------------------------ state detection
hardened = bool(sql("select to_regclass('public.care_plans') is not null as x")[0]["x"])
print(f"== project {REF}: migration state = {'HARDENED (0002 applied)' if hardened else 'BASELINE (0001 only)'}")

# ------------------------------------------------------------------ users (real Auth users, real JWTs)
tag = uuid.uuid4().hex[:8]
PW = "Tst-" + uuid.uuid4().hex + "!"
users = {}
for role in ("patient_a", "patient_b", "doctor", "admin", "meta_admin"):
    email = f"cosmo-rls-{role}-{tag}@example.com"
    meta = {"full_name": role, "role": "admin"} if role == "meta_admin" else {"full_name": role}      # tries to smuggle role=admin through metadata
    s, b = admin_api("POST", "users", {"email": email, "password": PW, "email_confirm": True, "user_metadata": meta})
    assert s in (200, 201), f"user create failed {s} {b}"
    users[role] = {"id": b["id"], "email": email}
A, B, DOC, ADM = users["patient_a"]["id"], users["patient_b"]["id"], users["doctor"]["id"], users["admin"]["id"]
try:
    if not hardened:   # baseline has no sign-up trigger: create profiles the way an operator would
        for role, r in (("patient_a", "patient"), ("patient_b", "patient"), ("doctor", "doctor"), ("admin", "admin"), ("meta_admin", "patient")):
            sql(f"insert into profiles (id, full_name, role) values ('{users[role]['id']}', '{role}', '{r}') on conflict (id) do update set role='{r}'")
    else:
        sql(f"update profiles set role='doctor' where id='{DOC}'; update profiles set role='admin' where id='{ADM}';")
    jwt = {r: login(u["email"], PW) for r, u in users.items()}

    # ------------------------------------------------------------------ seed (as the database owner)
    docA, docB, convA, convB, imgA, imgB, quoteA, kbd, kba, pi = (str(uuid.uuid4()) for _ in range(10))
    sql(f"""
    insert into documents (id, owner_id, filename, storage_path, mime, sha256, doc_type, page_count, status, processing_version, review_status) values
      ('{docA}','{A}','a.pdf','{A}/a.pdf','application/pdf','x','laboratory_report',1,'processed','v','pending_clinician_review'),
      ('{docB}','{B}','b.pdf','{B}/b.pdf','application/pdf','y','laboratory_report',1,'processed','v','pending_clinician_review');
    insert into doc_fields (document_id, name, value, unit) values ('{docA}','Ferritin','8','ng/mL'), ('{docB}','TSH','2.1','mIU/L');
    insert into images (id, owner_id, region, storage_path, verdict) values ('{imgA}','{A}','face','{A}/i.jpg','accepted'), ('{imgB}','{B}','face','{B}/i.jpg','accepted');
    insert into conversations (id, owner_id) values ('{convA}','{A}'), ('{convB}','{B}');
    insert into messages (conversation_id, role, content) values ('{convA}','patient','secret A'), ('{convB}','patient','secret B');
    insert into quotes (id, patient_id, status, lines, total, currency) values ('{quoteA}','{A}','draft','[]',100,'INR');
    insert into price_items (id, name, unit_price, currency, active, approved_by) values ('{pi}','Consult',500,'INR',true,'{DOC}');
    insert into kb_articles (id, title, body, review_status) values ('{kbd}','Draft','d','draft');
    insert into kb_articles (id, title, body, review_status, reviewed_by, reviewed_at, content_sha256, source_note, attestation)
      values ('{kba}','Approved','a','approved','{DOC}', now(), '{"b" * 64}', 'seed: leaflet page 1 (test)', '{{"statement":"seed"}}'::jsonb);
    insert into audit (actor, action, target) values ('seed','test.seed','x');
    """)
    if hardened:
        planA_ok, planA_draft = str(uuid.uuid4()), str(uuid.uuid4())
        sql(f"""
        insert into care_plans (id, patient_id, doctor_id, status, assessment) values ('{planA_ok}','{A}','{DOC}','approved','approved plan for A');
        insert into care_plans (id, patient_id, doctor_id, status, assessment) values ('{planA_draft}','{A}','{DOC}','draft','DRAFT for A');
        insert into jobs (owner_id, task, status) values ('{A}','image_check','completed'), ('{B}','image_check','completed');
        insert into field_corrections (document_id, status, reviewer) values ('{docA}','corrected','{DOC}');
        insert into settings (key, value) values ('quote_config','{{"tax_percent":18}}'), ('secret_setting','{{"x":1}}') on conflict (key) do nothing;
        """)
    storage("POST", f"object/patient-documents/{A}/a.pdf", None, b"%PDF-A", service=True)
    storage("POST", f"object/patient-documents/{B}/b.pdf", None, b"%PDF-B", service=True)

    # ------------------------------------------------------------------ ISOLATION & READS (must hold in BOTH states)
    r = rest("GET", "documents?select=id,owner_id", jwt["patient_a"])
    check("patient A sees exactly their own documents", ok_rows(r) and {x["id"] for x in r[1]} == {docA}, str(r)[:120])
    check("patient B cannot read patient A's document by id", blocked(rest("GET", f"documents?id=eq.{docA}", jwt["patient_b"])))
    check("patient A cannot read patient B's fields", blocked(rest("GET", f"doc_fields?document_id=eq.{docB}", jwt["patient_a"])))
    check("patient A cannot read patient B's photos", blocked(rest("GET", f"images?id=eq.{imgB}", jwt["patient_a"])))
    check("patient A cannot read patient B's conversation or messages", blocked(rest("GET", f"conversations?id=eq.{convB}", jwt["patient_a"])) and blocked(rest("GET", f"messages?conversation_id=eq.{convB}", jwt["patient_a"])))
    check("patient A reads their own messages", ok_rows(rest("GET", f"messages?conversation_id=eq.{convA}", jwt["patient_a"])))
    check("patient cannot read audit, datasets or model registry", all(blocked(rest("GET", f"{t}?select=*", jwt["patient_a"])) for t in ("audit", "datasets", "dataset_versions", "model_versions")))
    check("anonymous caller (no user JWT) reads nothing", all(blocked(rest("GET", f"{t}?select=*")) for t in ("documents", "images", "conversations", "quotes", "profiles")))
    check("doctor reads a patient's documents and photos", ok_rows(rest("GET", f"documents?id=eq.{docA}", jwt["doctor"])) and ok_rows(rest("GET", f"images?id=eq.{imgA}", jwt["doctor"])))
    check("doctor and admin cannot read patient conversations (chat privacy)", blocked(rest("GET", f"messages?conversation_id=eq.{convA}", jwt["doctor"])) and blocked(rest("GET", f"messages?conversation_id=eq.{convA}", jwt["admin"])))
    check("admin reads the audit trail", ok_rows(rest("GET", "audit?select=*", jwt["admin"])))
    check("patient sees only approved KB articles", {x["id"] for x in rest("GET", "kb_articles?select=id", jwt["patient_a"])[1]} == {kba})
    check("patient reads the approved price list", ok_rows(rest("GET", "price_items?select=id", jwt["patient_a"])))
    check("patient cannot change their own role", not ok_rows(rest("PATCH", f"profiles?id=eq.{A}", jwt["patient_a"], {"role": "admin"})) and sql(f"select role from profiles where id='{A}'")[0]["role"] == "patient")
    check("patient cannot grant themselves the doctor/admin role via a new profile row", blocked(rest("POST", "profiles", jwt["patient_a"], {"id": str(uuid.uuid4()), "role": "admin"})))

    if hardened:
        rpc = [rest("POST", f"rpc/{fn}", jwt["patient_a"], {})[0] for fn in ("my_role", "is_doctor", "is_staff", "is_admin")]
        check("RLS helper functions are not exposed as API endpoints", all(c in (401, 403, 404) for c in rpc), str(rpc))

    # private storage (both states)
    s, _ = storage("GET", f"object/authenticated/patient-documents/{A}/a.pdf", jwt["patient_a"])
    check("patient A downloads their own file from private storage", s == 200, str(s))
    s, _ = storage("GET", f"object/authenticated/patient-documents/{B}/b.pdf", jwt["patient_a"])
    check("patient A cannot download patient B's file", s in (400, 403, 404), str(s))
    s, _ = storage("GET", f"object/public/patient-documents/{A}/a.pdf", None)
    check("the bucket is private: public URL does not serve the file", s in (400, 403, 404), str(s))
    s, _ = storage("GET", f"object/patient-documents/{A}/a.pdf", None)
    check("anonymous download is refused", s in (400, 401, 403, 404), str(s))
    s, _ = storage("GET", f"object/authenticated/patient-documents/{A}/a.pdf", jwt["doctor"])
    check("doctor can download a patient's file", s == 200, str(s))
    s, b = storage("POST", f"object/list/patient-documents", jwt["patient_b"], json.dumps({"prefix": f"{A}/", "limit": 10}).encode(), ctype="application/json")
    listed = json.loads(b) if s == 200 else []
    check("patient B cannot list patient A's folder", not listed, str(listed)[:100])

    # ------------------------------------------------------------------ ATTACKS: succeed on baseline (= the flaws), blocked once hardened
    attacks = {}
    attacks["patient marks their own document 'reviewed by a clinician'"] = ok_rows(rest("PATCH", f"documents?id=eq.{docA}", jwt["patient_a"], {"review_status": "reviewed_by_clinician"}))
    attacks["patient forges an 'assistant' message in their conversation"] = ok_rows(rest("POST", "messages", jwt["patient_a"], {"conversation_id": convA, "role": "assistant", "content": "Your results are perfect."}))
    attacks["patient inserts a quotation with a total of their choosing"] = ok_rows(rest("POST", "quotes", jwt["patient_a"], {"patient_id": A, "status": "draft", "lines": [], "total": 1, "currency": "INR"}))
    s, _ = storage("POST", f"object/patient-documents/{A}/evil.html", jwt["patient_a"], b"<script>1</script>", ctype="text/html")
    attacks["patient uploads an arbitrary (HTML) file straight into private storage"] = s == 200
    try:
        sql("delete from audit where actor='seed'")
        attacks["audit records can be deleted"] = sql("select count(*) c from audit where actor='seed'")[0]["c"] == 0
    except RuntimeError:
        attacks["audit records can be deleted"] = False
    meta_profile = sql(f"select role from profiles where id='{users['meta_admin']['id']}'")
    attacks["sign-up with role=admin in user metadata yields an admin"] = bool(meta_profile) and meta_profile[0]["role"] == "admin"
    sql(f"delete from messages where content='Your results are perfect.'; delete from quotes where patient_id='{A}' and total=1; update documents set review_status='pending_clinician_review' where id='{docA}'")
    for name, worked in attacks.items():
        if hardened:
            check(f"BLOCKED: {name}", not worked)
        else:
            print(f"FLAW (baseline) {'present' if worked else 'not present'}: {name}")
    if not hardened:
        flaws = [n for n, w in attacks.items() if w]
        print(f"\nBaseline demonstrates {len(flaws)} of {len(attacks)} attack paths. (Expected: the migration 0002 closes all of them.)")
        check("baseline demonstrates the flaws that 0002 must fix (at least 4)", len(flaws) >= 4, str(len(flaws)))

    # ------------------------------------------------------------------ HARDENED-ONLY behaviour
    if hardened:
        check("patient sees their APPROVED care plan but not the draft", {x["id"] for x in rest("GET", "care_plans?select=id", jwt["patient_a"])[1]} == {planA_ok})
        check("patient B sees no care plans", rest("GET", "care_plans?select=id", jwt["patient_b"])[1] == [])
        check("doctor sees drafts and approved plans", len(rest("GET", f"care_plans?patient_id=eq.{A}", jwt["doctor"])[1]) == 2)
        newplan = {"patient_id": B, "doctor_id": DOC, "status": "draft", "assessment": "x"}
        check("doctor can author a care plan", ok_rows(rest("POST", "care_plans", jwt["doctor"], newplan)))
        check("doctor cannot author a plan in another doctor's name", blocked(rest("POST", "care_plans", jwt["doctor"], {**newplan, "patient_id": A, "doctor_id": ADM, "status": "approved"})))
        check("doctor can approve a plan", ok_rows(rest("PATCH", f"care_plans?id=eq.{planA_draft}", jwt["doctor"], {"status": "approved", "doctor_id": DOC})))
        check("ADMIN cannot author a care plan", blocked(rest("POST", "care_plans", jwt["admin"], {"patient_id": A, "doctor_id": ADM, "status": "approved", "assessment": "x"})))
        check("ADMIN cannot approve or edit a care plan", blocked(rest("PATCH", f"care_plans?id=eq.{planA_ok}", jwt["admin"], {"assessment": "tampered"})))
        check("patient cannot edit their own care plan", blocked(rest("PATCH", f"care_plans?id=eq.{planA_ok}", jwt["patient_a"], {"assessment": "tampered"})))
        check("doctor can mark a document reviewed", ok_rows(rest("PATCH", f"documents?id=eq.{docA}", jwt["doctor"], {"review_status": "reviewed_by_clinician"})))
        check("doctor cannot rewrite other document columns (column privileges)", not ok_rows(rest("PATCH", f"documents?id=eq.{docA}", jwt["doctor"], {"filename": "hacked.pdf"})))
        check("doctor can correct an extracted value", ok_rows(rest("PATCH", f"doc_fields?document_id=eq.{docA}", jwt["doctor"], {"value": "9", "review_status": "corrected"})))
        check("admin cannot correct an extracted value", blocked(rest("PATCH", f"doc_fields?document_id=eq.{docA}", jwt["admin"], {"value": "1"})))
        check("doctor can mark a photo reviewed (only as themselves)", ok_rows(rest("PATCH", f"images?id=eq.{imgA}", jwt["doctor"], {"clinician_review": "reviewed", "reviewed_by": DOC})))
        check("doctor cannot attribute a photo review to someone else", blocked(rest("PATCH", f"images?id=eq.{imgB}", jwt["doctor"], {"clinician_review": "reviewed", "reviewed_by": ADM})))
        check("doctor cannot approve a quote as another user", blocked(rest("PATCH", f"quotes?id=eq.{quoteA}", jwt["doctor"], {"status": "approved", "approved_by": ADM})))
        check("doctor can approve a quote as themselves", ok_rows(rest("PATCH", f"quotes?id=eq.{quoteA}", jwt["doctor"], {"status": "approved", "approved_by": DOC})))
        check("patient cannot approve their own quote", blocked(rest("PATCH", f"quotes?id=eq.{quoteA}", jwt["patient_a"], {"status": "approved", "approved_by": A})))
        check("KB: doctor can add a DRAFT article but not insert an approved one", ok_rows(rest("POST", "kb_articles", jwt["doctor"], {"title": "t", "body": "b", "review_status": "draft"})) and
              blocked(rest("POST", "kb_articles", jwt["doctor"], {"title": "t2", "body": "b", "review_status": "approved", "reviewed_by": DOC})))
        full = {"review_status": "approved", "reviewed_by": DOC, "reviewed_at": "2026-10-18T00:00:00Z", "content_sha256": "a" * 64, "source_note": "Clinic leaflet p3 (test)", "attestation": {"statement": "reviewed (test)"}}
        check("KB: approval without the attestation record is refused", blocked(rest("PATCH", f"kb_articles?id=eq.{kbd}", jwt["doctor"], {"review_status": "approved", "reviewed_by": DOC})))
        check("KB: approval must be attributed to the approving doctor", blocked(rest("PATCH", f"kb_articles?id=eq.{kbd}", jwt["doctor"], {**full, "reviewed_by": ADM})))
        check("KB: a complete, self-attributed approval is accepted", ok_rows(rest("PATCH", f"kb_articles?id=eq.{kbd}", jwt["doctor"], full)))
        ok_rows(rest("PATCH", f"kb_articles?id=eq.{kbd}", jwt["doctor"], {"body": "edited after approval"}))
        row = sql(f"select review_status, reviewed_by, content_sha256, source_note, version from kb_articles where id='{kbd}'")[0]
        check("KB: editing approved text returns it to draft and clears the approval record (database trigger)",
              row["review_status"] == "draft" and row["reviewed_by"] is None and row["content_sha256"] == "" and row["source_note"] == "" and row["version"] >= 2, row)
        check("KB: a doctor cannot re-approve in the same statement as a content change", ok_rows(rest("PATCH", f"kb_articles?id=eq.{kbd}", jwt["doctor"], {**full, "body": "sneaky"})) and
              sql(f"select review_status from kb_articles where id='{kbd}'")[0]["review_status"] == "draft")
        check("KB: admin cannot approve clinical content", blocked(rest("PATCH", f"kb_articles?id=eq.{kba}", jwt["admin"], {"review_status": "draft"})))
        nA, nB = str(uuid.uuid4()), str(uuid.uuid4())
        sql(f"insert into notifications (id, user_id, kind, title, dedupe_key) values ('{nA}','{A}','t','for A','k1'), ('{nB}','{B}','t','for B','k1');")
        check("notifications: each user sees only their own", {x["id"] for x in rest("GET", "notifications?select=id", jwt["patient_a"])[1]} == {nA})
        check("notifications: staff and admin cannot read patients' notifications", rest("GET", "notifications?select=id", jwt["doctor"])[1] == [] and rest("GET", "notifications?select=id", jwt["admin"])[1] == [])
        check("notifications: a user can mark their own read", ok_rows(rest("PATCH", f"notifications?id=eq.{nA}", jwt["patient_a"], {"read_at": "2026-10-18T00:00:00Z"})))
        check("notifications: nobody can edit another user's notification", not ok_rows(rest("PATCH", f"notifications?id=eq.{nB}", jwt["patient_a"], {"read_at": "2026-10-18T00:00:00Z"})))
        check("notifications: only read_at is editable (title, owner)", blocked(rest("PATCH", f"notifications?id=eq.{nA}", jwt["patient_a"], {"title": "forged"})) and blocked(rest("PATCH", f"notifications?id=eq.{nA}", jwt["patient_a"], {"user_id": B})))
        check("notifications: users cannot create or delete them", blocked(rest("POST", "notifications", jwt["patient_a"], {"user_id": A, "kind": "x", "title": "self-made"})) and not ok_rows(rest("DELETE", f"notifications?id=eq.{nA}", jwt["patient_a"])))
        try:
            sql(f"insert into notifications (user_id, kind, title, dedupe_key) values ('{A}','t','dup','k1')")
            dup_refused = False
        except RuntimeError:
            dup_refused = True
        check("notifications: the same event cannot be stored twice for one user (dedupe key)", dup_refused)
        check("jobs: each patient sees only their own", {x["owner_id"] for x in rest("GET", "jobs?select=owner_id", jwt["patient_a"])[1]} == {A})
        check("jobs: admin sees all, patients cannot create jobs", len(rest("GET", "jobs?select=id", jwt["admin"])[1]) >= 2 and blocked(rest("POST", "jobs", jwt["patient_a"], {"owner_id": A, "task": "x", "status": "queued"})))
        check("settings: any user reads quote_config but not other keys", [x["key"] for x in rest("GET", "settings?select=key", jwt["patient_a"])[1]] == ["quote_config"])
        check("settings: patients cannot change quote_config", blocked(rest("PATCH", "settings?key=eq.quote_config", jwt["patient_a"], {"value": {"tax_percent": 0}})))
        check("corrections: doctor can record one (as themselves, not training-eligible)", ok_rows(rest("POST", "field_corrections", jwt["doctor"], {"document_id": docA, "status": "corrected", "reviewer": DOC})) and
              blocked(rest("POST", "field_corrections", jwt["doctor"], {"document_id": docA, "status": "corrected", "reviewer": DOC, "training_eligible": True})))
        check("corrections: patients cannot read them", blocked(rest("GET", "field_corrections?select=*", jwt["patient_a"])))
        check("corrections: only an admin can set governance fields, and only with a reference + de-identification",
              blocked(rest("PATCH", f"field_corrections?document_id=eq.{docA}", jwt["doctor"], {"training_eligible": True, "governance_ref": "G-1", "deidentified": True})) and
              blocked(rest("PATCH", f"field_corrections?document_id=eq.{docA}", jwt["admin"], {"training_eligible": True, "governance_ref": "", "deidentified": False})) and
              ok_rows(rest("PATCH", f"field_corrections?document_id=eq.{docA}&status=eq.corrected", jwt["admin"], {"training_eligible": True, "governance_ref": "G-1", "deidentified": True})))
        try:
            sql("update audit set action='tampered' where actor='seed'"); upd = True
        except RuntimeError as e:
            upd = "append-only" not in str(e)
        try:
            sql("truncate audit"); trunc = True
        except RuntimeError as e:
            trunc = "append-only" not in str(e)
        check("audit: UPDATE and TRUNCATE are rejected by the trigger", not upd and not trunc)
        check("sign-up with role=admin in metadata produced a PATIENT", sql(f"select role from profiles where id='{users['meta_admin']['id']}'")[0]["role"] == "patient")
        check("patient cannot read other users' profiles; staff can", {x["id"] for x in rest("GET", "profiles?select=id", jwt["patient_a"])[1]} == {A} and len(rest("GET", "profiles?select=id", jwt["doctor"])[1]) >= 4)
        check("patient can update only their own full_name", ok_rows(rest("PATCH", f"profiles?id=eq.{A}", jwt["patient_a"], {"full_name": "A. Patient"})) and blocked(rest("PATCH", f"profiles?id=eq.{B}", jwt["patient_a"], {"full_name": "pwned"})))
        s, _ = storage("POST", f"object/patient-images/{A}/ok.jpg", None, b"\xff\xd8\xff", ctype="image/jpeg", service=True)
        s2, _ = storage("POST", f"object/patient-images/{A}/bad.html", None, b"<html>", ctype="text/html", service=True)
        check("bucket enforces allowed mime types even for the service role", s == 200 and s2 in (400, 415), f"{s},{s2}")
        s, _ = storage("POST", f"object/patient-documents/{A}/mine.pdf", jwt["patient_a"], b"%PDF-1", ctype="application/pdf")
        check("patients cannot write to storage directly (uploads go through the validated API)", s in (400, 401, 403), str(s))
        s, _ = storage("DELETE", f"object/patient-documents/{A}/a.pdf", jwt["patient_a"])
        s3, _ = storage("GET", f"object/authenticated/patient-documents/{A}/a.pdf", jwt["patient_a"])
        check("patients cannot delete their stored files directly", s3 == 200)
finally:
    # clean up everything this run created (rows first: some foreign keys have no cascade)
    ids = ",".join(f"'{u['id']}'" for u in users.values())
    try:
        if hardened:
            sql(f"""
            delete from field_corrections where reviewer in ({ids}) or document_id in (select id from documents where owner_id in ({ids}));
            delete from care_plans where patient_id in ({ids}) or doctor_id in ({ids});
            delete from jobs where owner_id in ({ids});
            delete from settings where key = 'secret_setting';
            """)
        sql(f"""
        delete from quotes where patient_id in ({ids}) or approved_by in ({ids}) or created_by in ({ids});
        delete from price_items where approved_by in ({ids});
        delete from kb_articles where reviewed_by in ({ids}) or title in ('Draft','Approved','t','t2');
        delete from images where owner_id in ({ids});
        """)
        if hardened:   # the audit table is append-only; the database owner lifts the trigger just to remove this run's own seed rows
            sql("alter table audit disable trigger audit_no_change; delete from audit where actor='seed'; alter table audit enable trigger audit_no_change;")
        else:
            sql("delete from audit where actor='seed'")
    except RuntimeError as e:
        print("cleanup warning:", str(e)[:150])
    for folder in (users["patient_a"]["id"], users["patient_b"]["id"]):
        for f in ("a.pdf", "b.pdf", "evil.html", "ok.jpg", "mine.pdf"):
            storage("DELETE", f"object/patient-documents/{folder}/{f}", None, service=True)
            storage("DELETE", f"object/patient-images/{folder}/{f}", None, service=True)
    for u in users.values():
        admin_api("DELETE", f"users/{u['id']}")

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed  (state: {'hardened' if hardened else 'baseline'})")
sys.exit(1 if failed else 0)

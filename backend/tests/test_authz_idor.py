"""Server-side authorization and insecure-direct-object-reference checks across every resource endpoint that takes an id.

One patient (patient1) owns a document, a photo, a conversation, a quotation, a notification and a job. Every other identity (another patient, a doctor where the resource is private
to the patient, an administrator without clinical access, an unauthenticated caller, a disabled account) must get a refusal, never the data. These are API-level checks of the backend's
own rules; the database policies (tests/test_pg_rls.py) are a second layer, and the same file runs against PostgreSQL with COSMO_TEST_DB=postgres."""
import json

import pytest
from fastapi.testclient import TestClient

from app import db, storage
from app.main import app
from app.services import notify
from tests.test_api import lab_pdf

MARK = "PRIVATE-MARKER-71ab"
PDF = lab_pdf([f"Ferritin     8     ng/mL     15 - 150  L", MARK])


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(client, u):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


@pytest.fixture(scope="module")
def world(client):
    h = H(client, "patient1")
    doc = client.post("/api/documents", files={"file": ("r.pdf", PDF)}, headers=h)
    assert doc.status_code == 200, doc.text
    did = doc.json()["id"]
    fid = client.get(f"/api/documents/{did}", headers=h).json()["fields"][0]["id"]
    ref = storage.put("patient1", b"\xff\xd8\xff\xe0 photo", "jpg")
    iid = db.uid()
    db.x("INSERT INTO images(id,owner_id,region,stored_path,verdict,report,created_at,deleted,scan_status,size_bytes) VALUES(?,?,?,?,?,?,?,0,'unscanned',10)",
         (iid, "patient1", "face", ref, "accepted", "{}", db.now()))
    cid = client.post("/api/conversations", headers=h).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": f"my private question {MARK}"}, headers=h)
    qid = db.uid()
    db.x("INSERT INTO quotes(id,patient_id,status,lines,total,currency,notes,created_by,approved_by,created_at,updated_at) VALUES(?,?,'approved','[]',1,'INR',?,'patient1','doctor1',?,?)",
         (qid, "patient1", "note " + MARK, db.now(), db.now()))
    notify.send("patient1", "k", "private title " + MARK, "body", "/x", f"idor-{db.uid()}")
    nid = db.q1("SELECT id FROM notifications WHERE user_id='patient1' ORDER BY created_at DESC LIMIT 1")["id"]
    jid = db.uid()
    db.x("INSERT INTO jobs(id,owner_id,task,status,created_at) VALUES(?,?,'document_process','done',?)", (jid, "patient1", db.now()))
    yield dict(did=did, fid=fid, iid=iid, cid=cid, qid=qid, nid=nid, jid=jid)
    storage.delete(ref)
    for table, col, val in (("quotes", "id", qid), ("jobs", "id", jid), ("notifications", "id", nid), ("images", "id", iid)):      # leave nothing that changes other suites' expectations
        db.x(f"DELETE FROM {table} WHERE {col}=?", (val,))
    db.x("DELETE FROM messages WHERE conversation_id=?", (cid,)); db.x("DELETE FROM conversations WHERE id=?", (cid,))


# (method, path template, json body, what the owner gets)  -- every endpoint that takes an id of a patient-owned resource
OWNED = [
    ("GET", "/api/documents/{did}", None), ("GET", "/api/documents/{did}/file", None), ("GET", "/api/documents/{did}/thumbnail", None), ("GET", "/api/documents/{did}/explanation", None),
    ("POST", "/api/documents/{did}/fields/{fid}/flag", {"reason": "wrong"}), ("POST", "/api/documents/{did}/link", None), ("DELETE", "/api/documents/{did}", None),
    ("GET", "/api/images/{iid}/file", None), ("POST", "/api/images/{iid}/link", None), ("DELETE", "/api/images/{iid}", None),
    ("GET", "/api/conversations/{cid}", None), ("POST", "/api/conversations/{cid}/messages", {"text": "hello"}), ("DELETE", "/api/conversations/{cid}", None),
    ("POST", "/api/notifications/{nid}/read", None), ("GET", "/api/quotes/{qid}/pdf", None),
    ("GET", "/api/v1/jobs/{jid}", None), ("POST", "/api/v1/jobs/{jid}/cancel", None),
]
# resources that are private to the patient even from clinicians
PATIENT_ONLY = {"/api/conversations/{cid}", "/api/conversations/{cid}/messages", "/api/notifications/{nid}/read", "/api/v1/jobs/{jid}", "/api/v1/jobs/{jid}/cancel"}
# deletion is the patient's own action
PATIENT_ACTS = {("DELETE", "/api/documents/{did}"), ("DELETE", "/api/images/{iid}"), ("DELETE", "/api/conversations/{cid}"), ("POST", "/api/documents/{did}/fields/{fid}/flag")}


def call(client, method, path, body, headers, w):
    url = path.format(**w)
    return client.request(method, url, json=body, headers=headers) if body is not None else client.request(method, url, headers=headers)


def refused(r):
    return r.status_code in (401, 403, 404) and MARK not in r.text


@pytest.mark.parametrize("method,path,body", OWNED)
def test_another_patient_is_refused_on_every_resource(client, world, method, path, body):
    r = call(client, method, path, body, H(client, "patient2"), world)
    assert refused(r), (method, path, r.status_code, r.text[:200])


@pytest.mark.parametrize("method,path,body", OWNED)
def test_unauthenticated_callers_are_refused_on_every_resource(client, world, method, path, body):
    r = call(client, method, path, body, {}, world)
    assert r.status_code == 401 and MARK not in r.text, (method, path, r.status_code)


@pytest.mark.parametrize("method,path,body", [o for o in OWNED if o[1] in PATIENT_ONLY or (o[0], o[1]) in PATIENT_ACTS])
def test_clinicians_and_administrators_cannot_use_resources_that_are_the_patients_own(client, world, method, path, body):
    for who in ("doctor1", "admin1"):
        r = call(client, method, path, body, H(client, who), world)
        assert refused(r), (who, method, path, r.status_code, r.text[:200])


@pytest.mark.parametrize("path", ["/api/documents/{did}", "/api/documents/{did}/file", "/api/documents/{did}/thumbnail", "/api/images/{iid}/file"])
def test_administrators_have_no_clinical_access_by_default_and_gain_read_only_access_when_the_operator_grants_it(client, world, path, monkeypatch):
    monkeypatch.setenv("COSMO_ADMIN_CLINICAL_ACCESS", "none")
    r = call(client, "GET", path, None, H(client, "admin1"), world)
    assert refused(r), (path, r.status_code)
    monkeypatch.setenv("COSMO_ADMIN_CLINICAL_ACCESS", "read")
    assert call(client, "GET", path, None, H(client, "admin1"), world).status_code == 200
    assert call(client, "DELETE", "/api/documents/{did}", None, H(client, "admin1"), world).status_code in (403, 404)       # read access never includes changing


def test_clinicians_can_read_the_clinical_record_but_the_owner_is_unaffected(client, world):
    d = H(client, "doctor1")
    assert client.get(f"/api/documents/{world['did']}", headers=d).status_code == 200
    assert client.get(f"/api/images/{world['iid']}/file", headers=d).status_code == 200
    assert client.get(f"/api/clinic/cases/patient1", headers=d).status_code == 200
    assert client.get(f"/api/conversations/{world['cid']}", headers=d).status_code in (403, 404)                    # conversations are private to the patient
    own = H(client, "patient1")
    assert client.get(f"/api/documents/{world['did']}", headers=own).status_code == 200 and client.get(f"/api/conversations/{world['cid']}", headers=own).status_code == 200


def test_staff_endpoints_refuse_patients_and_wrong_staff_roles(client, world):
    p, d, a = H(client, "patient1"), H(client, "doctor1"), H(client, "admin1")
    for method, path, body in [("GET", "/api/clinic/cases/patient1", None), ("GET", "/api/clinic/care-plans/patient1", None), ("POST", "/api/clinic/documents/{did}/review", {"status": "reviewed"}),
                               ("PATCH", "/api/clinic/fields/{fid}", {"value": "9"}), ("POST", "/api/clinic/images/{iid}/review", {"decision": "ok"})]:
        assert call(client, method, path, body, p, world).status_code == 403, path
    for method, path, body in [("GET", "/api/admin/users", None), ("POST", "/api/admin/users/patient1/role", {"role": "admin"}), ("POST", "/api/admin/users/patient1/disabled", {"disabled": True}),
                               ("POST", "/api/admin/users/patient1/reset-password", {"password": "a-long-enough-pass"}), ("GET", "/api/admin/audit", None), ("POST", "/api/admin/retention/purge", None),
                               ("POST", "/api/admin/legal-holds", {"patient_id": "patient1", "reason_code": "other"}), ("GET", "/api/admin/retention/plan", None)]:
        for who in (p, d):
            assert call(client, method, path, body, who, world).status_code == 403, (method, path)
    for method, path, body in [("POST", "/api/clinic/care-plans/{qid}/approve", None), ("PUT", "/api/clinic/care-plans/patient1", {"assessment": "x", "findings": [], "stages": [], "notes": "", "recommended_item_ids": []}),
                               ("POST", "/api/clinic/kb/x/approve", None), ("POST", "/api/clinic/quotes/{qid}/approve", None)]:
        assert call(client, method, path, body, a, world).status_code == 403, (method, path)             # administrators do not author or approve clinical content


def test_a_patient_cannot_pass_another_patients_id_to_list_endpoints(client, world):
    h = H(client, "patient2")
    for url in ("/api/documents?patient_id=patient1", "/api/images?patient_id=patient1", "/api/history?patient_id=patient1", "/api/quotes?patient_id=patient1"):
        r = client.get(url, headers=h)
        assert MARK not in r.text and world["did"] not in r.text and world["iid"] not in r.text, url
        if r.status_code == 200:
            assert (r.json() if isinstance(r.json(), list) else r.json().get("items", [])) in ([], {}) or all(x.get("owner_id", "patient2") in ("patient2", None) for x in (r.json() if isinstance(r.json(), list) else []))


def test_a_disabled_account_loses_access_at_once(client, world):
    h = H(client, "patient1")
    assert client.get(f"/api/documents/{world['did']}", headers=h).status_code == 200
    db.x("UPDATE users SET disabled=1 WHERE id='patient1'")
    try:
        for method, path, body in OWNED[:6]:
            r = call(client, method, path, body, h, world)
            assert r.status_code == 403 and MARK not in r.text, (method, path)
    finally:
        db.x("UPDATE users SET disabled=0 WHERE id='patient1'")
    assert client.get(f"/api/documents/{world['did']}", headers=h).status_code == 200


def test_the_roles_in_a_token_or_a_request_cannot_be_chosen_by_the_caller(client, world):
    h = H(client, "patient2")
    for extra in ({"X-User-Role": "admin"}, {"X-User-Id": "patient1"}, {"X-Role": "doctor"}):
        assert refused(client.get(f"/api/documents/{world['did']}", headers={**h, **extra}))
    assert client.get("/api/me", headers=h).json()["role"] == "patient"
    assert refused(client.get(f"/api/documents/{world['did']}?owner_id=patient2&role=admin", headers=h))
    forged = h["Authorization"][:-4] + "AAAA"
    assert client.get("/api/me", headers={"Authorization": forged}).status_code == 401


def test_resource_ids_are_not_guessable():
    ids = {db.uid() for _ in range(200)}
    assert len(ids) == 200 and all(len(i) >= 16 and not i.isdigit() for i in ids)

"""Retention and deletion framework: nothing without an operator-chosen period, dry run by default, legal holds, bounded and idempotent, file/row coordination, minimal audit."""
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, retention, storage
from app.config import UPLOAD_DIR
from app.main import app
from scripts import retention as cli

DAY = 86400
P1, P2 = "ret_p1", "ret_p2"
ALL_ENV = [f"COSMO_RETENTION_{c.name.upper()}_DAYS" for c in retention.CATEGORIES] + ["COSMO_CONV_RETENTION_DAYS"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(client, u):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


@pytest.fixture(autouse=True)
def clean(monkeypatch, client):
    for k in ALL_ENV:
        monkeypatch.delenv(k, raising=False)
    for p in (P1, P2):
        db.insert_ignore("users", {"id": p, "name": p, "role": "patient", "password_hash": "", "credentials": "", "verified": 0, "disabled": 0})
    yield
    for t, c in (("messages", None), ("conversations", "owner_id"), ("notifications", "user_id"), ("jobs", "owner_id"), ("doc_pages", None), ("doc_fields", None),
                 ("documents", "owner_id"), ("images", "owner_id"), ("quotes", "patient_id"), ("care_plans", "patient_id")):
        if c:
            db.x(f"DELETE FROM {t} WHERE {c} IN (?,?)", (P1, P2))
    release_all()
    db.x("DELETE FROM users WHERE id IN (?,?)", (P1, P2))


def release_all():
    """Holds are never deleted (the history of holds is a record); tests release them."""
    db.x("UPDATE legal_holds SET released_by='test', released_at=? WHERE released_at IS NULL", (db.now(),))


def ago(days):
    return db.Epoch(time.time() - days * DAY)


def conv(owner, days):
    cid = db.uid()
    db.x("INSERT INTO conversations(id,owner_id,created_at) VALUES(?,?,?)", (cid, owner, ago(days)))
    db.x("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,'patient','private words',?)", (db.uid(), cid, ago(days)))
    return cid


def count(table, where="1=1", args=()):
    return db.q1(f"SELECT COUNT(*) n FROM {table} WHERE {where}", args)["n"]


def doc(owner, days):
    ref = storage.put(owner, b"%PDF- fake", "pdf")
    did = db.uid()
    db.x("INSERT INTO documents(id,owner_id,filename,stored_path,status,created_at,deleted) VALUES(?,?,?,?,?,?,0)", (did, owner, "lab.pdf", ref, "processed", ago(days)))
    db.x("INSERT INTO doc_pages(id,document_id,page_no,text) VALUES(?,?,1,'text')", (db.uid(), did))
    db.x("INSERT INTO doc_fields(id,document_id,name,value) VALUES(?,?,'Ferritin','8')", (db.uid(), did))
    return did, ref


# ---------------------------------------------------------------- nothing is purged without a chosen period
def test_without_configuration_everything_is_kept_and_reported_as_not_configured():
    conv(P1, 4000); doc(P1, 4000)
    rep = retention.run(dry_run=False)
    assert all(not c["configured"] and c["purged"] == 0 and "not configured" in c["note"] for c in rep["categories"])
    assert count("conversations", "owner_id=?", (P1,)) == 1 and count("documents", "owner_id=? AND deleted=0", (P1,)) == 1


@pytest.mark.parametrize("value", ["0", "-5", "x", "40000", "1.5"])
def test_invalid_periods_are_refused_not_guessed(monkeypatch, value):
    monkeypatch.setenv("COSMO_RETENTION_DOCUMENTS_DAYS", value)
    with pytest.raises(retention.ConfigError):
        retention.configured_days()


def test_the_earlier_conversation_setting_name_still_works(monkeypatch):
    monkeypatch.setenv("COSMO_CONV_RETENTION_DAYS", "45")
    assert retention.configured_days()["conversations"] == 45
    monkeypatch.setenv("COSMO_RETENTION_CONVERSATIONS_DAYS", "10")
    assert retention.configured_days()["conversations"] == 10


# ---------------------------------------------------------------- dry run and execution
def test_a_dry_run_changes_nothing_and_a_real_run_removes_only_what_is_old(monkeypatch):
    monkeypatch.setenv("COSMO_RETENTION_CONVERSATIONS_DAYS", "30")
    monkeypatch.setenv("COSMO_RETENTION_DOCUMENTS_DAYS", "90")
    old_c, new_c = conv(P1, 40), conv(P1, 5)
    old_d, old_ref = doc(P1, 120)
    new_d, new_ref = doc(P1, 10)
    rep = retention.run()                                                                                  # dry run is the default
    by = {c["category"]: c for c in rep["categories"]}
    assert rep["dry_run"] is True and by["conversations"]["eligible"] >= 1 and by["documents"]["eligible"] >= 1
    assert count("conversations", "owner_id=?", (P1,)) == 2 and Path(old_ref).exists()
    rep = retention.run(dry_run=False)
    assert {c["category"]: c["purged"] for c in rep["categories"]}["conversations"] >= 1
    assert count("conversations", "id=?", (old_c,)) == 0 and count("messages", "conversation_id=?", (old_c,)) == 0 and count("conversations", "id=?", (new_c,)) == 1
    assert count("messages", "conversation_id=?", (new_c,)) == 1
    assert not Path(old_ref).exists() and Path(new_ref).exists()                                           # the stored file went with the row
    gone = db.q1("SELECT deleted, stored_path, filename FROM documents WHERE id=?", (old_d,))
    assert gone == {"deleted": 1, "stored_path": "", "filename": "deleted"}
    assert count("doc_pages", "document_id=?", (old_d,)) == 0 and count("doc_fields", "document_id=?", (old_d,)) == 0 and count("doc_pages", "document_id=?", (new_d,)) == 1
    again = retention.run(dry_run=False)                                                                   # idempotent
    assert {c["category"]: c["purged"] for c in again["categories"]}["conversations"] == 0 and {c["category"]: c["purged"] for c in again["categories"]}["documents"] == 0
    storage.delete(new_ref)


def test_other_categories_are_purged_by_their_own_periods(monkeypatch):
    for k in ("NOTIFICATIONS", "JOBS", "QUOTES", "CARE_PLANS", "IMAGES"):
        monkeypatch.setenv(f"COSMO_RETENTION_{k}_DAYS", "30")
    with db.context.as_system():
        db.x("INSERT INTO notifications(id,user_id,kind,title,dedupe_key,created_at) VALUES('rn-old',?,'k','t','a',?),('rn-new',?,'k','t','b',?)", (P1, ago(60), P1, ago(1)))
    db.x("INSERT INTO jobs(id,owner_id,task,status,created_at) VALUES('rj-old',?,'t','done',?),('rj-new',?,'t','done',?)", (P1, ago(60), P1, ago(1)))
    db.x("INSERT INTO quotes(id,patient_id,status,lines,total,created_at) VALUES('rq-old',?,'draft','[]',1,?),('rq-new',?,'draft','[]',1,?)", (P1, ago(60), P1, ago(1)))
    db.x("INSERT INTO care_plans(id,patient_id,doctor_id,status,assessment,updated_at) VALUES('rc-old',?,'d','draft','x',?),('rc-new',?,'d','draft','x',?)", (P1, ago(60), P1, ago(1)))
    ref = storage.put(P1, b"jpg", "jpg")
    db.x("INSERT INTO images(id,owner_id,region,stored_path,verdict,created_at,deleted) VALUES('ri-old',?,'face',?,'accepted',?,0)", (P1, ref, ago(60)))
    retention.run(dry_run=False)
    for t, o, n in (("notifications", "rn-old", "rn-new"), ("jobs", "rj-old", "rj-new"), ("quotes", "rq-old", "rq-new"), ("care_plans", "rc-old", "rc-new")):
        assert count(t, "id=?", (o,)) == 0 and count(t, "id=?", (n,)) == 1, t
    assert db.q1("SELECT deleted, stored_path FROM images WHERE id='ri-old'") == {"deleted": 1, "stored_path": ""} and not Path(ref).exists()


# ---------------------------------------------------------------- legal holds
def test_a_legal_hold_protects_one_patient_in_every_category_until_released(monkeypatch):
    monkeypatch.setenv("COSMO_RETENTION_CONVERSATIONS_DAYS", "30"); monkeypatch.setenv("COSMO_RETENTION_DOCUMENTS_DAYS", "30")
    held_c, free_c = conv(P1, 90), conv(P2, 90)
    held_d, held_ref = doc(P1, 90)
    free_d, free_ref = doc(P2, 90)
    h = retention.place_hold(P1, "legal_proceeding", "admin1")
    rep = retention.run(dry_run=False)
    by = {c["category"]: c for c in rep["categories"]}
    assert by["conversations"]["held_excluded"] >= 1
    assert count("conversations", "id=?", (held_c,)) == 1 and count("conversations", "id=?", (free_c,)) == 0
    assert count("documents", "id=? AND deleted=0", (held_d,)) == 1 and Path(held_ref).exists() and not Path(free_ref).exists()
    assert retention.release_hold(h["id"], "admin1") and not retention.release_hold(h["id"], "admin1")
    retention.run(dry_run=False)
    assert count("conversations", "id=?", (held_c,)) == 0 and not Path(held_ref).exists()


def test_any_active_hold_pauses_audit_purging(monkeypatch):
    monkeypatch.setenv("COSMO_RETENTION_AUDIT_DAYS", "30")
    db.x("INSERT INTO audit(id,ts,actor,action,target,detail) VALUES('ra-old',?,'x','t.old','','{}')", (ago(90),))
    h = retention.place_hold(P1, "other", "admin1")
    rep = retention.run(dry_run=False)
    assert {c["category"]: c for c in rep["categories"]}["audit"]["purged"] == 0 and count("audit", "id='ra-old'") == 1
    retention.release_hold(h["id"], "admin1")
    retention.run(dry_run=False)
    assert count("audit", "id='ra-old'") == 0


def test_hold_validation_and_idempotence():
    with pytest.raises(ValueError):
        retention.place_hold(P1, "because I said so", "admin1")
    with pytest.raises(LookupError):
        retention.place_hold("nobody", "other", "admin1")
    with pytest.raises(LookupError):
        retention.place_hold("doctor1", "other", "admin1")                                                   # holds are for patients
    a, b = retention.place_hold(P1, "other", "admin1"), retention.place_hold(P1, "legal_proceeding", "admin1")
    assert a["id"] == b["id"] and count("legal_holds", "patient_id=? AND released_at IS NULL", (P1,)) == 1


# ---------------------------------------------------------------- bounded, resumable, coordinated
def test_runs_are_bounded_and_the_next_run_continues(monkeypatch):
    monkeypatch.setenv("COSMO_RETENTION_CONVERSATIONS_DAYS", "30")
    for _ in range(5):
        conv(P1, 60)
    first = retention.run(dry_run=False, batch=2, max_batches=1)
    c = {x["category"]: x for x in first["categories"]}["conversations"]
    assert c["purged"] == 2 and "next run continues" in c["stopped"] and count("conversations", "owner_id=?", (P1,)) == 3
    while count("conversations", "owner_id=?", (P1,)):
        retention.run(dry_run=False, batch=2, max_batches=1)
    with pytest.raises(ValueError):
        retention.run(batch=0)
    with pytest.raises(ValueError):
        retention.run(batch=5000)


def test_a_file_that_cannot_be_removed_keeps_its_row_and_is_retried(monkeypatch):
    monkeypatch.setenv("COSMO_RETENTION_DOCUMENTS_DAYS", "30")
    did, ref = doc(P1, 90)
    real = storage.delete

    def failing(r, strict=False):
        if r == ref:
            raise storage.StorageError("storage is unreachable")
        return real(r, strict=strict)
    monkeypatch.setattr(storage, "delete", failing)
    rep = retention.run(dry_run=False)
    d = {c["category"]: c for c in rep["categories"]}["documents"]
    assert d["failed"] == 1 and d["purged"] == 0
    assert db.q1("SELECT deleted, stored_path FROM documents WHERE id=?", (did,)) == {"deleted": 0, "stored_path": ref} and Path(ref).exists() and count("doc_pages", "document_id=?", (did,)) == 1
    monkeypatch.setattr(storage, "delete", real)
    retention.run(dry_run=False)
    assert db.q1("SELECT deleted FROM documents WHERE id=?", (did,))["deleted"] == 1 and not Path(ref).exists()


def test_strict_delete_treats_a_missing_file_as_done_but_not_other_failures(tmp_path):
    storage.delete(str(UPLOAD_DIR / "x" / "never-existed.pdf"), strict=True)
    with pytest.raises(storage.StorageError):
        storage.delete(str(tmp_path / "outside.pdf"), strict=True)                                          # outside the private directory: refuse, do not guess


def test_job_input_files_are_removed_only_inside_the_private_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("COSMO_RETENTION_JOBS_DAYS", "30")
    inside = Path(storage.put(P1, b"x", "bin"))
    outside = tmp_path / "keep.bin"; outside.write_bytes(b"x")
    db.x("INSERT INTO jobs(id,owner_id,task,status,input_path,created_at) VALUES('rj1',?,'t','done',?,?),('rj2',?,'t','done',?,?)", (P1, str(inside), ago(60), P1, str(outside), ago(60)))
    retention.run(dry_run=False)
    assert not inside.exists() and outside.exists() and count("jobs", "id IN ('rj1','rj2')") == 0


def test_audit_entries_about_a_run_contain_counts_only(monkeypatch):
    monkeypatch.setenv("COSMO_RETENTION_CONVERSATIONS_DAYS", "30")
    cid = conv(P1, 60)
    retention.run(dry_run=False)
    rows = db.q("SELECT target, detail FROM audit WHERE action='retention.purge' ORDER BY ts DESC LIMIT 5")
    assert rows and all(set(json.loads(r["detail"])) <= {"purged", "failed", "days"} for r in rows)
    assert cid not in json.dumps(rows) and P1 not in json.dumps(rows)


def test_old_audit_entries_are_removed_in_batches_and_recent_ones_stay(monkeypatch):
    monkeypatch.setenv("COSMO_RETENTION_AUDIT_DAYS", "30")
    release_all()
    for i in range(5):
        db.x("INSERT INTO audit(id,ts,actor,action,target,detail) VALUES(?,?,'x','t.old','','{}')", (f"rao{i}", ago(90)))
    db.x("INSERT INTO audit(id,ts,actor,action,target,detail) VALUES('ran',?,'x','t.new','','{}')", (ago(1),))
    retention.run(dry_run=False, batch=2, max_batches=1)
    assert count("audit", "id LIKE 'rao%'") == 3
    retention.run(dry_run=False, batch=2, max_batches=10)
    assert count("audit", "id LIKE 'rao%'") == 0 and count("audit", "id='ran'") == 1


# ---------------------------------------------------------------- orphan files
def test_orphan_files_are_found_after_a_grace_period_and_only_deleted_on_request():
    ref = Path(storage.put(P1, b"stray", "pdf"))
    kept_ref = Path(storage.put(P1, b"kept", "pdf"))
    db.x("INSERT INTO documents(id,owner_id,filename,stored_path,created_at,deleted) VALUES('ro1',?,'a',?,?,0)", (P1, str(kept_ref), ago(1)))
    assert ref not in retention.orphan_files()                                                              # younger than the grace period: may be an upload in flight
    found = retention.orphan_files(grace_seconds=0, now=time.time() + 5)
    assert ref in found and kept_ref not in found and ref.exists()
    assert retention.delete_orphans([ref]) == 1 and not ref.exists() and kept_ref.exists()
    storage.delete(str(kept_ref))


# ---------------------------------------------------------------- erasure on request
def test_erasure_is_deferred_while_a_legal_hold_is_active(client):
    h = H(client, "patient2")
    db.insert_ignore("users", {"id": "patient2", "name": "Demo Two", "role": "patient", "password_hash": "x", "credentials": "", "verified": 0, "disabled": 0})
    hold = retention.place_hold("patient2", "regulatory_inquiry", "admin1")
    try:
        r = client.post("/api/me/delete-my-data", headers=h)
        assert r.status_code == 409 and "contact the clinic" in r.json()["detail"] and "hold" not in r.text.lower()
        assert db.q1("SELECT 1 x FROM audit WHERE action='patient.erasure_deferred' AND target='patient2'")
    finally:
        retention.release_hold(hold["id"], "admin1")
    assert client.post("/api/me/delete-my-data", headers=h).status_code == 200


# ---------------------------------------------------------------- admin API and command line
def test_admin_endpoints_default_to_a_dry_run_and_need_explicit_confirmation(client, monkeypatch):
    monkeypatch.setenv("COSMO_RETENTION_CONVERSATIONS_DAYS", "30")
    cid = conv(P1, 60)
    a, d, p = H(client, "admin1"), H(client, "doctor1"), H(client, "patient1")
    for who in (d, p):
        assert client.post("/api/admin/retention/purge", headers=who).status_code == 403 and client.get("/api/admin/retention/plan", headers=who).status_code == 403
    plan = client.get("/api/admin/retention/plan", headers=a).json()
    assert {c["category"]: c for c in plan["categories"]}["conversations"]["eligible"] >= 1
    dry = client.post("/api/admin/retention/purge", headers=a)
    assert dry.status_code == 200 and dry.json()["dry_run"] is True and count("conversations", "id=?", (cid,)) == 1
    assert client.post("/api/admin/retention/purge", json={"dry_run": False}, headers=a).status_code == 422
    assert client.post("/api/admin/retention/purge", json={"dry_run": False, "confirm": "yes"}, headers=a).status_code == 422 and count("conversations", "id=?", (cid,)) == 1
    done = client.post("/api/admin/retention/purge", json={"dry_run": False, "confirm": "DELETE"}, headers=a)
    assert done.status_code == 200 and count("conversations", "id=?", (cid,)) == 0
    monkeypatch.setenv("COSMO_RETENTION_NOTIFICATIONS_DAYS", "abc")
    assert client.post("/api/admin/retention/purge", headers=a).status_code == 422


def test_hold_endpoints_are_admin_only_and_validated(client):
    a, d = H(client, "admin1"), H(client, "doctor1")
    db.insert_ignore("users", {"id": P1, "name": P1, "role": "patient", "password_hash": "", "credentials": "", "verified": 0, "disabled": 0})
    assert client.post("/api/admin/legal-holds", json={"patient_id": P1, "reason_code": "other"}, headers=d).status_code == 403
    assert client.post("/api/admin/legal-holds", json={"patient_id": P1, "reason_code": "free text reason"}, headers=a).status_code == 422
    assert client.post("/api/admin/legal-holds", json={"patient_id": "nobody", "reason_code": "other"}, headers=a).status_code == 404
    hid = client.post("/api/admin/legal-holds", json={"patient_id": P1, "reason_code": "other"}, headers=a).json()["id"]
    assert [x["id"] for x in client.get("/api/admin/legal-holds", headers=a).json()["holds"]] == [hid]
    assert client.get("/api/admin/legal-holds", headers=d).status_code == 403
    assert client.post(f"/api/admin/legal-holds/{hid}/release", headers=a).status_code == 200 and client.post(f"/api/admin/legal-holds/{hid}/release", headers=a).status_code == 404
    assert db.q1("SELECT 1 x FROM audit WHERE action='legal_hold.place'") and db.q1("SELECT 1 x FROM audit WHERE action='legal_hold.release'")


def test_the_command_line_is_a_dry_run_unless_explicitly_confirmed(monkeypatch, capsys):
    monkeypatch.setenv("COSMO_RETENTION_CONVERSATIONS_DAYS", "30")
    cid = conv(P1, 60)
    assert cli.main(["plan"]) == 0 and cli.main(["run"]) == 0 and count("conversations", "id=?", (cid,)) == 1
    assert cli.main(["run", "--execute"]) == 2 and count("conversations", "id=?", (cid,)) == 1
    assert cli.main(["run", "--execute", "--confirm", "DELETE"]) == 0 and count("conversations", "id=?", (cid,)) == 0
    out = capsys.readouterr().out
    assert cid not in out and P1 not in out                                                               # counts only
    monkeypatch.setenv("COSMO_RETENTION_JOBS_DAYS", "zero")
    assert cli.main(["plan"]) == 2

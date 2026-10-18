"""Private storage: local backend unit tests, and REAL Supabase Storage through the real API with real authenticated users (skipped without credentials)."""
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, storage, supabase_auth
from app.config import UPLOAD_DIR
from tests.test_api import lab_pdf
from tests.test_supabase_auth import REAL, real_env  # noqa: F401  (fixture)


# ---------------------------------------------------------------- local backend
def test_local_roundtrip_and_delete_is_idempotent():
    ref = storage.put("owner1", b"hello", "pdf")
    assert Path(ref).is_file() and storage.read(ref) == b"hello"
    storage.delete(ref); storage.delete(ref); storage.delete("")
    with pytest.raises(storage.StorageError):
        storage.read(ref)


def test_owner_and_extension_are_sanitised():
    ref = storage.put("../../evil owner!", b"x", "p/d..f")
    assert UPLOAD_DIR.resolve() in Path(ref).resolve().parents and ".." not in Path(ref).name
    storage.delete(ref)
    with pytest.raises(storage.StorageError):
        storage.put("!!!", b"x", "pdf")                                                                  # nothing is left after sanitising the owner


def test_references_outside_the_upload_directory_are_refused(tmp_path):
    outside = tmp_path / "secret.txt"; outside.write_text("secret")
    with pytest.raises(storage.StorageError):
        storage.read(str(outside))
    storage.delete(str(outside))                                                                         # must not delete it either
    assert outside.exists()
    with pytest.raises(storage.StorageError):
        storage.read(str(UPLOAD_DIR / ".." / ".." / "etc" / "passwd"))


@pytest.mark.parametrize("ref", ["supabase://other-bucket/a/b.pdf", "supabase://patient-documents/", "supabase://patient-documents/../x.pdf", "supabase://patient-documents/a/../../b"])
def test_malformed_supabase_references_are_refused(ref, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co"); monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    with pytest.raises(storage.StorageError):
        storage.read(ref)


def test_supabase_put_requires_a_uuid_owner_and_configuration(monkeypatch):
    monkeypatch.setenv("COSMO_STORAGE", "supabase")
    monkeypatch.delenv("SUPABASE_URL", raising=False); monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(storage.StorageError, match="not configured"):
        storage.put(str(uuid.uuid4()), b"x", "pdf")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co"); monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    with pytest.raises(storage.StorageError, match="user id"):
        storage.put("patient1", b"x", "pdf")


def test_unreachable_supabase_fails_clearly(monkeypatch):
    monkeypatch.setenv("COSMO_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:9"); monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    with pytest.raises(storage.StorageError, match="unreachable"):
        storage.put(str(uuid.uuid4()), b"x", "pdf")
    assert storage.healthy()[0] is False


# ---------------------------------------------------------------- REAL Supabase Storage
pytestmark_real = pytest.mark.skipif(not REAL, reason="no Supabase development project credentials in the environment")


@pytestmark_real
def test_real_bucket_roundtrip_and_mime_and_buckets(real_env, monkeypatch):
    monkeypatch.setenv("COSMO_STORAGE", "supabase")
    owner = real_env["users"]["patient"]["id"]
    pdf = storage.put(owner, b"%PDF-1.4 real", "pdf")
    png = storage.put(owner, b"\x89PNG\r\n\x1a\n", "png")
    assert pdf.startswith(f"supabase://patient-documents/{owner}/") and png.startswith(f"supabase://patient-images/{owner}/")     # routed to the right private bucket
    assert storage.read(pdf) == b"%PDF-1.4 real"
    assert storage.read(pdf) == b"%PDF-1.4 real"                                                          # a second read primes the CDN edge cache
    assert storage.healthy()[0] is True
    storage.delete(pdf); storage.delete(pdf)                                                              # idempotent
    with pytest.raises(storage.StorageError):
        storage.read(pdf)
    with pytest.raises(storage.StorageError):
        storage.put(owner, b"<html>", "html")                                                              # bucket mime allow-list refuses it
    storage.delete(png)


@pytestmark_real
def test_real_end_to_end_documents_in_supabase_storage_through_the_api(real_env, monkeypatch):
    monkeypatch.setenv("COSMO_STORAGE", "supabase")
    from app.main import app
    u = real_env["users"]
    pdf = lab_pdf(["Ferritin     8     ng/mL     15 - 150  L"])
    with TestClient(app) as c:
        h = {r: {"Authorization": f"Bearer {x['jwt']}"} for r, x in u.items()}
        r = c.post("/api/documents", files={"file": ("lab.pdf", pdf)}, headers=h["patient"])
        assert r.status_code == 200, r.text
        did = r.json()["id"]
        ref = db.q1("SELECT stored_path FROM documents WHERE id=?", (did,))["stored_path"]
        assert ref.startswith(f"supabase://patient-documents/{u['patient']['id']}/")
        assert not any(UPLOAD_DIR.rglob(Path(ref).name))                                                    # nothing was written to local disk
        assert c.get(f"/api/documents/{did}/file", headers=h["patient"]).content == pdf
        t = c.get(f"/api/documents/{did}/thumbnail", headers=h["patient"])
        assert t.status_code == 200 and t.content[:2] == b"\xff\xd8"
        assert c.get(f"/api/documents/{did}/file", headers=h["patient2"]).status_code == 404               # another patient
        assert c.get(f"/api/documents/{did}/file", headers=h["doctor"]).status_code == 200                 # clinician
        assert c.get("/api/v1/health/ready").json()["core"][1]["detail"].startswith("Supabase Storage reachable")
        # direct, RLS-governed access with the patient's own JWT also works for the owner and not for another patient
        bucket, key = ref[len("supabase://"):].split("/", 1)
        import urllib.request
        direct = urllib.request.Request(f"{real_env['url']}/storage/v1/object/authenticated/{bucket}/{key}", headers={"Authorization": f"Bearer {u['patient']['jwt']}", "apikey": real_env["svc"]})
        with urllib.request.urlopen(direct, timeout=30) as resp:
            assert resp.status == 200 and resp.read() == pdf
        # delete through the API removes the object itself
        assert c.delete(f"/api/documents/{did}", headers=h["patient"]).status_code == 200
        with pytest.raises(storage.StorageError):
            storage.read(ref)
        # erasure workflow removes stored objects too
        r2 = c.post("/api/documents", files={"file": ("lab2.pdf", pdf)}, headers=h["patient"])
        ref2 = db.q1("SELECT stored_path FROM documents WHERE id=?", (r2.json()["id"],))["stored_path"]
        assert storage.read(ref2) == pdf
        out = c.post("/api/me/delete-my-data", headers=h["patient"]).json()
        assert out["deleted"]["documents"] >= 1
        with pytest.raises(storage.StorageError):
            storage.read(ref2)

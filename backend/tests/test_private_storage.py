"""Private file storage: scanner interface (never 'clean' without a scanner, fail closed), quotas, atomic private writes, short-lived signed links."""
import base64
import os
import socket
import struct
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, filelinks, malware, prod_checks, storage
from app.config import UPLOAD_DIR
from app.main import app
from tests.test_api import lab_pdf

PDF = lab_pdf(["Ferritin     8     ng/mL     15 - 150  L"])


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(client, u="patient1"):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


def upload(client, h, data=PDF, name="r.pdf"):
    return client.post("/api/documents", files={"file": (name, data)}, headers=h)


class FakeClamd:
    """Speaks just enough of the clamd INSTREAM protocol. Replies: OK, or FOUND when the stream contains b'EICAR', or a scripted reply."""

    def __init__(self, reply=None, hang=False):
        self.reply, self.hang, self.streams = reply, hang, []
        self.sock = socket.socket(); self.sock.bind(("127.0.0.1", 0)); self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        with c:
            def need(n):
                b = b""
                while len(b) < n:
                    p = c.recv(n - len(b))
                    if not p:
                        raise EOFError
                    b += p
                return b
            try:
                assert need(10) == b"zINSTREAM\0"
                data = b""
                while True:
                    n = struct.unpack("!I", need(4))[0]
                    if n == 0:
                        break
                    data += need(n)
                self.streams.append(data)
                if self.hang:
                    threading.Event().wait(5)
                    return
                c.sendall((self.reply or (b"stream: Eicar-Test-Signature FOUND" if b"EICAR" in data else b"stream: OK")) + b"\0")
            except (EOFError, OSError, AssertionError):
                return

    def close(self):
        self.sock.close()


@pytest.fixture
def clamd(monkeypatch):
    f = FakeClamd()
    monkeypatch.setenv("COSMO_SCANNER", "clamd"); monkeypatch.setenv("COSMO_CLAMD_PORT", str(f.port))
    yield f
    f.close()


def files_on_disk():
    return {p for p in UPLOAD_DIR.rglob("*") if p.is_file()}


# ---------------------------------------------------------------- scanner interface
def test_without_a_scanner_nothing_is_ever_called_clean(client, monkeypatch):
    monkeypatch.delenv("COSMO_SCANNER", raising=False)
    assert malware.NoScanner().scan(b"anything").state == "unscanned"
    r = upload(client, H(client))
    assert r.status_code == 200
    assert db.q1("SELECT scan_status FROM documents WHERE id=?", (r.json()["id"],))["scan_status"] == "unscanned"


def test_production_without_a_scanner_refuses_uploads_unless_that_is_explicitly_accepted(client, monkeypatch):
    prod = {"COSMO_MODE": "production"}
    with pytest.raises(malware.UploadRefused) as e:
        malware.check(PDF, env=prod)
    assert e.value.status == 503 and "unavailable" in str(e.value)
    assert malware.check(PDF, env={**prod, "COSMO_ALLOW_UNSCANNED_UPLOADS": "1"}) == "unscanned"            # accepted, and recorded as unscanned, never as clean
    monkeypatch.setattr(malware, "unscanned_allowed", lambda env=os.environ: False)                          # the route applies the same rule (production mode also turns the consent gate on)
    before = files_on_disk()
    r = upload(client, H(client))
    assert r.status_code == 503 and "unavailable" in r.json()["detail"] and files_on_disk() == before
    assert any("malware scanner" in p for p in malware.problems({})) and malware.problems({"COSMO_ALLOW_UNSCANNED_UPLOADS": "1"}) == [] and malware.problems({"COSMO_SCANNER": "clamd"}) == []


def test_a_clean_verdict_from_the_scanner_is_recorded_and_the_whole_file_is_streamed(client, clamd):
    r = upload(client, H(client))
    assert r.status_code == 200 and clamd.streams == [PDF]
    assert db.q1("SELECT scan_status FROM documents WHERE id=?", (r.json()["id"],))["scan_status"] == "clean"


def test_an_infected_file_is_refused_and_nothing_is_stored(client, clamd):
    before, rows = files_on_disk(), db.q1("SELECT COUNT(*) c FROM documents")["c"]
    r = upload(client, H(client), PDF + b"\nEICAR")
    assert r.status_code == 422 and "virus" in r.json()["detail"] and "Eicar" not in r.text            # the signature name is not echoed to the user
    assert files_on_disk() == before and db.q1("SELECT COUNT(*) c FROM documents")["c"] == rows
    img = client.post("/api/images", data={"region": "face"}, files={"file": ("p.jpg", b"\xff\xd8EICAR")}, headers=H(client))
    assert img.status_code == 422 and files_on_disk() == before


@pytest.mark.parametrize("reply", [b"stream: INSTREAM size limit exceeded. ERROR", b"garbage", b""])
def test_unusable_scanner_replies_fail_closed(client, monkeypatch, reply):
    f = FakeClamd(reply=reply or b"\0")
    monkeypatch.setenv("COSMO_SCANNER", "clamd"); monkeypatch.setenv("COSMO_CLAMD_PORT", str(f.port))
    before = files_on_disk()
    try:
        r = upload(client, H(client))
        assert r.status_code == 503 and "could not check" in r.json()["detail"] and files_on_disk() == before
    finally:
        f.close()


def test_an_unreachable_or_slow_scanner_fails_closed_and_never_accepts_the_file(client, monkeypatch):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()                # a port nobody listens on
    monkeypatch.setenv("COSMO_SCANNER", "clamd"); monkeypatch.setenv("COSMO_CLAMD_PORT", str(port))
    before = files_on_disk()
    assert upload(client, H(client)).status_code == 503 and files_on_disk() == before
    slow = FakeClamd(hang=True)
    monkeypatch.setenv("COSMO_CLAMD_PORT", str(slow.port)); monkeypatch.setenv("COSMO_SCAN_TIMEOUT", "0.5")
    try:
        assert upload(client, H(client)).status_code == 503 and files_on_disk() == before
    finally:
        slow.close()


def test_the_scan_happens_before_any_parser_touches_the_bytes(client, clamd, monkeypatch):
    from app.documents import pipeline
    called = []
    monkeypatch.setattr(pipeline, "process", lambda *a, **k: called.append(1))
    assert upload(client, H(client), PDF + b"EICAR").status_code == 422 and called == []


# ---------------------------------------------------------------- quotas
def test_quota_stops_uploads_beyond_the_file_and_byte_limits_and_counts_only_live_files(client, monkeypatch):
    h = H(client, "patient2")
    db.x("DELETE FROM documents WHERE owner_id='patient2'"); db.x("DELETE FROM images WHERE owner_id='patient2'")
    monkeypatch.setenv("COSMO_USER_FILE_LIMIT", "2")
    a, b = upload(client, h), upload(client, h)
    assert a.status_code == b.status_code == 200
    before = files_on_disk()
    c3 = upload(client, h)
    assert c3.status_code == 413 and "storage limit" in c3.json()["detail"] and files_on_disk() == before
    assert client.delete(f"/api/documents/{a.json()['id']}", headers=h).status_code == 200            # deleting frees room
    assert upload(client, h).status_code == 200
    monkeypatch.setenv("COSMO_USER_FILE_LIMIT", "500")
    monkeypatch.setattr(storage, "limits", lambda: (len(PDF) * 2 + 10, 500))
    assert upload(client, h).status_code == 413
    db.x("DELETE FROM documents WHERE owner_id='patient1'")                                             # (earlier tests in this file left some)
    assert upload(client, H(client, "patient1")).status_code == 200


# ---------------------------------------------------------------- local storage mechanics
def test_stored_names_are_opaque_and_nothing_partial_is_left_behind():
    ref = storage.put("owner-x", b"data", "pdf")
    try:
        name = Path(ref).name
        assert len(name) == 36 and name.endswith(".pdf") and not list(Path(ref).parent.glob(".*.part"))
        if os.name == "posix":
            assert oct(Path(ref).stat().st_mode & 0o777) == "0o600"
    finally:
        storage.delete(ref)


def test_a_failed_write_leaves_no_file_and_no_partial(monkeypatch):
    before = files_on_disk()
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(storage.StorageError):
        storage.put("owner-y", b"data", "pdf")
    assert files_on_disk() == before


def test_a_rejected_document_leaves_nothing_on_disk(client):
    before = files_on_disk()
    assert upload(client, H(client), b"not a document at all", "x.pdf").status_code == 422 and files_on_disk() == before


# ---------------------------------------------------------------- signed links
def new_doc(client, user="patient1"):
    return upload(client, H(client, user)).json()["id"]


def test_a_link_is_issued_after_authorization_expires_quickly_and_hides_the_storage_path(client):
    did = new_doc(client)
    h = H(client)
    r = client.post(f"/api/documents/{did}/link", headers=h)
    assert r.status_code == 200 and r.json()["expires_in"] == 60
    url = r.json()["url"]
    body = base64.urlsafe_b64decode(url.rsplit("/", 1)[1].split(".")[0] + "==").decode()
    stored = db.q1("SELECT stored_path FROM documents WHERE id=?", (did,))["stored_path"]
    assert stored not in body and "uploads" not in body.lower() and did in body
    got = client.get(url)                                                                               # no bearer: the signed link is the credential
    assert got.status_code == 200 and got.content == PDF and got.headers["cache-control"] == "no-store"
    assert got.headers["content-disposition"].startswith("attachment") and got.headers["x-content-type-options"] == "nosniff" and "sandbox" in got.headers["content-security-policy"]
    assert db.q1("SELECT 1 x FROM audit WHERE action='file.link_issued' AND actor='patient1'") and db.q1("SELECT 1 x FROM audit WHERE action='file.download_linked' AND actor='patient1'")


def test_only_those_who_may_read_the_file_can_get_a_link(client):
    did = new_doc(client)
    assert client.post(f"/api/documents/{did}/link", headers=H(client, "patient2")).status_code == 404     # another patient
    assert client.post(f"/api/documents/{did}/link").status_code == 401
    assert client.post(f"/api/documents/{did}/link", headers=H(client, "doctor1")).status_code == 200
    assert client.post("/api/documents/nope/link", headers=H(client)).status_code == 404
    assert client.post(f"/api/images/{did}/link", headers=H(client)).status_code == 404                       # a document id is not an image id


def test_administrators_are_refused_unless_clinical_access_was_granted(client, monkeypatch):
    did = new_doc(client)
    monkeypatch.setenv("COSMO_ADMIN_CLINICAL_ACCESS", "none")
    assert client.post(f"/api/documents/{did}/link", headers=H(client, "admin1")).status_code == 404
    monkeypatch.setenv("COSMO_ADMIN_CLINICAL_ACCESS", "read")
    assert client.post(f"/api/documents/{did}/link", headers=H(client, "admin1")).status_code == 200


def test_expired_tampered_and_foreign_tokens_are_all_refused_alike(client):
    did = new_doc(client)
    good = filelinks.issue("document", did, "patient1")
    assert client.get(f"/api/files/{good}").status_code == 200
    expired = filelinks.issue("document", did, "patient1", now=1.0)
    forged_user = filelinks.issue("document", did, "patient2")
    flipped = good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB")
    answers = {client.get(f"/api/files/{t}").status_code for t in (expired, flipped, "junk", "a.b", forged_user)}
    assert answers == {404}                                                                             # patient2's validly signed link still cannot read patient1's file
    assert filelinks.issue("document", did, "p", ttl=10 ** 6, now=0).startswith("e") and filelinks.redeem(filelinks.issue("document", did, "p", ttl=10 ** 6, now=100), now=100 + 301 - 1)[2] == "p"
    with pytest.raises(filelinks.LinkError):
        filelinks.redeem(filelinks.issue("document", did, "p", ttl=10 ** 6, now=100), now=100 + 301)    # ttl is capped at five minutes


def test_deleting_the_file_or_disabling_the_account_kills_outstanding_links(client):
    did = new_doc(client)
    link = client.post(f"/api/documents/{did}/link", headers=H(client)).json()["url"]
    assert client.get(link).status_code == 200
    assert client.delete(f"/api/documents/{did}", headers=H(client)).status_code == 200
    assert client.get(link).status_code == 404
    did2 = new_doc(client)
    link2 = client.post(f"/api/documents/{did2}/link", headers=H(client, "doctor1")).json()["url"]
    db.x("UPDATE users SET disabled=1 WHERE id='doctor1'")
    try:
        assert client.get(link2).status_code == 404
    finally:
        db.x("UPDATE users SET disabled=0 WHERE id='doctor1'")
    assert client.get(link2).status_code == 200


def test_photos_get_links_too(client):
    h = H(client)
    ref = storage.put("patient1", bytes.fromhex("ffd8ffe0") + b"fake-jpeg-bytes", "jpg")          # stored directly: the suitability check rejects synthetic photos
    iid = db.uid()
    db.x("INSERT INTO images(id,owner_id,region,stored_path,verdict,report,created_at,deleted,scan_status,size_bytes) VALUES(?,?,?,?,?,?,?,0,'unscanned',20)",
         (iid, "patient1", "face", ref, "accepted", "{}", db.now()))
    link = client.post(f"/api/images/{iid}/link", headers=h).json()["url"]
    got = client.get(link)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg" and got.headers["content-disposition"] == 'attachment; filename="download"'
    assert client.post(f"/api/images/{iid}/link", headers=H(client, "patient2")).status_code == 404
    storage.delete(ref)

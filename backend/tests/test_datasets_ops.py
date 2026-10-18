"""Dataset acquisition and import behaviour against a REAL local HTTP server (Range, slow streaming, redirects, missing Content-Length)."""
import csv
import hashlib
import http.server
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db
from app.datasets import importer, registry
from app.main import app
from tests.test_api import tok

@pytest.fixture(autouse=True, scope="module")
def _database():
    db.init()          # these tests do not depend on another module having created the schema


PAYLOAD = (bytes(range(256)) * 4096) * 5          # 5 MiB, deterministic
SHA = hashlib.sha256(PAYLOAD).hexdigest()


class Srv:
    seen_ranges: list = []
    ignore_range = False
    no_length = False
    slow = False
    redirect_to = None
    payload = PAYLOAD


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if Srv.redirect_to and self.path == "/redir":
            self.send_response(302); self.send_header("Location", Srv.redirect_to); self.end_headers(); return
        rng = self.headers.get("Range")
        Srv.seen_ranges.append(rng)
        data, start = Srv.payload, 0
        if rng and not Srv.ignore_range:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                self.send_response(416); self.end_headers(); return
            self.send_response(206); self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        if not Srv.no_length:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        for i in range(0, len(body), 256 * 1024):
            try:
                self.wfile.write(body[i:i + 256 * 1024])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            if Srv.slow:
                time.sleep(0.03)


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("COSMO_ALLOW_LOCAL_HTTP", "1")
    Srv.seen_ranges, Srv.ignore_range, Srv.no_length, Srv.slow, Srv.redirect_to, Srv.payload = [], False, False, False, None, PAYLOAD
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


# ---------------------------------------------------------------- download
def test_download_verifies_checksum(server, tmp_path):
    dest = importer.download(server + "/f", tmp_path / "f.bin", SHA, "t1")
    assert dest.read_bytes() == PAYLOAD and not (tmp_path / "f.bin.part").exists()


def test_checksum_mismatch_discards_everything(server, tmp_path):
    with pytest.raises(importer.ImportError_, match="Checksum mismatch"):
        importer.download(server + "/f", tmp_path / "f.bin", "0" * 64, "t2")
    assert not (tmp_path / "f.bin").exists() and not (tmp_path / "f.bin.part").exists()


def test_plain_http_and_non_local_hosts_are_refused(server, tmp_path, monkeypatch):
    with pytest.raises(importer.ImportError_, match="https"):
        importer.download("http://example.com/x", tmp_path / "x", None, "t3")
    monkeypatch.delenv("COSMO_ALLOW_LOCAL_HTTP")
    with pytest.raises(importer.ImportError_, match="https"):
        importer.download(server + "/f", tmp_path / "x", None, "t3")      # the test hook is off by default


def test_cancel_keeps_progress_and_resume_sends_range(server, tmp_path):
    Srv.slow = True
    first = {"done": False}

    def cancel_after_first_chunk(done, total):
        if not first["done"]:
            first["done"] = True
            importer.cancel("t4")
    with pytest.raises(importer.ImportError_, match="cancelled"):
        importer.download(server + "/f", tmp_path / "f.bin", SHA, "t4", on_progress=cancel_after_first_chunk)
    part = tmp_path / "f.bin.part"
    got = part.stat().st_size
    assert 0 < got < len(PAYLOAD) and not (tmp_path / "f.bin").exists()
    Srv.seen_ranges.clear(); Srv.slow = False
    dest = importer.download(server + "/f", tmp_path / "f.bin", SHA, "t4")      # resume
    assert Srv.seen_ranges == [f"bytes={got}-"] and dest.read_bytes() == PAYLOAD


def test_server_that_ignores_range_restarts_cleanly(server, tmp_path):
    (tmp_path / "f.bin.part").write_bytes(PAYLOAD[:12345])
    Srv.ignore_range = True
    assert importer.download(server + "/f", tmp_path / "f.bin", SHA, "t5").read_bytes() == PAYLOAD


def test_resume_when_part_is_already_complete_http_416(server, tmp_path):
    (tmp_path / "f.bin.part").write_bytes(PAYLOAD)                           # server answers 416 to bytes=N-
    assert importer.download(server + "/f", tmp_path / "f.bin", SHA, "t6").read_bytes() == PAYLOAD


def test_corrupted_partial_file_is_detected_after_resume(server, tmp_path):
    (tmp_path / "f.bin.part").write_bytes(b"x" * 1000)                       # garbage prefix from an earlier, different file
    with pytest.raises(importer.ImportError_, match="Checksum mismatch"):
        importer.download(server + "/f", tmp_path / "f.bin", SHA, "t7")
    assert not (tmp_path / "f.bin.part").exists()


def test_size_cap_is_enforced_with_and_without_content_length(server, tmp_path):
    with pytest.raises(importer.ImportError_, match="larger than"):
        importer.download(server + "/f", tmp_path / "a.bin", None, "t8", max_bytes=1024 * 1024)
    Srv.no_length = True
    with pytest.raises(importer.ImportError_, match="exceeded"):
        importer.download(server + "/f", tmp_path / "b.bin", None, "t8", max_bytes=1024 * 1024)
    assert not (tmp_path / "b.bin.part").exists()


def test_redirect_to_plain_http_is_refused(server, tmp_path, monkeypatch):
    monkeypatch.delenv("COSMO_ALLOW_LOCAL_HTTP")
    Srv.redirect_to = "http://example.com/evil"
    monkeypatch.setenv("COSMO_ALLOW_LOCAL_HTTP", "1")
    with pytest.raises(importer.ImportError_, match="non-https"):
        importer.download(server + "/redir", tmp_path / "r.bin", None, "t9")


def test_unreachable_server_and_http_errors_are_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("COSMO_ALLOW_LOCAL_HTTP", "1")
    with pytest.raises(importer.ImportError_, match="Could not reach"):
        importer.download("http://127.0.0.1:9/never", tmp_path / "n.bin", None, "t10")


def test_not_enough_disk_space_is_refused_before_writing(server, tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 9, 1))
    with pytest.raises(importer.ImportError_, match="free disk space"):
        importer.download(server + "/f", tmp_path / "d.bin", None, "t11")
    assert not (tmp_path / "d.bin.part").exists()


# ---------------------------------------------------------------- unzip + ingest
def test_safe_unzip_rejects_traversal_and_skips_non_images(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../evil.png", b"x")
    with pytest.raises(importer.ImportError_, match="Unsafe"):
        importer.safe_unzip(z, tmp_path / "out")
    ok = tmp_path / "ok.zip"
    png = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    with zipfile.ZipFile(ok, "w") as f:
        f.writestr("a/img.png", png); f.writestr("a/run.exe", b"MZ"); f.writestr("meta.csv", "a,b")
    assert importer.safe_unzip(ok, tmp_path / "out2") == 2 and not (tmp_path / "out2/a/run.exe").exists()


def _img(seed, size=64):
    return cv2.imencode(".png", np.random.default_rng(seed).integers(0, 255, (size, size, 3), dtype=np.uint8))[1].tobytes()


def test_ingest_label_conflicts_duplicates_and_near_duplicates(tmp_path):
    d = tmp_path / "imgs"; d.mkdir()
    a, b, c = _img(1), _img(2), _img(3)
    (d / "a1.png").write_bytes(a); (d / "a2.png").write_bytes(a)             # identical, same label  -> skipped duplicate
    (d / "b1.png").write_bytes(b); (d / "b2.png").write_bytes(b)             # identical, DIFFERENT labels -> both excluded
    (d / "c.png").write_bytes(c); (d / "broken.png").write_bytes(b"nope")
    lab = tmp_path / "l.csv"
    lab.write_text("filename,label,group_id\na1.png,X,p1\na2.png,X,p2\nb1.png,X,p3\nb2.png,Y,p4\nc.png,Y,p5\n")
    r = importer.ingest_folder("ingest-t", d, "tester", lab)
    assert r["count"] == 2 and r["duplicates_skipped"] == 1 and r["label_conflicts_excluded"] == 1 and len(r["errors"]) == 1
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (r["version_id"],))["manifest"])
    assert {e["file"] for e in mf["entries"]} == {"a1.png", "c.png"} and mf["label_conflicts_excluded"][0]["labels"] == ["X", "Y"]
    r2 = importer.ingest_folder("ingest-t", d, "tester", lab)
    assert r2["version"] == r["version"] + 1                                  # versioned, never overwritten


def test_ingest_can_be_cancelled_midway(tmp_path, monkeypatch):
    d = tmp_path / "imgs"; d.mkdir()
    for i in range(6):
        (d / f"{i}.png").write_bytes(_img(10 + i))
    seen = {"n": 0}
    real = importer.dhash

    def hooked(im):
        seen["n"] += 1
        if seen["n"] == 2:
            importer.cancel("ingest-c")
        return real(im)
    monkeypatch.setattr(importer, "dhash", hooked)
    r = importer.ingest_folder("ingest-c", d, "tester")
    assert r["count"] == 2 and any(e["error"] == "cancelled" for e in r["errors"])


def test_ingest_limit_and_disk_guard(tmp_path, monkeypatch):
    d = tmp_path / "imgs"; d.mkdir()
    for i in range(4):
        (d / f"{i}.png").write_bytes(_img(30 + i))
    assert importer.ingest_folder("ingest-l", d, "t", limit=2)["count"] == 2
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 9, 1))
    with pytest.raises(importer.ImportError_):
        importer.ingest_folder("ingest-l", d, "t")


# ---------------------------------------------------------------- registry + staff routes
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class FakeResp(io.BytesIO):
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def mendeley_json(name="CC BY 4.0", files=None):
    return json.dumps({"version": 1, "doi": {"id": "10.1/x"}, "data_licence": {"short_name": name, "url": "http://creativecommons.org/licenses/by/4.0", "description": "terms"},
                       "files": files if files is not None else [{"filename": "a.bin", "content_details": {"download_url": "https://files.example/a.bin", "sha256_hash": SHA, "size": len(PAYLOAD)}}]}).encode()


def test_official_manifest_and_fetch_terms(monkeypatch):
    rec = dict(id="ops-ds", name="Ops", url="https://data.mendeley.com/datasets/abc123", approval_status="unreviewed", annotations=[], terms_verified=False)
    registry.save(dict(rec))
    monkeypatch.setattr(registry.urllib.request, "urlopen", lambda req, timeout=0: FakeResp(mendeley_json()))
    d = registry.fetch_terms("ops-ds", "admin1")
    assert d["official_license_name"] == "CC BY 4.0" and d["official_manifest"]["files"][0]["sha256"] == SHA and len(d["terms_snapshot_hash"]) == 64
    assert registry.official_manifest({"url": "https://example.com/nothing"}) is None                       # no adapter: manual review required


def test_licence_gate_matrix():
    base = dict(terms_verified=True, terms_snapshot_hash="h", verified_spdx="CC-BY-4.0", approval_status="approved", claimed_license="CC BY 4.0")
    assert registry.license_gate(base, "model_training", True)["allowed"]
    assert not registry.license_gate({**base, "verified_spdx": "CC-BY-NC-4.0"}, "model_training", True)["allowed"]
    assert registry.license_gate({**base, "verified_spdx": "CC-BY-NC-4.0"}, "model_training", False)["allowed"] is True      # non-commercial research use
    assert not registry.license_gate({**base, "terms_verified": False}, "model_training", False)["allowed"]
    assert not registry.license_gate({**base, "approval_status": "unreviewed"}, "model_training", False)["allowed"]
    assert registry.license_gate({**base, "verified_spdx": "", "permission_document": "LETTER-1", "terms_snapshot_hash": ""}, "model_training", True)["allowed"]
    assert not registry.license_gate({**base, "verified_spdx": ""}, "redistribution", True)["allowed"]


def test_compatibility_rules():
    d = dict(id="x", modality="dermoscopy", annotations=["lesion diagnosis"])
    face = dict(id="t", required_image_type="face photograph", required_labels=["acne severity grade"], architecture="CNN")
    r = registry.compatibility(d, face)
    assert not r["compatible"] and len(r["reasons_incompatible"]) >= 2
    seg = dict(id="t2", required_image_type="dermoscopic lesion", required_labels=["lesion diagnosis"], architecture="CNN segmentation")
    assert any("mask" in x for x in registry.compatibility(d, seg)["reasons_incompatible"])
    docs = dict(id="t3", required_image_type="scanned document", required_labels=[], architecture="OCR")
    assert any("documents" in x for x in registry.compatibility(d, docs)["reasons_incompatible"])


def _approved_dataset(did="ops-dl", files=None):
    manifest = {"files": files or [{"name": "a.bin", "url": "https://files.example/a.bin", "sha256": SHA, "size": 1000}]}
    registry.save(dict(id=did, name="Ops", url="https://x", approval_status="approved", terms_verified=True, terms_snapshot_hash="h", verified_spdx="CC-BY-4.0",
                       official_manifest=manifest, annotations=[]))


def wait_dl(client, h, did, until=("done", "failed")):
    for _ in range(100):
        s = client.get(f"/api/admin/datasets/{did}/download", headers=h).json()
        if s["state"] in until:
            return s
        time.sleep(0.1)
    raise AssertionError(s)


def test_download_route_gate_success_failure_and_double_start(client, monkeypatch):
    a, p = tok(client, "admin1"), tok(client, "patient1")
    registry.save(dict(id="ops-blocked", name="B", url="https://x", approval_status="unreviewed", annotations=[], official_manifest={"files": [{"name": "a", "url": "https://f/a", "sha256": "0", "size": 1}]}))
    assert client.post("/api/admin/datasets/ops-blocked/download", headers=a).status_code == 409           # gate blocks
    assert client.post("/api/admin/datasets/ops-blocked/download", headers=p).status_code == 403
    assert client.post("/api/admin/datasets/nope/download", headers=a).status_code == 404
    registry.save(dict(id="ops-nomanifest", name="N", url="https://x", approval_status="approved", terms_verified=True, terms_snapshot_hash="h", verified_spdx="CC-BY-4.0", annotations=[]))
    assert client.post("/api/admin/datasets/ops-nomanifest/download", headers=a).status_code == 422       # no official manifest

    _approved_dataset("ops-dl")
    gate = threading.Event()

    def fake_ok(url, dest, sha, did, max_bytes=0, on_progress=None):
        on_progress(10, 100); gate.wait(5); dest.write_bytes(b"ok"); return dest
    monkeypatch.setattr(importer, "download", fake_ok)
    assert client.post("/api/admin/datasets/ops-dl/download", headers=a).status_code == 200
    time.sleep(0.2)
    assert client.post("/api/admin/datasets/ops-dl/download", headers=a).status_code == 409                # already running
    gate.set()
    s = wait_dl(client, a, "ops-dl")
    assert s["state"] == "done" and s["files"]["a.bin"]["verified"] is True

    _approved_dataset("ops-dl2")
    def fake_fail(*args, **kw):
        raise importer.ImportError_("Checksum mismatch: the downloaded file was discarded.")
    monkeypatch.setattr(importer, "download", fake_fail)
    client.post("/api/admin/datasets/ops-dl2/download", headers=a)
    s = wait_dl(client, a, "ops-dl2")
    assert s["state"] == "failed" and "Checksum mismatch" in s["error"]
    acts = [r["action"] for r in db.q("SELECT action FROM audit WHERE target IN ('ops-dl','ops-dl2')")]
    assert {"dataset.download_start", "dataset.download_done", "dataset.download_failed"} <= set(acts)


def test_download_route_refuses_when_disk_is_too_small(client, monkeypatch):
    a = tok(client, "admin1")
    _approved_dataset("ops-disk", files=[{"name": "big", "url": "https://f/big", "sha256": "0", "size": 10 ** 15}])
    r = client.post("/api/admin/datasets/ops-disk/download", headers=a)
    assert r.status_code == 507 and "disk" in r.json()["detail"].lower()


def test_cancel_route_and_versions(client):
    a = tok(client, "admin1")
    assert client.post("/api/admin/datasets/ops-dl/cancel", headers=a).json() == {"cancelled": True}
    assert isinstance(client.get("/api/admin/datasets/ingest-t/versions", headers=a).json(), list)

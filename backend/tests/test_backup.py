"""Encrypted backups: format, tamper/truncation/wrong-key detection, a full backup-restore round trip, retention and status reporting."""
import json
import os
import sqlite3
from datetime import datetime

import pytest

from scripts import backup as bk

KEY = bk.load_key(__import__("base64").b64encode(bytes(range(32))).decode())                  # fixed test-only key


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(bk, "CHUNK", 64)


def _enc(tmp_path, data: bytes):
    src, enc, out = tmp_path / "p.bin", tmp_path / "p.enc", tmp_path / "p.out"
    src.write_bytes(data)
    bk.encrypt_file(src, enc, KEY)
    return src, enc, out


# ---------------------------------------------------------------- key handling
def test_key_validation():
    assert len(bk.load_key("A" * 43 + "=")) == 32
    for bad in ("", "not base64!!", "QUJD", "A" * 44):
        with pytest.raises(bk.BackupError):
            bk.load_key(bad)


def test_genkey_prints_a_valid_random_key(capsys):
    assert bk.main(["backup", "genkey"]) == 0
    k1 = capsys.readouterr().out.strip()
    bk.main(["backup", "genkey"])
    assert k1 != capsys.readouterr().out.strip() and len(bk.load_key(k1)) == 32


# ---------------------------------------------------------------- format
@pytest.mark.parametrize("n", [0, 1, 63, 64, 65, 128, 64 * 5 + 7])
def test_roundtrip_at_chunk_boundaries(tmp_path, small_chunks, n):
    data = os.urandom(n)
    src, enc, out = _enc(tmp_path, data)
    bk.decrypt_file(enc, out, KEY)
    assert out.read_bytes() == data


def test_ciphertext_hides_plaintext_and_is_randomised(tmp_path):
    marker = b"PATIENT-NAME-JANE-DOE" * 50
    src, enc, _ = _enc(tmp_path, marker)
    assert marker[:21] not in enc.read_bytes()
    enc2 = tmp_path / "p2.enc"
    bk.encrypt_file(src, enc2, KEY)
    assert enc.read_bytes() != enc2.read_bytes()                                    # fresh nonce prefix each time


def test_wrong_key_is_named_clearly(tmp_path):
    _, enc, out = _enc(tmp_path, b"secret")
    with pytest.raises(bk.BackupError, match="wrong key"):
        bk.decrypt_file(enc, out, bk.load_key("B" * 43 + "="))


def test_any_flipped_byte_is_detected(tmp_path, small_chunks):
    _, enc, out = _enc(tmp_path, os.urandom(300))
    blob = enc.read_bytes()
    for pos in (len(blob) - 1, len(blob) // 2, bk.HEADER_LEN + 5, 17, 0):           # tag, middle, chunk length/data, nonce prefix, magic
        bad = bytearray(blob); bad[pos] ^= 1
        enc.write_bytes(bytes(bad))
        with pytest.raises(bk.BackupError):
            bk.decrypt_file(enc, out, KEY)
    enc.write_bytes(blob)
    bk.decrypt_file(enc, out, KEY)                                                  # the pristine file still decrypts


def test_truncation_at_a_chunk_boundary_is_detected(tmp_path, small_chunks):
    _, enc, out = _enc(tmp_path, os.urandom(64 * 4))
    blob = enc.read_bytes()
    rec = 4 + 64 + 16                                                               # one full chunk record
    for cut in (len(blob) - rec, len(blob) - 1, bk.HEADER_LEN + rec, bk.HEADER_LEN, 10):
        enc.write_bytes(blob[:cut])
        with pytest.raises(bk.BackupError):
            bk.decrypt_file(enc, out, KEY)


def test_reordered_dropped_or_appended_chunks_are_detected(tmp_path, small_chunks):
    _, enc, out = _enc(tmp_path, os.urandom(64 * 4))
    blob = enc.read_bytes()
    rec = 4 + 64 + 16
    h, chunks = blob[:bk.HEADER_LEN], [blob[bk.HEADER_LEN + i * rec: bk.HEADER_LEN + (i + 1) * rec] for i in range(5)]
    for variant in (h + chunks[1] + chunks[0] + b"".join(chunks[2:]),             # swapped
                    h + chunks[0] + b"".join(chunks[2:]),                          # one dropped
                    blob + chunks[0],                                              # a valid chunk appended
                    blob + b"\x00"):                                               # junk appended
        enc.write_bytes(variant)
        with pytest.raises(bk.BackupError):
            bk.decrypt_file(enc, out, KEY)


def test_not_a_backup_file(tmp_path):
    f = tmp_path / "x"; f.write_bytes(b"hello world" * 10)
    with pytest.raises(bk.BackupError, match="not a Cosmo"):
        bk.decrypt_file(f, tmp_path / "o", KEY)


# ---------------------------------------------------------------- full round trip
def _make_data_dir(root):
    d = root / "data"
    (d / "private_uploads" / "owner1").mkdir(parents=True)
    (d / "private_uploads" / "owner1" / "a.pdf").write_bytes(b"%PDF-1.4 patient file")
    (d / "private_uploads" / "_jobs").mkdir()
    (d / "private_uploads" / "_jobs" / "tmp.bin").write_bytes(b"transient")
    c = sqlite3.connect(d / "cosmo.sqlite3")
    for t in ("users", "documents", "images", "care_plans", "audit"):
        c.execute(f"CREATE TABLE {t}(id TEXT, note TEXT)")
        c.execute(f"INSERT INTO {t} VALUES('1','row-in-{t}')")
    c.commit(); c.close()
    return d


def test_encrypted_backup_and_restore_round_trip(tmp_path):
    d = _make_data_dir(tmp_path)
    dest = tmp_path / "backups"
    f = bk.backup_encrypted(d, dest, KEY)
    assert f.suffix == ".cbk" and f.with_suffix(".cbk.json").is_file()
    assert [p.name for p in dest.iterdir() if p.name.startswith(".")] == []         # no plaintext working copy left behind
    assert b"row-in-users" not in f.read_bytes() and b"patient file" not in f.read_bytes()
    side = json.loads(f.with_suffix(".cbk.json").read_text())
    assert side["sha256"] == bk.sha256(f) and side["key_id"] == bk.key_id(KEY).hex() and "key" not in json.dumps(side).replace("key_id", "")
    st = json.loads((d / "backup_status.json").read_text())
    assert st["last_ok"] is True and st["last_file"] == f.name and st["last_success_ts"] > 0

    target = tmp_path / "restored"
    res = bk.restore_encrypted(f, target, KEY)
    assert res["integrity_check"] == "ok" and res["counts"] == {t: 1 for t in ("users", "documents", "images", "care_plans", "audit")}
    assert (target / "private_uploads" / "owner1" / "a.pdf").read_bytes() == b"%PDF-1.4 patient file"
    assert not (target / "private_uploads" / "_jobs").exists()                       # transient inputs are excluded by design
    assert sqlite3.connect(target / "cosmo.sqlite3").execute("SELECT note FROM users").fetchone()[0] == "row-in-users"
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []
    with pytest.raises(SystemExit, match="not empty"):
        bk.restore_encrypted(f, target, KEY)                                          # never overwrite


def test_restore_with_wrong_key_or_tampered_file_leaves_nothing_behind(tmp_path):
    d = _make_data_dir(tmp_path)
    f = bk.backup_encrypted(d, tmp_path / "b", KEY)
    with pytest.raises(bk.BackupError, match="wrong key"):
        bk.restore_encrypted(f, tmp_path / "r1", bk.load_key("C" * 43 + "="))
    blob = bytearray(f.read_bytes()); blob[-20] ^= 1
    f.write_bytes(bytes(blob))
    with pytest.raises(bk.BackupError, match="authentication"):
        bk.restore_encrypted(f, tmp_path / "r2", KEY)
    assert not (tmp_path / "r1").exists() and not (tmp_path / "r2").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_a_failed_backup_is_reported_in_the_status_file_and_cleans_up(tmp_path):
    d = _make_data_dir(tmp_path)
    with pytest.raises(Exception):
        bk.backup_encrypted(d, tmp_path / "b", b"short-key")
    st = json.loads((d / "backup_status.json").read_text())
    assert st["last_ok"] is False and st["last_error"] and "last_success_ts" not in st
    assert [p.name for p in (tmp_path / "b").iterdir()] == []
    bk.backup_encrypted(d, tmp_path / "b", KEY)                                       # recovery: success overwrites the failure
    st = json.loads((d / "backup_status.json").read_text())
    assert st["last_ok"] is True and st["last_error"] == ""


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    d = _make_data_dir(tmp_path)
    import app.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", d)
    monkeypatch.setenv("COSMO_BACKUP_KEY", "A" * 43 + "=")
    assert bk.main(["backup", "backup-encrypted", str(tmp_path / "out")]) == 0
    name = json.loads(capsys.readouterr().out)["backup"]
    assert bk.main(["backup", "restore-encrypted", str(tmp_path / "out" / name), str(tmp_path / "back")]) == 0
    assert json.loads(capsys.readouterr().out)["integrity_check"] == "ok"
    monkeypatch.delenv("COSMO_BACKUP_KEY")
    with pytest.raises(bk.BackupError):
        bk.main(["backup", "backup-encrypted", str(tmp_path / "out2")])
    assert bk.main(["backup"]) == 2


# ---------------------------------------------------------------- retention
def _touch(dest, stamp):
    dest.mkdir(exist_ok=True)
    (dest / f"cosmo-backup-{stamp}.cbk").write_bytes(b"x")
    (dest / f"cosmo-backup-{stamp}.cbk.json").write_text("{}")


def test_retention_keeps_recent_days_one_per_week_and_the_newest(tmp_path):
    now = datetime(2026, 11, 20, 12, 0, 0)
    stamps = ["20261120-020000", "20261119-020000", "20261118-020000", "20261115-020000",            # within 7 days
              "20261112-020000", "20261111-020000",                                                  # week 46: keep the newest only
              "20261105-020000", "20261104-020000", "20261029-020000", "20261022-020000",            # one per week for 4 weeks
              "20260901-020000", "20260101-020000"]                                                  # far older: removed
    for s in stamps:
        _touch(tmp_path, s)
    (tmp_path / "unrelated.txt").write_text("keep me")
    res = bk.prune(tmp_path, 7, 4, now)
    assert {n for n in res["removed"]} >= {"cosmo-backup-20260901-020000.cbk", "cosmo-backup-20260101-020000.cbk", "cosmo-backup-20261111-020000.cbk"}
    left = sorted(p.name for p in tmp_path.glob("*.cbk"))
    assert "cosmo-backup-20261120-020000.cbk" in left and "cosmo-backup-20261112-020000.cbk" in left
    assert not (tmp_path / "cosmo-backup-20260101-020000.cbk.json").exists()                         # sidecar removed with its backup
    assert (tmp_path / "unrelated.txt").exists()
    assert len(left) <= 4 + 4 + 1


def test_retention_never_deletes_the_only_or_newest_backup(tmp_path):
    _touch(tmp_path, "20200101-000000")
    assert bk.prune(tmp_path, 7, 4, datetime(2026, 11, 20))["removed"] == []
    assert bk.prune(tmp_path / "missing", 7, 4) == {"kept": [], "removed": []}

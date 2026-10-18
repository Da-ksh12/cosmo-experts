"""The secret scanner must catch real credential shapes and stay quiet on placeholders. Fixture values are built at run time so this file holds no credential-shaped text."""
from pathlib import Path

from scripts import scan_secrets as ss


def _scan(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return ss.scan(tmp_path)


def test_detects_each_credential_shape(tmp_path):
    cases = {
        "a.txt": "token " + "sbp_" + "0123456789abcdef" * 3,
        "b.txt": "k=" + ".".join(["eyJ" + "a" * 20, "eyJ" + "b" * 20, "c" * 20]),
        "c.pem": "-----BEGIN " + "PRIVATE KEY-----",
        "d.txt": "id AK" + "IA" + "ABCDEFGH12345678",
        "e.py": 'password = "' + "Zq9" * 10 + '"',
        "f.env": "sb_" + "secret_" + "Zq9Xw2" * 6,
    }
    for n, t in cases.items():
        (tmp_path / n).write_text(t, encoding="utf-8")
    found = {f for f, _, _ in ss.scan(tmp_path)}
    assert found == set(cases)


def test_placeholders_and_binaries_and_skipped_dirs_are_ignored(tmp_path):
    assert _scan(tmp_path, "x.md", 'SECRET = "xxxxxxxxxxxxxxxxxxxxxxxxxxxx"\napi_key: "your-api-key-goes-here-1234567"') == []
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "y.js").write_text("sbp_" + "0123456789abcdef" * 3)
    (tmp_path / "img.png").write_bytes(b"sbp_" + b"0123456789abcdef" * 3)
    assert ss.scan(tmp_path) == []


def test_findings_never_contain_the_value(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("sbp_" + "0123456789abcdef" * 3)
    assert ss.main(["x", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "a.txt:1: possible supabase-management-token" in out and "0123456789abcdef" not in out


def test_the_repository_itself_is_clean():
    assert ss.scan(Path(__file__).resolve().parents[2]) == []

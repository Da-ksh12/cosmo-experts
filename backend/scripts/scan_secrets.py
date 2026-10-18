"""Fail if a credential-looking value is present in the repository tree.   python backend/scripts/scan_secrets.py [root]

Detects: Supabase management tokens (sbp_...), JWTs (three base64url parts starting eyJ, which includes service-role keys), PEM private keys, AWS access key ids,
generic long `secret|token|password|api_key = "..."` assignments, and Supabase new-style secret keys (sb_secret_...). Skips build output, dependency
folders, data directories and binary files. Placeholder words (xxxx, example, changeme) are ignored only for the generic assignment pattern; the fixed-format patterns are never suppressed.
Exit 1 and print file:line (never the value) on a finding."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "supabase-management-token": re.compile(r"\bsbp_[0-9a-f]{30,}\b"),
    "supabase-secret-key": re.compile(r"\bsb_secret_[A-Za-z0-9_\-]{20,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "aws-access-key-id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "generic-assignment": re.compile(r"""(?i)\b(?:secret|token|passw(?:or)?d|api[_-]?key)\b\s*[:=]\s*["']([A-Za-z0-9/+_\-]{24,})["']"""),
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", ".next", "__pycache__", "data", "secrets", "dist", "build", ".pytest_cache", "playwright-report"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".pt", ".safetensors", ".onnx", ".gguf", ".sqlite3", ".woff", ".woff2", ".cbk", ".bin"}
FAKE = re.compile(r"(?i)x{4,}|example|changeme|placeholder|dummy|fixture|your[_-]|<.*>|0{8,}")      # applied ONLY to the generic assignment pattern
SELF = Path(__file__).resolve()


def tracked_or_walk(root: Path):
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True, check=True).stdout
        for rel in filter(None, out.decode().split("\0")):
            yield root / rel
        return
    except (OSError, subprocess.CalledProcessError):
        pass
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS):
            yield p


def scan(root: Path) -> list[tuple[str, int, str]]:
    hits = []
    for p in tracked_or_walk(root):
        if p.resolve() == SELF or p.suffix.lower() in SKIP_SUFFIX or set(p.relative_to(root).parts) & SKIP_DIRS or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if len(text) > 2_000_000:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in PATTERNS.items():
                m = rx.search(line)
                if m and not (name == "generic-assignment" and FAKE.search(m.group(0))):
                    hits.append((str(p.relative_to(root)).replace("\\", "/"), n, name))
    return hits


def main(argv) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    hits = scan(root)
    for f, n, name in hits:
        print(f"{f}:{n}: possible {name}")
    print(f"{len(hits)} finding(s)" if hits else "no credentials found")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

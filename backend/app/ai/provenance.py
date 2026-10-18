"""Code revision for provenance: the Git commit (with a dirty marker) when the repository is available, otherwise the source-tree hash that model cards already use.
Computed once per process: hashing the tree on every request would be wasteful."""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

ENGINE_VERSION = "ai-engine-1.0.0"
_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()
_rev: str | None = None


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(_ROOT), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def code_revision() -> str:
    global _rev
    with _lock:
        if _rev is None:
            head = _git("rev-parse", "--short=12", "HEAD")
            if head:
                dirty = _git("status", "--porcelain", "--", "backend/app")
                _rev = f"git-{head}" + ("+dirty" if dirty else "")
            else:
                from ..training import trainer
                _rev = trainer.code_revision()
        return _rev


def reset() -> None:
    global _rev
    with _lock:
        _rev = None

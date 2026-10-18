"""Short-lived signed links to private files.

A link is issued ONLY after the normal authorization check for the requesting account, lives at most five minutes (default one), names the file by database id
(never by storage path, bucket or key), and is bound to the account it was issued to. Redeeming it re-checks everything: the signature, the expiry, that the account
still exists and is not disabled, that the file still exists and has not been deleted, and that the account may still read it. Deleting a file or disabling an account
therefore kills outstanding links at once. Links are not reusable across accounts and nothing about them is cached (no-store)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from .config import SECRET

TTL_DEFAULT, TTL_MAX = 60, 300


class LinkError(Exception):
    pass


def _sig(body: bytes) -> str:
    return hmac.new(SECRET.encode(), b"cosmo-file-link\x1f" + body, hashlib.sha256).hexdigest()[:40]


def issue(kind: str, resource_id: str, user_id: str, ttl: int = TTL_DEFAULT, now: float | None = None) -> str:
    if kind not in ("document", "image"):
        raise ValueError("kind")
    exp = int((now if now is not None else time.time()) + max(5, min(ttl, TTL_MAX)))
    body = json.dumps({"k": kind, "r": resource_id, "u": user_id, "e": exp}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + _sig(body)


def redeem(token: str, now: float | None = None) -> tuple[str, str, str]:
    """Returns (kind, resource_id, user_id) or raises LinkError. The message never says which check failed."""
    try:
        b64, sig = token.split(".", 1)
        body = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        if not hmac.compare_digest(sig, _sig(body)):
            raise LinkError("invalid")
        d = json.loads(body)
        if int(d["e"]) < (now if now is not None else time.time()) or d["k"] not in ("document", "image"):
            raise LinkError("invalid")
        return d["k"], str(d["r"]), str(d["u"])
    except LinkError:
        raise
    except (ValueError, KeyError, TypeError):
        raise LinkError("invalid") from None

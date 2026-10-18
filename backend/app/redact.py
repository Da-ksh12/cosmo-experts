"""Secret and personal-data redaction for logs, exception messages and diagnostics.

Used by the logging filter (logging_setup.py), by `safe_exc` wherever an exception text could reach a log or a client, and by Settings.describe().
Redaction is defence in depth: code should not log secrets in the first place. It is pattern-based, so it cannot guarantee that every possible secret format is caught;
the structured logger therefore also avoids logging request bodies, headers, file contents and conversation text at all."""
from __future__ import annotations

import re
from typing import Any

_R = "[REDACTED]"
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bsbp_[0-9A-Za-z]{20,}"), "sbp_" + _R),                                                        # Supabase management tokens
    (re.compile(r"\bsb_(?:secret|publishable)_[0-9A-Za-z_\-]{16,}"), "sb_" + _R),                                # Supabase API keys (new format)
    (re.compile(r"\beyJ[0-9A-Za-z_\-]{8,}\.eyJ[0-9A-Za-z_\-]{8,}\.[0-9A-Za-z_\-]{8,}"), "[REDACTED-JWT]"),      # JWTs incl. service-role keys
    (re.compile(r"(?i)\b(postgres(?:ql)?|mysql|redis|amqp)://([^:/\s@]+):([^@\s]+)@"), r"\1://\2:" + _R + "@"),    # credentials in connection URLs
    (re.compile(r"(?i)\bBearer\s+[0-9A-Za-z._~+/\-]+=*"), "Bearer " + _R),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|cookie)\b([\"']?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"), r"\1\2" + _R),
    (re.compile(r"(?i)\b(cosmo_rt|sb-[a-z0-9]+-auth-token)=[^;\s]+"), r"\1=" + _R),                               # session cookies
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "[REDACTED-PRIVATE-KEY]"),
    (re.compile(r"\b([A-Za-z0-9._%+\-]{1,2})[A-Za-z0-9._%+\-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"), r"\1***@\2"),  # e-mail addresses: keep two characters and the domain
]
SENSITIVE_KEYS = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|service[_-]?role|database[_-]?url|dsn)")
# fields that hold patient content: their VALUES are never logged by the structured logger
CONTENT_KEYS = re.compile(r"(?i)^(text|content|body|report|source_text|transcript|answer|message|question|file|image|audio)$")


def redact(text: Any) -> str:
    s = text if isinstance(text, str) else str(text)
    for rx, rep in _PATTERNS:
        s = rx.sub(rep, s)
    return s


def redact_obj(obj: Any, depth: int = 0) -> Any:
    """Recursively redact dict/list structures: sensitive keys lose their value, patient-content keys are replaced by their length, strings are pattern-redacted."""
    if depth > 6:
        return _R
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SENSITIVE_KEYS.search(str(k)):
                out[k] = _R
            elif CONTENT_KEYS.match(str(k)) and isinstance(v, (str, bytes)):
                out[k] = f"[{len(v)} {'bytes' if isinstance(v, bytes) else 'chars'} omitted]"
            else:
                out[k] = redact_obj(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, depth + 1) for v in obj[:50]]
    if isinstance(obj, bytes):
        return f"[{len(obj)} bytes omitted]"
    return redact(obj) if isinstance(obj, str) else obj


def safe_exc(e: BaseException, limit: int = 300) -> str:
    """'TypeName: redacted message' suitable for logs and audit details."""
    return f"{type(e).__name__}: {redact(str(e))[:limit]}"

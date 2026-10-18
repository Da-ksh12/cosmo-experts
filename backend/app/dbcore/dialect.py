"""SQLite-style SQL to PostgreSQL (psycopg) translation for the application's query vocabulary.

The application writes plain SQL with `?` placeholders. For PostgreSQL this module (a) turns `?` outside string literals into `%s`, (b) doubles every literal `%`
(psycopg treats `%` as a placeholder marker, and LIKE patterns contain them), and (c) maps LIKE to ILIKE because SQLite's LIKE is case-insensitive for ASCII and the
application relies on that. String literals ('...' with '' escapes) and quoted identifiers ("...") are never altered apart from `%`. Anything beyond this vocabulary
(INSERT OR IGNORE/REPLACE, PRAGMA, executescript) is deliberately NOT translated: the db module offers explicit helpers (insert_ignore, upsert) instead."""
from __future__ import annotations

import re

_LIKE = re.compile(r"\bLIKE\b", re.I)
_FORBIDDEN = re.compile(r"\b(INSERT\s+OR\s+(IGNORE|REPLACE)|PRAGMA|AUTOINCREMENT|ATTACH\s+DATABASE)\b", re.I)


class DialectError(ValueError):
    pass


def _tokens(sql: str):
    """Yield (kind, text) where kind is 'lit' (single-quoted), 'ident' (double-quoted) or 'code'."""
    i, n, start = 0, len(sql), 0
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            if start < i:
                yield "code", sql[start:i]
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            else:
                raise DialectError("unterminated quoted string in SQL")
            yield ("lit" if ch == "'" else "ident"), sql[i:j + 1]
            i = start = j + 1
        else:
            i += 1
    if start < n:
        yield "code", sql[start:]


def to_pg(sql: str) -> str:
    out = []
    for kind, text in _tokens(sql):
        if kind == "code":
            if _FORBIDDEN.search(text):
                raise DialectError("SQL uses a SQLite-only construct; use the db helper (insert_ignore/upsert) instead")
            text = _LIKE.sub("ILIKE", text.replace("%", "%%").replace("?", "%s"))
        else:
            text = text.replace("%", "%%")
        out.append(text)
    return "".join(out)

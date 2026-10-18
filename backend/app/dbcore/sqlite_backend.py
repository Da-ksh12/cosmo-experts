"""SQLite backend (local development and isolated tests). One short-lived connection per transaction behind a process-wide lock, as before."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

IntegrityError = sqlite3.IntegrityError


class SqliteTx:
    def __init__(self, conn: sqlite3.Connection):
        self.c = conn

    def q(self, sql, args=()):
        return [dict(r) for r in self.c.execute(sql, tuple(args)).fetchall()]

    def q1(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()) -> int:
        return self.c.execute(sql, tuple(args)).rowcount

    def script(self, sql: str):
        self.c.executescript(sql)


class SqliteBackend:
    name = "sqlite"
    transactional_ddl = False

    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.RLock()

    @contextmanager
    def tx(self, ctx=None):
        with self._lock:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            try:
                yield SqliteTx(c)
                c.commit()
            except BaseException:
                c.rollback()
                raise
            finally:
                c.close()

    def q(self, sql, args=(), ctx=None):
        with self.tx(ctx) as t:
            return t.q(sql, args)

    def x(self, sql, args=(), ctx=None) -> int:
        with self.tx(ctx) as t:
            return t.x(sql, args)

    def close(self):
        pass

    def healthy(self) -> tuple[bool, str]:
        try:
            self.q("SELECT 1 x")
            return True, "sqlite reachable"
        except Exception as e:
            return False, type(e).__name__

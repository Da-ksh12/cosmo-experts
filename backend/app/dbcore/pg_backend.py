"""PostgreSQL backend: pooled psycopg 3 connections, transaction-scoped identity context, bounded retries for connection failures only.

Design rules
* One transaction per db call (or per `tx()` block). Every transaction starts by publishing the DbContext as transaction-local settings (set_config(..., true)) so
  row-level-security policies see the caller. Nothing is left on the connection afterwards, which keeps it safe behind a transaction-mode pooler (Supavisor/PgBouncer):
  no session state, no `options` startup parameter, server-side prepared statements disabled.
* Retries: acquiring a connection is retried a bounded number of times with backoff. A read-only single statement is retried if the connection breaks mid-flight.
  A write is NEVER retried after it started executing: an ambiguous failure must reach the caller, not be repeated blindly.
* Timestamps: `Epoch` values are sent as timestamptz and timestamptz columns come back as `Epoch`, so application code is unchanged.
* Credentials are never logged; error text is redacted."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
from psycopg import sql as pgsql
from psycopg.adapt import Dumper
from psycopg.pq import Format
from psycopg.rows import dict_row
from psycopg.types.datetime import TimestamptzLoader
from psycopg_pool import ConnectionPool, PoolTimeout

from ..redact import safe_exc
from .context import DbContext
from .dialect import to_pg
from .types import Epoch

log = logging.getLogger("cosmo.db")
IntegrityError = psycopg.IntegrityError


class DatabaseUnavailable(Exception):
    """The database could not be reached after the configured retries. The message is safe to log."""


def _retryable(e: BaseException) -> bool:
    """Only connection-level failures are worth retrying: no connection (08), server shutting down/not accepting (57P0x), resource exhaustion such as too many
    connections (53). A cancelled statement (57014: timeout), a deadlock or any SQL error is NOT retried: repeating it would not help and would hide the cause."""
    if isinstance(e, (DatabaseUnavailable, PoolTimeout)):
        return True
    state = getattr(e, "sqlstate", None)
    if state is None:
        return isinstance(e, psycopg.OperationalError)           # no SQLSTATE: the connection itself failed
    return state.startswith(("08", "57P", "53"))


class _EpochDumper(Dumper):
    format = Format.TEXT
    oid = psycopg.postgres.types["timestamptz"].oid

    def dump(self, obj):
        return datetime.fromtimestamp(float(obj), tz=timezone.utc).isoformat().encode()


class _EpochLoader(TimestamptzLoader):
    def load(self, data):
        return Epoch(super().load(data).timestamp())


def _configure(conn: psycopg.Connection) -> None:
    conn.adapters.register_dumper(Epoch, _EpochDumper)
    conn.adapters.register_loader("timestamptz", _EpochLoader)
    conn.prepare_threshold = None            # never create server-side prepared statements (transaction-pooler safe)


class PgTx:
    def __init__(self, conn: psycopg.Connection):
        self.c = conn

    def q(self, sql, args=()):
        with self.c.cursor() as cur:
            cur.execute(to_pg(sql), tuple(args))
            return cur.fetchall() if cur.description else []

    def q1(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()) -> int:
        with self.c.cursor() as cur:
            cur.execute(to_pg(sql), tuple(args))
            return cur.rowcount

    def script(self, sql: str):
        """Several statements without parameters (migrations)."""
        self.c.execute(pgsql.SQL(sql))                         # simple-query protocol: multiple statements allowed, no parameters


class PostgresBackend:
    name = "postgres"
    transactional_ddl = True

    def __init__(self, url: str, pool_min: int = 1, pool_max: int = 10, connect_timeout: int = 5, statement_timeout_ms: int = 30000, retries: int = 3,
                 context_secret: str = ""):
        self.url, self.retries, self.statement_timeout_ms = url, retries, statement_timeout_ms
        self._ctx_key = context_secret.encode("utf-8")
        self.pool = ConnectionPool(conninfo=url, min_size=pool_min, max_size=pool_max, timeout=connect_timeout, configure=_configure, check=ConnectionPool.check_connection,
                                   kwargs={"row_factory": dict_row, "connect_timeout": connect_timeout, "autocommit": False}, open=False, name="cosmo-app")
        self._opened = False

    def open(self, wait_s: float = 10.0):
        if not self._opened:
            try:
                self.pool.open(wait=True, timeout=wait_s)
            except Exception as e:
                self.pool.close()
                raise DatabaseUnavailable("could not open the database pool: " + safe_exc(e)) from None
            self._opened = True

    def close(self):
        if self._opened:
            self.pool.close()
            self._opened = False

    # ------------------------------------------------------------------ transactions
    def _acquire(self):
        self.open()
        last = None
        for attempt in range(self.retries + 1):
            try:
                cm = self.pool.connection()
                return cm, cm.__enter__()
            except (PoolTimeout, psycopg.OperationalError) as e:
                last = e
                if attempt < self.retries:
                    time.sleep(min(0.1 * (3 ** attempt), 2.0))
        raise DatabaseUnavailable("database unavailable after retries: " + safe_exc(last)) from None

    @contextmanager
    def tx(self, ctx: DbContext | None = None):
        cm, conn = self._acquire()
        try:
            ctx = ctx or DbContext()
            msg = f"{ctx.user_id}\x1f{ctx.role}\x1f{ctx.admin_clinical}".encode("utf-8")
            sig = hmac.new(self._ctx_key, msg, hashlib.sha256).hexdigest() if self._ctx_key else ""     # the policies only honour an identity whose signature verifies
            conn.execute("SELECT set_config('app.user_id', %s, true), set_config('app.role', %s, true), set_config('app.admin_clinical', %s, true), "
                         "set_config('app.ctx_sig', %s, true), set_config('statement_timeout', %s, true)", (ctx.user_id, ctx.role, ctx.admin_clinical, sig, str(self.statement_timeout_ms)))
            yield PgTx(conn)
        except BaseException as e:
            cm.__exit__(type(e), e, e.__traceback__)             # rolls back and returns the connection to the pool
            raise
        else:
            cm.__exit__(None, None, None)                          # commits

    def q(self, sql, args=(), ctx=None):
        last = None
        for attempt in range(self.retries + 1):                    # a read is idempotent: a broken connection may be retried
            try:
                with self.tx(ctx) as t:
                    return t.q(sql, args)
            except (psycopg.OperationalError, DatabaseUnavailable) as e:
                if not _retryable(e):
                    raise                                              # e.g. statement timeout: surface the real error
                last = e
                if attempt < self.retries:
                    time.sleep(min(0.1 * (3 ** attempt), 2.0))
        raise DatabaseUnavailable("read failed after retries: " + safe_exc(last)) from None

    def x(self, sql, args=(), ctx=None) -> int:
        with self.tx(ctx) as t:                                    # no retry once a write may have been sent
            return t.x(sql, args)

    def healthy(self) -> tuple[bool, str]:
        try:
            self.q("SELECT 1 AS x")
            return True, "postgres reachable"
        except Exception as e:
            return False, type(e).__name__

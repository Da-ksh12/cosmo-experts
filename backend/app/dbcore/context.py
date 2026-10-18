"""Database access context: WHO a statement runs for.

On PostgreSQL every transaction begins by publishing this context as transaction-local settings (app.user_id, app.role, app.admin_clinical); the row-level-security
policies read them, so the database itself refuses cross-patient reads and writes even if an application check were missing. The default context is role 'none':
a code path that forgot to establish an identity can read and write nothing. Server-internal work (startup, background jobs, notifications to other users) must say so
explicitly with `as_system()`. On SQLite the context is carried but not enforced (local development and tests)."""
from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from dataclasses import dataclass

ROLES = ("patient", "doctor", "admin", "system", "none")


@dataclass(frozen=True)
class DbContext:
    user_id: str = ""
    role: str = "none"
    admin_clinical: str = "none"         # 'read' lets administrators read clinical records; anything else does not


_ctx: contextvars.ContextVar[DbContext] = contextvars.ContextVar("cosmo_db_context", default=DbContext())


def current() -> DbContext:
    return _ctx.get()


def _admin_clinical() -> str:
    return "read" if (os.environ.get("COSMO_ADMIN_CLINICAL_ACCESS") or "").strip().lower() == "read" else "none"


def make(user_id: str, role: str) -> DbContext:
    if role not in ROLES:
        raise ValueError("unknown database role")
    return DbContext(user_id or "", role, _admin_clinical())


def set_user(user_id: str, role: str):
    """Establish the identity for the rest of the current task/request. Returns a token for reset()."""
    return _ctx.set(make(user_id, role))


def reset(token) -> None:
    _ctx.reset(token)


@contextmanager
def as_user(user_id: str, role: str):
    tok = set_user(user_id, role)
    try:
        yield
    finally:
        reset(tok)


@contextmanager
def as_system():
    with as_user("", "system"):
        yield


def run_as_system(fn):
    """Decorator for thread entry points and workers: they start with an empty context and need to say they are server-internal."""
    def wrapper(*a, **kw):
        with as_system():
            return fn(*a, **kw)
    wrapper.__name__ = getattr(fn, "__name__", "wrapper")
    return wrapper

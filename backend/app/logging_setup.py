"""Structured JSON logging with correlation ids and mandatory redaction.

Every record carries the request id of the request that produced it (set by the middleware), passes through RedactingFilter, and is emitted as one JSON line.
Request bodies, headers, uploaded content and conversation text are never logged by this application. uvicorn's access log is redacted as well because it prints the
full request target (a query string could carry a token)."""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time

from .redact import redact, redact_obj

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
            if record.exc_info and not record.exc_text:
                record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            elif record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:                                    # a logging failure must never break a request
            record.msg, record.args = "[log record could not be redacted and was dropped]", ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z", "level": record.levelname, "logger": record.name,
               "request_id": request_id_var.get(), "msg": record.getMessage()}
        extra = getattr(record, "fields", None)
        if extra:
            out["fields"] = redact_obj(extra)
        if record.exc_info:
            out["exc"] = redact(logging.Formatter().formatException(record.exc_info))[:2000]
        return json.dumps(out, ensure_ascii=False)


_CONFIGURED = False


def configure(level: str | None = None, force: bool = False) -> None:
    """Install the redacting JSON handler on the root and uvicorn loggers. Safe to call repeatedly."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return
    lvl = getattr(logging, (level or os.environ.get("COSMO_LOG_LEVEL", "INFO")).upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(lvl)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True                                   # through the root handler: one redacted JSON stream
    _CONFIGURED = True


def log_fields(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    logger.log(level, msg, extra={"fields": fields})

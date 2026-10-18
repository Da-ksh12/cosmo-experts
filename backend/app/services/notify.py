"""In-app notifications for genuine state changes (a document finished processing, a clinician reviewed a photo, a plan or quotation was approved, a new submission
arrived for the clinic). Nothing here is an external channel: email, SMS and push are NOT implemented and the UI does not suggest otherwise.

Privacy: titles and bodies are deliberately generic (no result values, diagnoses or file names). Idempotent: a repeated event with the same dedupe key for the same
user creates one notification. A notification failure must never fail the business action that caused it."""
from __future__ import annotations

import logging

from .. import db

log = logging.getLogger("cosmo.notify")


def send(user_id: str, kind: str, title: str, body: str = "", link: str = "", dedupe: str = "") -> bool:
    """Returns True if a new notification was stored."""
    try:
        with db.conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO notifications(id,user_id,kind,title,body,link,dedupe_key,created_at,read_at) VALUES(?,?,?,?,?,?,?,?,NULL)",
                            (db.uid(), user_id, kind, title[:140], body[:300], link[:200], dedupe, db.now()))
            return cur.rowcount == 1
    except Exception:                                   # never break the caller
        log.exception("notification not stored")
        return False


def to_role(role: str, kind: str, title: str, body: str = "", link: str = "", dedupe: str = "") -> int:
    n = 0
    for u in db.q("SELECT id FROM users WHERE role=?", (role,)):
        n += send(u["id"], kind, title, body, link, f"{dedupe}:{u['id']}" if dedupe else "")
    return n

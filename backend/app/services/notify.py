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
        with db.context.as_system():                       # a notification for ANOTHER user is a server action, not the requester's own write
            return db.insert_ignore("notifications", {"id": db.uid(), "user_id": user_id, "kind": kind, "title": title[:140], "body": body[:300], "link": link[:200],
                                                      "dedupe_key": dedupe, "created_at": db.now(), "read_at": None})
    except Exception:                                   # never break the caller
        log.exception("notification not stored")
        return False


def to_role(role: str, kind: str, title: str, body: str = "", link: str = "", dedupe: str = "") -> int:
    n = 0
    with db.context.as_system():                                 # reading the user directory to address a role is a server action
        for u in db.q("SELECT id FROM users WHERE role=?", (role,)):
            n += send(u["id"], kind, title, body, link, f"{dedupe}:{u['id']}" if dedupe else "")
    return n


def mark_read(user_id: str, notification_id: str) -> bool:
    """Mark one of the person's OWN notifications read. Someone else's id is indistinguishable from a missing one (False)."""
    if not db.q1("SELECT 1 x FROM notifications WHERE id=? AND user_id=?", (notification_id, user_id)):
        return False
    db.x("UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE id=? AND user_id=?", (db.now(), notification_id, user_id))
    return True


def mark_all_read(user_id: str) -> dict:
    db.x("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (db.now(), user_id))
    return {"unread": 0}

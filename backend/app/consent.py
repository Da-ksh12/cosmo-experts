"""Patient privacy notice and consent.

The notice text is LEGAL content: the draft shipped in content/consent_notice.draft.md is never shown for acceptance in production. A notice becomes active only when
content/consent_notice.approved.json exists with a reviewer/approval block whose SHA-256 matches the notice text (same pattern as the urgent-symptom policy).
Acceptance is recorded per user against the exact notice hash, so changing the text asks everyone again. Enforcement (patients cannot upload, record or message until they have
accepted the current notice) is on in production and with COSMO_REQUIRE_CONSENT=1; it is off by default for local development and tests.
A hash proves the text was not altered after approval; it is not a signature, and who may place the file is the clinic's change control."""
from __future__ import annotations

import hashlib
import json
import os

from fastapi import Depends, HTTPException

from . import clinical_content as cc
from . import db
from .security import current_user


def enforced(env=os.environ) -> bool:
    return env.get("COSMO_MODE") == "production" or env.get("COSMO_REQUIRE_CONSENT") == "1"


def _sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def notice() -> dict:
    """The notice currently offered: {'text', 'sha256', 'status': 'approved'|'draft'|'none', 'approved_by'}."""
    ap = cc.CONTENT_DIR / "consent_notice.approved.json"
    try:
        doc = json.loads(ap.read_text(encoding="utf-8"))
        text, a = str(doc["text"]), doc["approval"]
        for k in ("reviewer_name", "reviewer_role", "registration_number", "approved_on", "statement", "content_sha256"):
            if not str(a.get(k, "")).strip():
                raise ValueError(k)
        if a["content_sha256"] != _sha(text):
            raise ValueError("hash")
        return {"text": text, "sha256": a["content_sha256"], "status": "approved", "approved_by": f"{a['reviewer_name']} ({a['reviewer_role']}), {a['approved_on']}"}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    d = cc.CONTENT_DIR / "consent_notice.draft.md"
    if d.is_file():
        t = d.read_text(encoding="utf-8")
        return {"text": t, "sha256": _sha(t), "status": "draft", "approved_by": ""}
    return {"text": "", "sha256": "", "status": "none", "approved_by": ""}


def usable() -> bool:
    """A notice patients may be asked to accept: approved, or (outside production) a draft."""
    n = notice()
    return n["status"] == "approved" or (n["status"] == "draft" and os.environ.get("COSMO_MODE") != "production")


def _latest(user_id: str) -> dict | None:
    return db.q1("SELECT notice_sha256, notice_status, action, at FROM consent_events WHERE user_id=? ORDER BY at DESC, id DESC LIMIT 1", (user_id,))


def accepted(user_id: str) -> bool:
    """True only if the person's LATEST decision is an acceptance of the notice that is current now. A withdrawal, or a changed notice, makes it false.
    Acceptances recorded before the history existed (table `consents`) count only for a user with no history at all."""
    n = notice()
    if not n["sha256"]:
        return False
    last = _latest(user_id)
    if last:
        return last["action"] == "accepted" and last["notice_sha256"] == n["sha256"]
    return bool(db.q1("SELECT 1 x FROM consents WHERE user_id=? AND notice_sha256=?", (user_id, n["sha256"])))


def _record(user: dict, n: dict, action: str, source: str = "web") -> None:
    with db.tx() as t:
        t.x("INSERT INTO consent_events(id,user_id,notice_sha256,notice_status,action,at,source) VALUES(?,?,?,?,?,?,?)",
            (db.uid(), user["id"], n["sha256"], "approved" if n["status"] == "approved" else "draft", action, db.now(), source))
        db.audit(user["id"], f"consent.{'accept' if action == 'accepted' else 'withdraw'}", user["id"], {"notice_sha256": n["sha256"], "status": n["status"]}, t=t)


def status_for(user: dict) -> dict:
    n = notice()
    need = enforced() and user["role"] == "patient"
    ok = accepted(user["id"])
    last = _latest(user["id"]) if user["role"] == "patient" else None
    return {"enforced": enforced(), "required": need and not ok, "accepted": ok, "available": usable(),
            "withdrawn": bool(last and last["action"] == "withdrawn"),
            "notice": {"text": n["text"], "sha256": n["sha256"], "status": n["status"], "approved_by": n["approved_by"]} if need else None,
            # what consent does NOT cover, stated so that screens can show it: consent here is never a permission to train models
            "not_covered": ["model_training"]}


def accept(user: dict, notice_sha256: str) -> None:
    n = notice()
    if not usable() or n["sha256"] != notice_sha256:
        raise HTTPException(409, "The privacy notice changed or is unavailable. Please reload and read the current notice.")
    if accepted(user["id"]):
        return                                                                       # idempotent: no duplicate history entries
    _record(user, n, "accepted")
    db.insert_ignore("consents", {"user_id": user["id"], "notice_sha256": n["sha256"], "accepted_at": db.now()})


def withdraw(user: dict) -> None:
    """Withdrawal takes effect at once: the gate closes for new uploads, recordings and messages. It does not delete what was already stored; that is the deletion
    request process (docs/PRIVACY_AND_CONSENT.md), which keeps records the law requires."""
    last = _latest(user["id"])
    if last and last["action"] == "withdrawn":
        return
    n = notice()
    if not last and not accepted(user["id"]):
        return                                                                       # nothing to withdraw
    _record(user, {"sha256": (last or {}).get("notice_sha256") or n["sha256"], "status": (last or {}).get("notice_status") or n["status"]}, "withdrawn")


def history(user_id: str) -> list[dict]:
    """The person's own consent decisions, newest first. The notice text is not repeated; its hash and approval status identify it."""
    return db.q("SELECT notice_sha256, notice_status, action, at FROM consent_events WHERE user_id=? ORDER BY at DESC, id DESC LIMIT 200", (user_id,))


def required(user=Depends(current_user)):
    """Dependency for patient endpoints that collect or process personal data."""
    if user["role"] == "patient" and enforced() and not accepted(user["id"]):
        raise HTTPException(403, {"code": "consent_required", "message": "Please read and accept the privacy notice to continue."})
    return user


def patient_consented(user=Depends(current_user)):
    """Like require("patient"), plus the consent check."""
    if user["role"] != "patient":
        raise HTTPException(403, "You do not have permission for this action.")
    return required(user)

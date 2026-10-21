"""Clinic profile and appointment requests (see the package docstring). All reads and writes go through the signed-in person's own context; nothing here can show or change another
patient's request."""
from __future__ import annotations

import json
import logging
import re

from .. import db
from ..services import notify

log = logging.getLogger("cosmo.booking")
PROFILE_KEY = "clinic_profile"
STATUSES = ("requested", "contacted", "confirmed", "declined", "cancelled", "closed")
OPEN_STATUSES = ("requested", "contacted")
STAFF_STATUSES = ("contacted", "confirmed", "declined", "closed")
CONTACT_METHODS = ("app", "phone", "email")
URGENCY = ("routine", "urgent")
MAX_OPEN_PER_PATIENT = 3
_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d{2,5})?(?:/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)?$")
_PHONE = re.compile(r"^\+?[0-9][0-9 ()-]{5,19}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
PROFILE_FIELDS = {"clinic_name": 120, "phone": 24, "email": 200, "address": 300, "hours": 300, "booking_url": 300, "whatsapp": 24, "notes": 500}


class BookingError(Exception):
    """The message is safe to show to the person."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- the clinic's profile
def get_profile() -> dict:
    row = db.q1("SELECT value FROM settings WHERE key=?", (PROFILE_KEY,))
    try:
        data = json.loads(row["value"]) if row else {}
    except ValueError:
        data = {}
    return {k: str(data.get(k) or "")[:n] for k, n in PROFILE_FIELDS.items()}


def validate_profile(d: dict) -> dict:
    out = {}
    for k, n in PROFILE_FIELDS.items():
        v = str(d.get(k) or "").strip()
        if len(v) > n:
            raise BookingError(f"'{k}' is too long (at most {n} characters).")
        out[k] = v
    if out["phone"] and not _PHONE.match(out["phone"]):
        raise BookingError("The phone number does not look right.")
    if out["whatsapp"] and not _PHONE.match(out["whatsapp"]):
        raise BookingError("The WhatsApp number does not look right.")
    if out["email"] and not _EMAIL.match(out["email"]):
        raise BookingError("The e-mail address does not look right.")
    if out["booking_url"] and not _URL.match(out["booking_url"]):
        raise BookingError("The online booking page must be a plain https:// address.")
    return out


def set_profile(d: dict, actor_id: str) -> dict:
    clean = validate_profile(d)
    db.set_setting(PROFILE_KEY, json.dumps(clean))
    db.audit(actor_id, "clinic.profile", "", {k: bool(v) for k, v in clean.items()})      # what was filled in, never the values
    return get_profile()


def services() -> list[dict]:
    """The clinic's approved services and prices, exactly as the clinic entered them."""
    return db.q("SELECT id,name,description,unit_price,currency,category,sessions,duration_min FROM price_items WHERE active=1 AND approved_by IS NOT NULL AND approved_by!='' ORDER BY category,name")


def public_profile() -> dict:
    """What a patient may see: only the fields the clinic filled in, how a request can be made, and the honest state of availability."""
    p = get_profile()
    shown = {k: v for k, v in p.items() if v}
    contact = any(shown.get(k) for k in ("phone", "email", "whatsapp"))
    return {"profile": shown, "configured": bool(shown), "contact_available": contact,
            "booking": {"mode": "request", "external_page": shown.get("booking_url") or None,
                        "note": "Sending a request does not book a time. The clinic's team contacts you to agree one."},
            "availability": {"source": "not_connected", "slots": [], "note": "Live availability is not connected. The clinic confirms a time after your request."},
            "services": services()}


# ---------------------------------------------------------------- requests
def _public(r: dict, staff: bool = False) -> dict:
    out = {"id": r["id"], "status": r["status"], "service_id": r.get("service_id") or None, "service_name": r.get("service_name") or None, "reason": r["reason"], "preferred": r.get("preferred") or "",
           "contact_method": r["contact_method"], "urgency": r.get("urgency") or "routine", "analysis_id": r.get("analysis_id") or None, "share_summary": bool(r.get("share_summary")),
           "confirmed_for": r.get("confirmed_for") or None, "staff_note": r.get("staff_note") or "" if (staff or r["status"] in ("confirmed", "declined", "contacted")) else "",
           "created_at": r["created_at"], "updated_at": r.get("updated_at")}
    if staff:
        out.update(patient_id=r["patient_id"], contact_detail=r.get("contact_detail") or "", patient=r.get("patient"))
    return out


def create_request(user: dict, body: dict) -> dict:
    """A patient's appointment request. Never a booking: the reply says so."""
    reason = " ".join(str(body.get("reason") or "").split())
    if len(reason) < 3:
        raise BookingError("Please say briefly what the appointment is for.")
    if len(reason) > 500:
        raise BookingError("Please keep the reason under 500 characters.")
    preferred = " ".join(str(body.get("preferred") or "").split())[:200]
    method = body.get("contact_method") or "app"
    if method not in CONTACT_METHODS:
        raise BookingError("Please choose how the clinic should reach you.")
    detail = str(body.get("contact_detail") or "").strip()
    if method == "phone" and not _PHONE.match(detail):
        raise BookingError("Please enter a phone number the clinic can call.")
    if method == "email" and not _EMAIL.match(detail):
        raise BookingError("Please enter an e-mail address the clinic can write to.")
    if method == "app":
        detail = ""
    urgency = body.get("urgency") or "routine"
    if urgency not in URGENCY:
        urgency = "routine"
    service_id, service_name = None, None
    if body.get("service_id"):
        s = db.q1("SELECT id,name FROM price_items WHERE id=? AND active=1 AND approved_by IS NOT NULL AND approved_by!=''", (str(body["service_id"]),))
        if not s:
            raise BookingError("That service is not on the clinic's list.")
        service_id, service_name = s["id"], s["name"]
    analysis_id = None
    if body.get("analysis_id"):
        from ..photo_analysis import store
        if store.get(str(body["analysis_id"]), user["id"]) is None:                  # a photo assessment can only be attached by its owner
            raise BookingError("That photo assessment was not found.", 404)
        analysis_id = str(body["analysis_id"])
    open_n = db.q1("SELECT COUNT(*) c FROM appointment_requests WHERE patient_id=? AND status IN ('requested','contacted')", (user["id"],))["c"]
    if open_n >= MAX_OPEN_PER_PATIENT:
        raise BookingError(f"You already have {open_n} requests waiting for the clinic. Please wait for them to be answered, or cancel one.", 409)
    rid = db.uid()
    now = db.now()
    db.x("INSERT INTO appointment_requests(id,patient_id,service_id,service_name,reason,preferred,contact_method,contact_detail,analysis_id,share_summary,urgency,status,confirmed_for,staff_note,decided_by,created_at,updated_at)"
         " VALUES(?,?,?,?,?,?,?,?,?,?,?,'requested','','','',?,?)",
         (rid, user["id"], service_id, service_name, reason, preferred, method, detail, analysis_id, 1 if body.get("share_summary") else 0, urgency, now, now))
    db.audit(user["id"], "appointment.request", rid, {"urgency": urgency, "has_service": bool(service_id), "has_photo": bool(analysis_id), "contact": method})
    notify.to_role("doctor", "appointment_request", "New appointment request" + (" (same day)" if urgency == "urgent" else ""), "A patient asked for an appointment. Open the clinic workspace.", "/clinic", f"appt:{rid}")
    return _public(db.q1("SELECT * FROM appointment_requests WHERE id=?", (rid,)))


def my_requests(user_id: str) -> list[dict]:
    return [_public(r) for r in db.q("SELECT * FROM appointment_requests WHERE patient_id=? ORDER BY created_at DESC LIMIT 50", (user_id,))]


def cancel_request(user_id: str, rid: str) -> dict:
    r = db.q1("SELECT * FROM appointment_requests WHERE id=? AND patient_id=?", (rid, user_id))
    if not r:
        raise BookingError("Request not found.", 404)
    if r["status"] not in OPEN_STATUSES:
        raise BookingError("This request can no longer be cancelled.", 409)
    db.x("UPDATE appointment_requests SET status='cancelled', updated_at=? WHERE id=? AND patient_id=?", (db.now(), rid, user_id))
    db.audit(user_id, "appointment.cancel", rid)
    return _public(db.q1("SELECT * FROM appointment_requests WHERE id=?", (rid,)))


def staff_queue(status: str | None = None) -> list[dict]:
    q = ("SELECT a.*, u.name patient FROM appointment_requests a JOIN users u ON u.id=a.patient_id" + (" WHERE a.status=?" if status else "")
         + " ORDER BY CASE a.status WHEN 'requested' THEN 0 WHEN 'contacted' THEN 1 ELSE 2 END, CASE a.urgency WHEN 'urgent' THEN 0 ELSE 1 END, a.created_at DESC LIMIT 200")
    return [_public(r, staff=True) for r in db.q(q, (status,) if status else ())]


def staff_summary(actor_id: str, rid: str) -> dict:
    """The short, factual summary of the photo check that the patient chose to share with this request. Nothing else of the patient's photos or conversations is opened here, and each view is audited."""
    r = db.q1("SELECT * FROM appointment_requests WHERE id=?", (rid,))
    if not r:
        raise BookingError("Request not found.", 404)
    if not (r["share_summary"] and r["analysis_id"]):
        raise BookingError("The patient did not share a photo summary with this request.", 404)
    from ..photo_analysis import answers
    try:
        res = answers.answer(r["patient_id"], "Prepare a summary for my dermatologist.", {"analysis_id": r["analysis_id"], "intent": "summary"})
    except answers.ContextError:
        res = None
    if not res:
        raise BookingError("The photo summary is no longer available (the photo may have been deleted).", 404)
    db.audit(actor_id, "appointment.summary_view", rid)
    return {"summary": res["answer"], "analysis_id": r["analysis_id"]}


def staff_update(actor_id: str, rid: str, status: str, *, confirmed_for: str = "", note: str = "") -> dict:
    r = db.q1("SELECT * FROM appointment_requests WHERE id=?", (rid,))
    if not r:
        raise BookingError("Request not found.", 404)
    if status not in STAFF_STATUSES:
        raise BookingError("Unknown status.")
    if r["status"] == "cancelled":
        raise BookingError("The patient cancelled this request.", 409)
    confirmed_for = " ".join(confirmed_for.split())[:120]
    if status == "confirmed" and len(confirmed_for) < 3:
        raise BookingError("Write the day and time you agreed with the patient (for example 'Tuesday 28 October, 4:30 pm').")
    if status != "confirmed":
        confirmed_for = ""
    db.x("UPDATE appointment_requests SET status=?, confirmed_for=?, staff_note=?, decided_by=?, updated_at=? WHERE id=?", (status, confirmed_for, " ".join(note.split())[:300], actor_id, db.now(), rid))
    db.audit(actor_id, "appointment.update", rid, {"status": status})
    title = {"contacted": "The clinic has seen your request", "confirmed": "Your appointment is confirmed", "declined": "About your appointment request", "closed": "Your appointment request was closed"}[status]
    notify.send(r["patient_id"], "appointment_update", title, "Open Book to see the details.", "/book", f"appt-{status}:{rid}")
    return _public(db.q1("SELECT a.*, u.name patient FROM appointment_requests a JOIN users u ON u.id=a.patient_id WHERE a.id=?", (rid,)), staff=True)

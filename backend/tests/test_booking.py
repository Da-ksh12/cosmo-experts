"""Appointment requests and the clinic's profile (app/booking, /api/v1/clinic/profile, /api/v1/appointments/requests, /api/v1/clinic/appointments).

What these tests pin down: the product has NO calendar, so no slot is ever shown or promised; a request is never a booking; only a member of the clinic's staff can record that a time was agreed,
in their own words; a patient sees and changes only their own requests; nothing is shown that the clinic did not enter; the assistant says all of this honestly."""
import re

import pytest
from fastapi.testclient import TestClient

from app import db
from app.assistant import dispatch
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


@pytest.fixture(autouse=True)
def _clean():
    yield
    db.x("DELETE FROM appointment_requests")
    db.x("DELETE FROM settings WHERE key='clinic_profile'")
    db.x("DELETE FROM notifications WHERE kind IN ('appointment_request','appointment_update')")


PROFILE = {"clinic_name": "Cosmo Skin Clinic", "phone": "+91 98765 43210", "email": "desk@example.com", "address": "12 Main Road", "hours": "Mon-Sat 10:00-18:00",
           "booking_url": "https://book.example.com/cosmo", "notes": "Please bring earlier reports."}


def ask(client, h, text):
    cid = client.post("/api/conversations", headers=h).json()["id"]
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": text}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- the clinic's profile
def test_nothing_is_shown_that_the_clinic_did_not_enter_and_availability_is_never_invented(client):
    p = client.get("/api/v1/clinic/profile", headers=tok(client, "patient1")).json()
    assert p["profile"] == {} and p["configured"] is False and p["contact_available"] is False
    assert p["availability"] == {"source": "not_connected", "slots": [], "note": p["availability"]["note"]} and "not connected" in p["availability"]["note"]
    assert p["booking"]["mode"] == "request" and p["booking"]["external_page"] is None and "does not book a time" in p["booking"]["note"]
    assert client.get("/api/v1/clinic/profile").status_code == 401


def test_only_the_clinic_staff_can_set_the_profile_and_it_is_validated(client):
    assert client.put("/api/v1/clinic/profile", json=PROFILE, headers=tok(client, "patient1")).status_code == 403
    d = tok(client, "doctor1")
    assert client.put("/api/v1/clinic/profile", json={**PROFILE, "phone": "call me maybe"}, headers=d).status_code == 422
    assert client.put("/api/v1/clinic/profile", json={**PROFILE, "booking_url": "http://insecure.example.com"}, headers=d).status_code == 422       # a booking page must be https
    assert client.put("/api/v1/clinic/profile", json={**PROFILE, "email": "not-an-email"}, headers=d).status_code == 422
    ok = client.put("/api/v1/clinic/profile", json=PROFILE, headers=d)
    assert ok.status_code == 200 and ok.json()["profile"]["clinic_name"] == "Cosmo Skin Clinic"
    p = client.get("/api/v1/clinic/profile", headers=tok(client, "patient1")).json()
    assert p["configured"] and p["contact_available"] and p["profile"]["booking_url"] == "https://book.example.com/cosmo" and p["availability"]["slots"] == []
    audit = db.q("SELECT detail FROM audit WHERE action='clinic.profile' ORDER BY ts DESC LIMIT 1")[0]["detail"]
    assert "98765" not in audit and "desk@example.com" not in audit                                                         # the audit says which fields were filled in, never the values


# ---------------------------------------------------------------- requests
def req(client, h, **kw):
    return client.post("/api/v1/appointments/requests", json={"reason": "Pimples that are not clearing", **kw}, headers=h)


def test_a_request_is_not_a_booking_and_stays_the_patients_own(client):
    p1, p2, doc = tok(client, "patient1"), tok(client, "patient2"), tok(client, "doctor1")
    r = req(client, p1, preferred="weekday evenings", contact_method="phone", contact_detail="+91 98765 11111")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "requested" and j["confirmed_for"] is None and j["urgency"] == "routine" and "contact_detail" not in j
    assert [x["id"] for x in client.get("/api/v1/appointments/requests", headers=p1).json()["requests"]] == [j["id"]]
    assert client.get("/api/v1/appointments/requests", headers=p2).json()["requests"] == []                                    # another patient cannot see it
    assert client.post(f"/api/v1/appointments/requests/{j['id']}/cancel", headers=p2).status_code == 404                        # nor change it
    q = client.get("/api/v1/clinic/appointments", headers=doc).json()["requests"]
    assert q[0]["id"] == j["id"] and q[0]["contact_detail"] == "+91 98765 11111" and q[0]["patient"]                              # the clinic sees how to reach the patient
    assert client.get("/api/v1/clinic/appointments", headers=p1).status_code == 403
    n = db.q("SELECT title FROM notifications WHERE kind='appointment_request'")
    assert n and all("appointment request" in x["title"].lower() for x in n)
    assert all("Pimples" not in x["title"] for x in n)                                                                       # the notification does not carry the reason


def test_a_request_is_validated_and_limited(client):
    p1 = tok(client, "patient1")
    assert client.post("/api/v1/appointments/requests", json={"reason": "x"}, headers=p1).status_code == 422
    assert req(client, p1, contact_method="phone", contact_detail="abc").status_code == 422
    assert req(client, p1, contact_method="email", contact_detail="nobody").status_code == 422
    assert req(client, p1, contact_method="carrier-pigeon").status_code == 422
    assert req(client, p1, service_id="no-such-service").status_code == 422
    assert req(client, p1, analysis_id="not-mine").status_code == 404                                                           # a photo assessment can only be attached by its owner
    ids = [req(client, p1).json()["id"] for _ in range(3)]
    over = req(client, p1)
    assert over.status_code == 409 and "already have 3 requests" in over.text                                                  # no flooding the clinic
    assert client.post(f"/api/v1/appointments/requests/{ids[0]}/cancel", headers=p1).json()["status"] == "cancelled"
    assert req(client, p1).status_code == 200                                                                                  # a cancelled request frees a place
    assert client.post(f"/api/v1/appointments/requests/{ids[0]}/cancel", headers=p1).status_code == 409                      # and cannot be cancelled twice


def test_a_real_service_can_be_chosen_and_an_unlisted_one_cannot(client):
    doc = tok(client, "doctor1")
    sid = client.post("/api/clinic/price-items", json={"name": "Acne consultation", "unit_price": 800, "currency": "INR", "category": "Consultation", "description": "First visit"}, headers=doc).json()["id"]
    r = req(client, tok(client, "patient1"), service_id=sid)
    assert r.status_code == 200 and r.json()["service_name"] == "Acne consultation"
    db.x("DELETE FROM price_items WHERE id=?", (sid,))


def test_only_staff_can_confirm_and_a_confirmation_needs_the_time_they_agreed_in_their_own_words(client):
    p1, doc = tok(client, "patient1"), tok(client, "doctor1")
    rid = req(client, p1).json()["id"]
    assert client.post(f"/api/v1/clinic/appointments/{rid}", json={"status": "confirmed"}, headers=p1).status_code == 403           # a patient cannot confirm their own request
    assert client.post(f"/api/v1/clinic/appointments/{rid}", json={"status": "confirmed"}, headers=doc).status_code == 422          # no time, no confirmation
    assert client.post(f"/api/v1/clinic/appointments/{rid}", json={"status": "teleported"}, headers=doc).status_code == 422
    seen = client.post(f"/api/v1/clinic/appointments/{rid}", json={"status": "contacted", "note": "Called, left a message."}, headers=doc).json()
    assert seen["status"] == "contacted"
    mine = client.get("/api/v1/appointments/requests", headers=p1).json()["requests"][0]
    assert mine["status"] == "contacted" and mine["confirmed_for"] is None and mine["staff_note"] == "Called, left a message."
    done = client.post(f"/api/v1/clinic/appointments/{rid}", json={"status": "confirmed", "confirmed_for": "Tuesday 28 October, 4:30 pm"}, headers=doc).json()
    assert done["status"] == "confirmed" and done["confirmed_for"] == "Tuesday 28 October, 4:30 pm"
    mine = client.get("/api/v1/appointments/requests", headers=p1).json()["requests"][0]
    assert mine["status"] == "confirmed" and mine["confirmed_for"] == "Tuesday 28 October, 4:30 pm"
    assert [n["title"] for n in db.q("SELECT title FROM notifications WHERE user_id='patient1' AND kind='appointment_update' ORDER BY created_at")][-1] == "Your appointment is confirmed"
    assert client.post(f"/api/v1/appointments/requests/{rid}/cancel", headers=p1).status_code == 409                               # a confirmed appointment is changed with the clinic, not by a button
    other = req(client, p1).json()["id"]
    client.post(f"/api/v1/appointments/requests/{other}/cancel", headers=p1)
    assert client.post(f"/api/v1/clinic/appointments/{other}", json={"status": "contacted"}, headers=doc).status_code == 409        # the patient withdrew it: staff cannot revive it


def test_deleting_my_data_removes_my_requests(client):
    r = client.post("/api/admin/users", json={"id": "bookerase1", "name": "Erase Me", "role": "patient", "password": "Cosmo-test-passw0rd!"}, headers=tok(client, "admin1"))
    assert r.status_code in (200, 409), r.text
    h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": "bookerase1", "password": "Cosmo-test-passw0rd!"}).json()["token"]}
    req(client, h)
    assert db.q1("SELECT COUNT(*) c FROM appointment_requests WHERE patient_id='bookerase1'")["c"] == 1
    assert client.post("/api/me/delete-my-data", headers=h).status_code == 200                                                  # the person's own erase-my-data route
    assert db.q1("SELECT COUNT(*) c FROM appointment_requests WHERE patient_id='bookerase1'")["c"] == 0


# ---------------------------------------------------------------- what the assistant says
def test_the_assistant_says_there_is_no_live_calendar_and_shows_only_what_the_clinic_entered(client):
    p1 = tok(client, "patient1")
    r = ask(client, p1, "I want to book an appointment with a dermatologist")
    assert r["route"]["kind"] == "booking" and r["route"]["sub"] == "appointment"
    assert "can't show or book a slot" in r["answer"] and "not a confirmed appointment" in r["answer"] and "has not added its contact details" in r["answer"]
    assert not re.search(r"\b\d{1,2}:\d{2}\b|\b(?:am|pm)\b", r["answer"]) and [a["label"] for a in r["card"]["actions"]] == ["Send an appointment request"]
    assert r["availability"] == "not_connected"
    client.put("/api/v1/clinic/profile", json=PROFILE, headers=tok(client, "doctor1"))
    r2 = ask(client, p1, "How do I book an appointment?")
    assert "Phone: +91 98765 43210" in r2["answer"] and "Hours: Mon-Sat 10:00-18:00" in r2["answer"] and "online booking page" in r2["answer"]
    acts = {a["label"]: a for a in r2["card"]["actions"]}
    assert acts["Open the clinic's booking page"]["href"] == "https://book.example.com/cosmo" and acts["Call the clinic"]["href"] == "tel:+919876543210" and "Send an appointment request" in acts


def test_services_and_prices_are_the_clinics_own_list_and_nothing_else(client):
    p1, doc = tok(client, "patient1"), tok(client, "doctor1")
    empty = ask(client, p1, "what services and prices do you have?")
    if not db.q("SELECT id FROM price_items WHERE active=1 AND approved_by!=''"):
        assert "has not published a price list yet" in empty["answer"]
    pid = client.post("/api/clinic/price-items", json={"name": "Chemical peel", "unit_price": 3500, "currency": "INR", "category": "Procedures", "sessions": 3}, headers=doc).json()["id"]
    r = ask(client, p1, "how much does a chemical peel cost?")
    assert r["route"]["sub"] == "services" and "Chemical peel: 3500.00 INR (3 sessions)" in r["answer"] and "final cost depends on the plan your doctor agrees with you" in r["answer"]
    db.x("UPDATE price_items SET approved_by='' WHERE id=?", (pid,))                                                           # a price the clinic has not approved is not shown
    assert "Chemical peel" not in ask(client, p1, "what are your prices").get("answer", "")
    db.x("DELETE FROM price_items WHERE id=?", (pid,))


def test_a_same_day_sign_offers_a_same_day_request_and_a_prompt_sign_a_normal_one(client):
    p1 = tok(client, "patient1")
    r = ask(client, p1, "I want to book an appointment, a red area on my arm is spreading fast and is hot and painful")
    assert r["status"] == "escalated" and any(a.get("route") == "/book?urgency=urgent" for a in r["card"]["escalation"]["actions"] + r["card"]["actions"])
    sameday = req(client, p1, urgency="urgent").json()
    assert sameday["urgency"] == "urgent"
    q = client.get("/api/v1/clinic/appointments", headers=tok(client, "doctor1")).json()["requests"]
    assert q[0]["id"] == sameday["id"] and q[0]["urgency"] == "urgent"                                                          # same-day requests are at the top of the clinic's queue

"""In-app notifications: created by genuine state changes, private to their owner, idempotent, generic in content, removed with the user's data."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import app
from app.services import notify
from tests.test_api import lab_pdf


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(client, user):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": user, "password": "cosmo-dev"}).json()["token"]}


def inbox(client, h, unread=False):
    return client.get("/api/notifications" + ("?unread=true" if unread else ""), headers=h).json()


def kinds(client, h):
    return [n["kind"] for n in inbox(client, h)["items"]]


def jpeg():
    buf = io.BytesIO()
    import numpy as np
    rng = np.random.default_rng(3)
    Image.fromarray((rng.random((600, 600, 3)) * 80 + 120).astype("uint8")).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def test_requires_authentication_and_starts_empty(client):
    assert client.get("/api/notifications").status_code == 401
    assert client.post("/api/notifications/read-all").status_code == 401
    db.x("DELETE FROM notifications")
    assert inbox(client, H(client, "patient1")) == {"unread": 0, "items": []}


def test_send_is_idempotent_and_never_raises():
    db.x("DELETE FROM notifications")
    assert notify.send("patient1", "x", "t", dedupe="k1") is True
    assert notify.send("patient1", "x", "t", dedupe="k1") is False                   # same event, same user: one notification
    assert notify.send("patient2", "x", "t", dedupe="k1") is True                    # same key for another user is independent
    assert notify.send("patient1", "x", "t") and notify.send("patient1", "x", "t")   # no dedupe key: every call is stored
    assert notify.send(None, "x", "t") is False                                       # NOT NULL violation is swallowed, not raised
    assert db.q1("SELECT COUNT(*) c FROM notifications WHERE user_id='patient1'")["c"] == 3
    db.x("DELETE FROM notifications")


def test_document_upload_notifies_the_patient_and_every_doctor_without_leaking_content(client):
    db.x("DELETE FROM notifications")
    p, d = H(client, "patient1"), H(client, "doctor1")
    r = client.post("/api/documents", files={"file": ("secret-name.pdf", lab_pdf(["Ferritin     8     ng/mL     15 - 150  L"]))}, headers=p)
    assert r.status_code == 200
    did = r.json()["id"]
    mine = inbox(client, p)["items"]
    assert [n["kind"] for n in mine] == ["report_ready"] and mine[0]["link"] == f"/reports/{did}"
    theirs = inbox(client, d)["items"]
    assert "submission" in [n["kind"] for n in theirs]
    blob = " ".join(f"{n['title']} {n['body']}" for n in mine + theirs).lower()
    for secret in ("ferritin", "secret-name", "ng/ml", "8 "):                        # no values, file names or findings in a notification
        assert secret not in blob
    assert "report_ready" not in kinds(client, H(client, "patient2"))               # other patients see nothing


def test_clinician_actions_notify_the_right_patient_once(client):
    p, d = H(client, "patient1"), H(client, "doctor1")
    did = client.get("/api/documents", headers=p).json()[0]["id"]
    db.x("DELETE FROM notifications")
    assert client.post(f"/api/clinic/documents/{did}/review", headers=d).status_code == 200
    client.post(f"/api/clinic/documents/{did}/review", headers=d)                    # repeating the action does not repeat the notification
    assert kinds(client, p) == ["report_reviewed"]
    assert kinds(client, H(client, "patient2")) == []

    up = client.post("/api/images", data={"region": "face"}, files={"file": ("p.jpg", jpeg())}, headers=p)
    assert up.status_code == 200, up.text
    iid = up.json()["id"]
    if up.json()["report"]["verdict"] != "rejected":
        assert "submission" in kinds(client, d)
        assert client.post(f"/api/clinic/images/{iid}/review", headers=d).status_code == 200
        assert kinds(client, p).count("photo_reviewed") == 1

    plan = client.put(f"/api/clinic/care-plans/patient1", json={"assessment": "Test assessment", "findings": [], "stages": [], "notes": "", "recommended_item_ids": []}, headers=d)
    assert plan.status_code == 200, plan.text
    pid = client.get("/api/clinic/care-plans/patient1", headers=d).json()[0]["id"]
    assert client.post(f"/api/clinic/care-plans/{pid}/approve", headers=d).status_code == 200
    assert "plan_approved" in kinds(client, p)

    item = client.post("/api/clinic/price-items", json={"name": "Notify test", "unit_price": 10, "currency": "INR"}, headers=d).json()["id"]
    q = client.post("/api/quotes", json={"items": [{"item_id": item, "qty": 1}]}, headers=p).json()["id"]
    assert "quote_requested" in kinds(client, d)
    client.post(f"/api/clinic/quotes/{q}/approve", headers=d)
    assert "quote_approved" in kinds(client, p)
    assert H(client, "admin1") and "quote_approved" not in kinds(client, H(client, "admin1"))


def test_read_state_is_per_user_and_cannot_be_changed_by_others(client):
    p, p2, d = H(client, "patient1"), H(client, "patient2"), H(client, "doctor1")
    items = inbox(client, p)["items"]
    assert items and inbox(client, p)["unread"] == len(items)
    nid = items[0]["id"]
    assert client.post(f"/api/notifications/{nid}/read", headers=p2).status_code == 404         # not yours: indistinguishable from missing
    assert client.post(f"/api/notifications/{nid}/read", headers=d).status_code == 404
    assert inbox(client, p)["unread"] == len(items)
    assert client.post(f"/api/notifications/{nid}/read", headers=p).json()["unread"] == len(items) - 1
    first_read = db.q1("SELECT read_at FROM notifications WHERE id=?", (nid,))["read_at"]
    client.post(f"/api/notifications/{nid}/read", headers=p)
    assert db.q1("SELECT read_at FROM notifications WHERE id=?", (nid,))["read_at"] == first_read   # reading twice keeps the first timestamp
    assert client.post("/api/notifications/nope/read", headers=p).status_code == 404
    assert len(inbox(client, p, unread=True)["items"]) == len(items) - 1
    assert client.post("/api/notifications/read-all", headers=p).json() == {"unread": 0}
    assert inbox(client, p, unread=True)["items"] == []
    assert client.post("/api/notifications/read-all", headers=p2).json()["unread"] == 0


def test_notifications_are_deleted_with_the_patients_data(client):
    p = H(client, "patient2")
    notify.send("patient2", "x", "t")
    assert inbox(client, p)["items"]
    assert client.post("/api/me/delete-my-data", headers=p).status_code == 200
    assert inbox(client, p)["items"] == []

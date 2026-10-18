"""Quotation validity: approval stamps an expiry from clinic settings, expired quotations are not offers (assistant, PDF, UI status), re-approval renews."""
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.quotes import service as quotes


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def H(client, u):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


@pytest.fixture
def setup(client):
    d, p = H(client, "doctor1"), H(client, "patient1")
    item = client.post("/api/clinic/price-items", json={"name": "Expiry test", "unit_price": 100, "currency": "INR"}, headers=d).json()["id"]
    yield client, d, p, item
    quotes.set_config({**quotes.get_config(), "validity_days": 30})
    db.x("DELETE FROM quotes WHERE lines LIKE '%Expiry test%'")                       # other modules assume patient1 has no approved quotation
    db.x("DELETE FROM price_items WHERE name='Expiry test'")


def new_quote(client, p, item):
    return client.post("/api/quotes", json={"items": [{"item_id": item, "qty": 1}]}, headers=p).json()["id"]


def mine(client, p, qid):
    return next(q for q in client.get("/api/quotes", headers=p).json() if q["id"] == qid)


def test_effective_status_rules():
    now = time.time()
    assert quotes.effective_status({"status": "approved", "valid_until": now + 10}, now) == "approved"
    assert quotes.effective_status({"status": "approved", "valid_until": now - 1}, now) == "expired"
    assert quotes.effective_status({"status": "approved", "valid_until": None}, now) == "approved"
    assert quotes.effective_status({"status": "draft", "valid_until": now - 1}, now) == "draft"             # a draft is not an offer: it never "expires"


def test_config_validation(setup):
    client, d, p, _ = setup
    base = {"tax_percent": 0, "payment_options": [], "inclusions": [], "terms": ""}
    assert client.put("/api/clinic/quote-config", json={**base, "validity_days": 45}, headers=d).json()["validity_days"] == 45
    for bad in (-1, 731):
        assert client.put("/api/clinic/quote-config", json={**base, "validity_days": bad}, headers=d).status_code == 422
    assert client.put("/api/clinic/quote-config", json={**base, "validity_days": 5}, headers=p).status_code == 403


def test_approval_stamps_the_expiry_from_the_clinic_setting(setup):
    client, d, p, item = setup
    client.put("/api/clinic/quote-config", json={"tax_percent": 0, "validity_days": 10, "payment_options": [], "inclusions": [], "terms": ""}, headers=d)
    q = new_quote(client, p, item)
    assert mine(client, p, q)["valid_until"] is None and mine(client, p, q)["effective_status"] == "draft"
    before = time.time()
    client.post(f"/api/clinic/quotes/{q}/approve", headers=d)
    row = mine(client, p, q)
    assert row["effective_status"] == "approved" and abs(row["valid_until"] - (before + 10 * 86400)) < 60


def test_zero_days_means_the_quotation_never_expires(setup):
    client, d, p, item = setup
    client.put("/api/clinic/quote-config", json={"tax_percent": 0, "validity_days": 0, "payment_options": [], "inclusions": [], "terms": ""}, headers=d)
    q = new_quote(client, p, item)
    client.post(f"/api/clinic/quotes/{q}/approve", headers=d)
    assert mine(client, p, q)["valid_until"] is None and mine(client, p, q)["effective_status"] == "approved"


def test_an_expired_quotation_is_not_offered_by_the_assistant_the_home_suggestions_or_the_pdf(setup):
    client, d, p, item = setup
    q = new_quote(client, p, item)
    client.post(f"/api/clinic/quotes/{q}/approve", headers=d)
    cid = client.post("/api/conversations", headers=p).json()["id"]
    live = client.post(f"/api/conversations/{cid}/messages", json={"text": "How much is my quotation?"}, headers=p).json()
    assert "100.00" in live["answer"]
    db.x("UPDATE quotes SET valid_until=? WHERE id=?", (db.now() - 5, q))
    assert mine(client, p, q)["effective_status"] == "expired"
    other = [x for x in client.get("/api/quotes", headers=p).json() if x["id"] != q and x["effective_status"] == "approved"]
    gone = client.post(f"/api/conversations/{cid}/messages", json={"text": "How much is my quotation?"}, headers=p).json()
    if not other:
        assert "100.00" not in gone["answer"] and "approved quotation" in gone["answer"].lower()
    pdf = client.get(f"/api/quotes/{q}/pdf", headers=p)
    text = pymupdf.open(stream=pdf.content, filetype="pdf")[0].get_text()
    assert pdf.status_code == 200 and "EXPIRED" in text and "Valid until" in text           # the lapsed date is still printed, next to the warning


def test_the_pdf_of_a_valid_quotation_shows_its_validity_date(setup):
    client, d, p, item = setup
    q = new_quote(client, p, item)
    client.post(f"/api/clinic/quotes/{q}/approve", headers=d)
    text = pymupdf.open(stream=client.get(f"/api/quotes/{q}/pdf", headers=p).content, filetype="pdf")[0].get_text()
    assert "Valid until" in text and "EXPIRED" not in text


def test_reapproval_renews_an_expired_quotation(setup):
    client, d, p, item = setup
    q = new_quote(client, p, item)
    client.post(f"/api/clinic/quotes/{q}/approve", headers=d)
    db.x("UPDATE quotes SET valid_until=? WHERE id=?", (db.now() - 5, q))
    assert mine(client, p, q)["effective_status"] == "expired"
    client.post(f"/api/clinic/quotes/{q}/approve", headers=d)
    assert mine(client, p, q)["effective_status"] == "approved"
    assert db.q1("SELECT COUNT(*) c FROM audit WHERE action='quote.approve' AND target=?", (q,))["c"] == 2

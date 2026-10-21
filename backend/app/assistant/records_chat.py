"""Answers built from the person's OWN records and the clinic's OWN configuration (categories C, D, E and F): a medical report, their history, their care plan, appointments and services.

Nothing here is guessed or invented. A report is explained from what it prints (value, unit, printed range, printed flag, page); a result is called 'outside the printed range' only when the report
printed a range, and 'no range printed' when it did not. A care plan is the clinician's own text. Services and prices are the clinic's approved list; contact details and hours are what the
clinic entered; availability is never shown because no calendar is connected, and a request is never called a booking. Everything is read through the signed-in person's own context."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from .. import db
from ..agent import agent as agent_loop, planner as agent_planner
from ..booking import service as booking
from ..services import explanations
from . import compose
from .compose import Reply

log = logging.getLogger("cosmo.assistant")
FLAG_TEXT = {"printed_flag_high": ("outside", "Marked high by the report"), "printed_flag_low": ("outside", "Marked low by the report"), "outside_printed_range": ("outside", "Outside the printed range"),
             "within_printed_range": ("within", "Within the printed range")}
AGENT_KINDS = ("list_documents", "list_medications", "list_quotes", "list_prices", "request_quote", "list_notifications", "mark_notifications_read", "request_review", "flag_value")


def _day(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%d %b %Y")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _documents(owner_id: str) -> list[dict]:
    return db.q("SELECT id,filename,doc_type,doc_date,page_count,review_status,created_at FROM documents WHERE owner_id=? AND deleted=0 AND status='processed' ORDER BY created_at DESC LIMIT 50", (owner_id,))


def has_reports(owner_id: str) -> bool:
    return bool(_documents(owner_id))


# ---------------------------------------------------------------- C: a medical report
def no_reports(route: dict) -> Reply:
    card = compose.new_card("report", title="No medical report yet",
                            summary="You have not uploaded a medical report yet. Upload one (a PDF or a clear photo of it) and I will read it, show the values with their units, printed ranges and dates, "
                                    "and explain what it prints in plain words. I do not interpret what a result means for you: your doctor does that.",
                            actions=[compose.UPLOAD_REPORT, compose.BOOK], context={})
    return compose.finalize(card, status="needs_input", reason="no_report", route=route, screened=False)


def which_report(docs: list[dict], route: dict) -> Reply:
    card = compose.new_card("report", title="Which report do you mean?", summary="You have more than one report. Choose one, or say the file name or 'the latest one'.",
                            actions=[compose.act_message(f"doc-{d['id']}", f"{d['filename']} ({_day(d['created_at'])})", f"Explain my report {d['filename']}") for d in docs[:6]], context={})
    return compose.finalize(card, status="needs_input", reason="document_not_specified", route=route, screened=False)


def explain_report(user: dict, doc: dict, route: dict) -> Reply:
    try:
        ex = explanations.build(user, doc["id"])
    except explanations.NotFound:
        return no_reports(route)
    found = ex["sections"][0] if ex["sections"] else {"items": [], "summary": ""}
    ids = [i["field_id"] for i in found["items"] if i.get("field_id")]
    rows = {r["id"]: r for r in db.q(f"SELECT id,name,value,unit,ref_range,flag,source_page FROM doc_fields WHERE document_id=? AND id IN ({','.join('?' for _ in ids) or 'NULL'})", (doc["id"], *ids))} if ids else {}
    table = []
    for it in found["items"]:
        f = rows.get(it.get("field_id"))
        if not f:
            continue
        kind, text = FLAG_TEXT.get(f["flag"] or "", ("unknown", "No range printed: it cannot be compared" if not f["ref_range"] else "No comparison was made"))
        table.append({"field_id": f["id"], "name": f["name"], "value": f["value"], "unit": f["unit"] or "", "range": f["ref_range"] or "", "status": kind, "status_text": text, "page": f["source_page"]})
    outside = [t for t in table if t["status"] == "outside"]
    summary = (found.get("summary") or "") + " This explains what the report prints. It does not say what the results mean for you."
    secs = []
    if table:
        secs.append(compose.section("outside", "Marked or outside the printed range", [f"{t['name']}: {t['value']} {t['unit']}".strip() + f" ({t['status_text'].lower()}; page {t['page']})" for t in outside] or ["Nothing was marked or outside the printed range."], "avoid" if outside else "info"))
    cites = [i["citation"] for i in found["items"] if i.get("citation")][:6]
    uncertain = next((s for s in ex["sections"] if s.get("id") == "uncertain"), None)
    questions = next((s for s in ex["sections"] if s.get("id") == "questions"), None)
    if uncertain:
        secs.append(compose.section("cannot", "What a report cannot tell you", [uncertain.get("summary") or ""] + [i.get("text", "") for i in uncertain.get("items", [])][:2]))
    if questions:
        secs.append(compose.section("questions", questions.get("title") or "Questions you could ask your doctor", [i.get("text", "") for i in questions.get("items", [])][:4]))
    card = compose.new_card("report", title=doc["filename"], summary=summary.strip(), sections=secs, context={"document_id": doc["id"]},
                            report={"document": {"id": doc["id"], "filename": doc["filename"], "type": doc["doc_type"], "date": doc.get("doc_date") or _day(doc["created_at"]), "review_status": doc["review_status"]},
                                    "items": table, "extraction_warnings": ex.get("extraction_warnings", [])},
                            review={"tier": "record", "label": "Read from your own report. Values are copied as printed and a clinician can confirm them." if doc["review_status"] != "reviewed_by_clinician" else "Read from your own report and reviewed by a clinician."},
                            actions=[{"id": "open", "label": "Open the full explanation", "kind": "report", "document_id": doc["id"]}, compose.act_message("review", "Ask the clinic to review this report", "Ask the clinic to review my latest report"), compose.UPLOAD_REPORT])
    return compose.finalize(card, route=route, screened=False, sources=[{"kind": "document", "document_id": c["document_id"], "page": c.get("page"), "excerpt": (c.get("excerpt") or "")[:300], "provenance": "report_fact", "citation": c} for c in cites])


def report(user: dict, text: str, route: dict, request_id: str = "") -> Reply | None:
    """Explain a report, or hand the other document intents to the agent. None means: this is a question about a value, let the retrieval answer it (engine.answer)."""
    docs = _documents(user["id"])
    kind = agent_planner.classify(text).kind
    if not docs and kind in ("explain_document", "list_medications"):          # a general question ("what is a reference range?") may still be answered from the clinic's approved articles: see dispatch
        return no_reports(route)
    if kind == "explain_document":
        d, how = agent_planner.pick_document(text, [{**x} for x in docs])
        if d is None:
            return which_report(docs, route) if docs else no_reports(route)
        return explain_report(user, next(x for x in docs if x["id"] == d["id"]), route)
    if kind in AGENT_KINDS:
        return from_agent(agent_loop.run(user, text, request_id=request_id), route)
    return None


def from_agent(out, route: dict) -> Reply:
    """The agent's answer (documents, medicines, quotations, prices, notifications, a proposed change) as a reply. A proposal waits for the person's confirmation: nothing is done here."""
    status = {"answered": "answered", "abstained": "abstained", "refused": "abstained", "clarification": "needs_input", "needs_confirmation": "needs_confirmation", "urgent": "escalated", "error": "abstained"}.get(out.status, "answered")
    acts = [compose.act_message("c" + a["id"][:6], a["summary"], "") | {"kind": "confirm", "proposal_id": a["id"], "tool": a.get("tool", "")} for a in out.actions]
    card = compose.new_card("report" if out.intent in ("explain_document", "list_documents", "list_medications") else "records", title="", summary=out.answer, actions=acts, context={})
    srcs = [{**c} for c in out.sources]
    return compose.finalize(card, status=status, abstained=out.status in ("abstained", "refused", "urgent"), reason=out.reason or ("clinical_decision" if out.status == "refused" else ""), route=route, sources=srcs,
                            screened=False, extra={"intent": out.intent, "proposals": out.actions})


# ---------------------------------------------------------------- D: history, E: care plan
def history(user: dict, route: dict) -> Reply:
    uid = user["id"]
    docs = _documents(uid)[:3]
    imgs = db.q("SELECT id,region,verdict,created_at,clinician_review,stored_path FROM images WHERE owner_id=? AND deleted=0 ORDER BY created_at DESC LIMIT 3", (uid,))
    plan = db.q1("SELECT id,reviewed_at FROM care_plans WHERE patient_id=? AND status='approved' ORDER BY updated_at DESC LIMIT 1", (uid,))
    reqs = booking.my_requests(uid)[:3]
    secs = [compose.section("reports", "Your reports", [f"{d['filename']} ({_day(d['created_at'])})" for d in docs] or ["No reports uploaded yet."]),
            compose.section("photos", "Your photos", [f"{i['region'].replace('_', ' / ').title()} photo, {_day(i['created_at'])}: " + ("reviewed by a clinician" if i["clinician_review"] == "reviewed" else "check result " + str(i["verdict"])) for i in imgs] or ["No photos yet."]),
            compose.section("plan", "Your care plan", [f"Approved on {_day(plan['reviewed_at'])}" if plan else "No care plan has been approved yet."]),
            compose.section("appointments", "Your appointment requests", [f"{r['reason'][:60]}: {r['status']}" + (f" ({r['confirmed_for']})" if r["confirmed_for"] else "") for r in reqs] or ["No appointment requests."])]
    card = compose.new_card("history", title="Your records at a glance", summary="This is what is on your account. Nothing is shared with anyone you have not chosen.", sections=secs,
                            actions=[compose.act_nav("h-history", "Open my history", "/history"), compose.act_nav("h-reports", "Open my reports", "/reports"), compose.BOOK], context={})
    return compose.finalize(card, route=route, screened=False)


def care_plan(user: dict, route: dict) -> Reply:
    from ..routers import care
    p = db.q1("SELECT * FROM care_plans WHERE patient_id=? AND status='approved' ORDER BY updated_at DESC LIMIT 1", (user["id"],))
    if not p:
        card = compose.new_card("care_plan", title="No care plan yet", summary="Your clinician writes a care plan after looking at your photos and reports. It will appear here and on the Care screen once it is approved.",
                                actions=[compose.TAKE_PHOTO, compose.BOOK, compose.act_nav("cp", "Open the Care screen", "/care")], context={})
        return compose.finalize(card, status="needs_input", reason="no_plan", route=route, screened=False)
    out = care._plan_out(p)
    doc = care._doctor_card(p["doctor_id"])
    secs = []
    if out["findings"]:
        secs.append(compose.section("findings", "Findings", out["findings"]))
    if out["stages"]:
        secs.append(compose.section("stages", "Stages", [f"{s['title']}: {s['status'].replace('_', ' ')}" + (f". {s['detail']}" if s.get("detail") else "") for s in out["stages"]]))
    if out["notes"]:
        secs.append(compose.section("notes", "Notes from your clinician", [out["notes"]]))
    card = compose.new_card("care_plan", title="Your care plan", summary=out["assessment"], sections=secs, context={},
                            review={"tier": "clinician", "label": f"Written and approved by {doc['name'] if doc else 'your clinician'}" + (f" on {_day(out['reviewed_at'])}" if out["reviewed_at"] else "")},
                            actions=[compose.act_nav("cp", "Open the Care screen", "/care"), compose.BOOK])
    return compose.finalize(card, route=route, screened=False)


# ---------------------------------------------------------------- F: appointments, services, quotations
def booking_reply(user: dict, text: str, route: dict, sub: str, tri=None) -> Reply:
    prof = booking.public_profile()
    p = prof["profile"]
    open_reqs = [r for r in booking.my_requests(user["id"]) if r["status"] in booking.OPEN_STATUSES]
    secs: list[dict] = []
    if sub in ("services", "quote"):
        svc = prof["services"]
        if svc:
            secs.append(compose.section("services", "The clinic's services and prices", [f"{s['name']}: {s['unit_price']:.2f} {s['currency']}" + (f" ({s['sessions']} sessions)" if (s.get("sessions") or 1) > 1 else "") for s in svc[:14]]))
            secs.append(compose.section("price_note", "About prices", ["These are the clinic's approved prices. The final cost depends on the plan your doctor agrees with you; ask the clinic for a quotation for a firm figure."]))
        else:
            secs.append(compose.section("services", "Services and prices", ["The clinic has not published a price list yet. You can send a request and the team will tell you."]))
        title = "Services and prices"
        summary = "Here is what the clinic has published."
    else:
        title = "Book a dermatologist appointment"
        if p.get("booking_url"):
            summary = "The clinic has an online booking page where you can see its available times. You can also send a request and the team will contact you."
        else:
            summary = ("Live appointment times are not connected here, so I can't show or book a slot. You can send the clinic a request: the team contacts you to agree a time. "
                       "A request is not a confirmed appointment until the clinic confirms it.")
    contact = [x for x in (f"Clinic: {p['clinic_name']}" if p.get("clinic_name") else "", f"Phone: {p['phone']}" if p.get("phone") else "", f"E-mail: {p['email']}" if p.get("email") else "",
                           f"Address: {p['address']}" if p.get("address") else "", f"Hours: {p['hours']}" if p.get("hours") else "", p.get("notes") or "") if x]
    secs.append(compose.section("contact", "The clinic", contact or ["The clinic has not added its contact details here yet. Your request still reaches its team in the app."]))
    if open_reqs:
        secs.append(compose.section("open", "Your open requests", [f"{r['reason'][:60]}: {r['status']}" for r in open_reqs]))
    acts = [compose.act_nav("book", "Send an appointment request", "/book")]
    if p.get("booking_url"):
        acts.insert(0, {"id": "booking-page", "label": "Open the clinic's booking page", "kind": "external", "href": p["booking_url"]})
    if p.get("phone"):
        acts.append({"id": "call", "label": "Call the clinic", "kind": "external", "href": "tel:" + "".join(ch for ch in p["phone"] if ch.isdigit() or ch == "+")})
    if sub in ("services", "quote"):
        acts.append(compose.act_message("quote", "Ask for a quotation", "I would like a quotation"))
    card = compose.new_card("booking", title=title, summary=summary, sections=secs, actions=acts, context={},
                            review={"tier": "clinic", "label": "Services, prices and contact details are the clinic's own."})
    if tri is not None and tri.escalated():
        card["escalation"] = compose.escalation(tri)
    return compose.finalize(card, route=route, screened=False, extra={"availability": "not_connected"})

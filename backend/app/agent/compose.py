"""Answer text for the agent, built from tool results with fixed wording. Nothing here calls a model, and nothing here repeats a document's words except inside quotation marks, which the
shared safety policy treats as the document speaking (labelled) rather than the assistant. Every statement that rests on a record carries the citation the tool returned for it."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..ai import safety


@dataclass
class Answer:
    text: str
    status: str = "answered"            # answered | abstained | clarification | needs_confirmation | refused | urgent | error
    citations: list[dict] = field(default_factory=list)
    data: dict | None = None            # structured payload for the screen (the full explanation, the document list ...)
    evidence: list[str] = field(default_factory=list)   # texts the answer quotes verbatim (so the safety policy can tell a quotation from the assistant speaking)
    reason: str = ""
    actions: list[dict] = field(default_factory=list)   # proposed state-changing actions awaiting the person's confirmation


HELP = ("I can help you with your own records and the clinic's approved information:\n"
        "- see the documents you uploaded and what was read from them\n"
        "- explain what a document says, showing the page and line each statement comes from\n"
        "- list the medicines printed on a prescription, exactly as written\n"
        "- find answers in your documents and in clinic-approved information\n"
        "- show your quotations, the price list and your notifications\n"
        "- if you confirm it, ask the clinic for a quotation, ask the team to look at a document, mark a value as possibly wrong, or mark notifications as read\n"
        "I can't diagnose, choose medicines or doses, or decide whether something is urgent.")
NO_DOCS = "No documents have been uploaded yet."


def _day(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%d %b %Y")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def help_text() -> Answer:
    return Answer(HELP)


def documents(res: dict) -> Answer:
    docs = res["documents"]
    if not docs:
        return Answer(NO_DOCS, data=res)
    lines = [f'{i}. "{d["filename"]}" - {d["doc_type"].replace("_", " ")}, {d["page_count"]} page(s), added {_day(d["created_at"])}' + ("" if d["review_status"] == "pending_clinician_review" else f', {d["review_status"].replace("_", " ")}')
             for i, d in enumerate(docs[:20], 1)]
    return Answer(f"Your account holds {len(docs)} document(s):\n" + "\n".join(lines) + ("\nOnly the 20 newest are listed." if len(docs) > 20 else ""), data=res)


def which_document(docs: list[dict], what: str) -> Answer:
    if not docs:
        return Answer(NO_DOCS, status="answered")
    names = "\n".join(f'- "{d["filename"]}" ({_day(d["created_at"])})' for d in docs[:10])
    return Answer(f"Which document do you mean? {what}\n{names}\nYou can say, for example, \"the latest one\" or use the document's name.", status="clarification", reason="document_not_specified")


def explanation(doc: dict, res: dict) -> Answer:
    ex = res["explanation"]
    if ex.get("abstained"):
        return Answer(f'I could not find individual results I can explain in "{doc["filename"]}". ' + ex["abstention"]["message"], status="abstained", reason="insufficient_evidence", data=ex)
    items = [i for s in ex["sections"][:3] for i in s["items"] if i.get("citation") and "text" in i][:8]
    body = "\n".join(f"- {i['text']}" for i in items)
    more = max(0, sum(len(s["items"]) for s in ex["sections"][:3]) - len(items))
    text = (f'Here is what "{doc["filename"]}" says, with the page each statement comes from:\n{body}' + (f"\n(and {more} more - open the full explanation to see them all)" if more else "")
            + f"\n{ex['sections'][-2]['summary']}\n{ex['clinician_review_message']}")
    return Answer(text, citations=[i["citation"] for i in items], data=ex, evidence=[i["citation"]["excerpt"] for i in items])


def medications(doc: dict, res: dict) -> Answer:
    meds = res["medicines"]
    if not meds:
        extra = " Some text in it looks like instructions to software and was not used." if res.get("hidden_text") else ""
        return Answer(f'I did not find medicine entries in "{doc["filename"]}".{extra}', status="abstained", reason="insufficient_evidence", data=res)
    lines = [f'- page {m["page"]}: "{m["raw"]}"' + (" (a clinician corrected this entry)" if m["review_status"] == "corrected" else "") for m in meds[:20]]
    text = (f'"{doc["filename"]}" lists these medicine entries, exactly as printed:\n' + "\n".join(lines)
            + "\nThis only repeats what is printed. Your doctor or pharmacist can tell you how each medicine is used, and what any abbreviation means for you.")
    return Answer(text, citations=res["citations"][:20], data=res, evidence=[m["raw"] for m in meds])


def quotes(res: dict) -> Answer:
    qs = res["quotes"]
    if not qs:
        return Answer("There are no quotations on your account yet. If you would like one, say which treatment you are interested in and I can ask the clinic to prepare it.", data=res)
    lines = []
    for q in qs[:10]:
        if q["effective_status"] == "approved":
            names = ", ".join(l["name"] + " x" + str(l["qty"]) for l in q["lines"])
            lines.append(f'- Approved: {q["total"]:.2f} {q["currency"]} ({names})')
        elif q["effective_status"] == "draft":
            lines.append("- Waiting for a clinic doctor to review and approve")
        else:
            lines.append(f'- {q["effective_status"].replace("_", " ").capitalize()}')
    return Answer("Your quotations:\n" + "\n".join(lines) + "\nA quotation is an estimate from the clinic's approved price list; the final cost depends on the plan your doctor agrees with you.", data=res)


def prices(res: dict) -> Answer:
    items = res["items"]
    if not items:
        return Answer("The clinic has not published an approved price list yet.", data=res)
    lines = [f'- {i["name"]}: {i["unit_price"]:.2f} {i["currency"]}' for i in items[:20]]
    return Answer("The clinic's approved price list:\n" + "\n".join(lines) + "\nTo ask for a quotation, tell me which of these you are interested in.", data=res)


def notifications(res: dict) -> Answer:
    ns = res["notifications"]
    if not ns:
        return Answer("There are no unread notifications.", data=res)
    return Answer(f"There are {len(ns)} notification(s) for you:\n" + "\n".join(f'- {n["title"]} ({_day(n["created_at"])})' for n in ns[:10]), data=res)


def proposal(action: dict) -> Answer:
    return Answer(f"{action['summary']}\nThis changes something, so nothing has been done yet. Please confirm below if you want me to go ahead; you can also cancel it.",
                  status="needs_confirmation", actions=[action], reason="confirmation_required")


def answer_from_records(question: str, docs: dict | None, kb: dict | None, quarantined: int) -> Answer:
    bullets, cites, evidence = [], [], []
    for h in (docs or {}).get("hits", [])[:3]:
        bullets.append(f'Your document "{h["filename"]}" (page {h["page"]}) says: "{h["excerpt"]}"')
        evidence.append(h["excerpt"])
    for c, h in zip((kb or {}).get("citations", [])[:2], (kb or {}).get("hits", [])[:2]):
        bullets.append(f'Clinic-approved information, "{h["title"]}": "{h["text"]}"')
        evidence.append(h["text"])
        cites.append(c)
    cites = [c for c in (docs or {}).get("citations", [])[:3]] + cites
    note = " Some text in your documents looks like instructions to software, so it was not used." if quarantined else ""
    if not bullets:
        return Answer(safety.ABSTAIN_MSG + note, status="abstained", reason="insufficient_evidence")
    return Answer("Here is what I found that relates to your question:\n- " + "\n- ".join(bullets) + "\nThis repeats what the sources say; it is not a diagnosis. Your doctor can explain what it means for you." + note,
                  citations=cites, evidence=evidence)

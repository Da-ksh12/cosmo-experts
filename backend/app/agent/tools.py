"""The agent's tools: a closed, typed list. Each tool declares who may call it, what it takes (a strict schema that refuses unknown fields), whether it only reads or changes state, how
long it may run, and returns a plain dictionary of data the server read for THIS person.

No tool takes a user id: the person is `ctx.user`, taken from the authenticated session. Every record is fetched with that id in the query itself, so asking for somebody else's record is
the same as asking for one that does not exist. There is no tool that runs a query, a command, a script, opens a path or fetches a URL.

A tool whose `effect` is "write" refuses to run unless the executor passes it the id of a confirmed action (`ctx.confirmed_action`); a chat turn can only propose it."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .. import db
from ..ai import injection, knowledge
from ..assistant import engine as assistant_engine
from ..assistant.retrieval import get_retriever
from ..documents import store
from ..quotes import service as quotes
from ..services import core, explanations, notify

ID = r"^[A-Za-z0-9_-]{1,64}$"
MAX_RESULT_BYTES = 64_000

MESSAGES = {
    "unknown_tool": "That is not something this assistant can do.",
    "forbidden": "You do not have permission for this action.",
    "consent_required": "Please read and accept the privacy notice to continue.",
    "invalid_arguments": "The request was not in the expected form.",
    "not_found": "That item was not found.",
    "confirmation_required": "This action changes something, so it needs your confirmation first.",
    "unavailable": "This is not available right now.",
    "timeout": "This took too long and was stopped.",
    "too_large": "The result was too large to use.",
    "error": "Something went wrong while doing this.",
}


class ToolError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code = code
        self.message = message or MESSAGES.get(code, MESSAGES["error"])


@dataclass
class ToolContext:
    user: dict                                   # the authenticated session: {"id", "role", ...}. The only source of "who".
    request_id: str = ""
    cancel: Any = None
    confirmed_action: str | None = None          # set only by the confirmation path


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoArgs(Args):
    pass


class DocArgs(Args):
    document_id: str = Field(pattern=ID)


class ExplainArgs(DocArgs):
    lang: Literal["en", "hi", "hinglish"] = "en"


class SearchArgs(Args):
    query: str = Field(min_length=3, max_length=300)
    k: int = Field(default=3, ge=1, le=5)


class NotificationArgs(Args):
    unread_only: bool = True


class MarkReadArgs(Args):
    notification_id: str | None = Field(default=None, pattern=ID)
    all: bool = False

    @model_validator(mode="after")
    def _one(self):
        if bool(self.notification_id) == bool(self.all):
            raise ValueError("name one notification or choose all")
        return self


class QuoteItem(Args):
    item_id: str = Field(pattern=ID)
    qty: int = Field(default=1, ge=1, le=100)


class QuoteArgs(Args):
    items: list[QuoteItem] = Field(min_length=1, max_length=10)
    notes: str = Field(default="", max_length=500)


class FlagArgs(DocArgs):
    field_id: str = Field(pattern=ID)
    reason: str = Field(default="", max_length=300)


class ReviewArgs(DocArgs):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    effect: str                                         # "read" | "write"
    roles: tuple[str, ...]
    args: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict]
    timeout: float = 10.0
    describe: Callable[[ToolContext, Any], str] | None = None      # write tools: the plain-words summary shown for confirmation (also checks the records exist and are the person's)


def _short(s: str | None, n: int = 80) -> str:
    s = (s or "").strip()
    return "(name hidden)" if injection.is_instruction_like(s) else (s[:n] + ("…" if len(s) > n else ""))


def own_document(ctx: ToolContext, doc_id: str) -> dict:
    d = db.q1("SELECT * FROM documents WHERE id=? AND owner_id=? AND deleted=0", (doc_id, ctx.user["id"]))
    if not d:
        raise ToolError("not_found")
    return d


# ---------------------------------------------------------------- read tools
def list_documents(ctx: ToolContext, a: NoArgs) -> dict:
    rows = db.q("SELECT id,filename,doc_type,doc_date,page_count,review_status,created_at FROM documents WHERE owner_id=? AND deleted=0 ORDER BY created_at DESC LIMIT 50", (ctx.user["id"],))
    return {"documents": [{**r, "filename": _short(r["filename"])} for r in rows]}


def get_document(ctx: ToolContext, a: DocArgs) -> dict:
    d = own_document(ctx, a.document_id)
    n = lambda t: db.q1(f"SELECT COUNT(*) c FROM {t} WHERE document_id=?", (d["id"],))["c"]      # noqa: E731  (fixed table names, never from input)
    return {"document": {"id": d["id"], "filename": _short(d["filename"]), "doc_type": d["doc_type"], "doc_date": d["doc_date"], "page_count": d["page_count"], "review_status": d["review_status"]},
            "counts": {"results": n("doc_fields"), "facts": n("doc_facts"), "medicines": n("doc_medications")}}


def explain_document(ctx: ToolContext, a: ExplainArgs) -> dict:
    own_document(ctx, a.document_id)
    try:
        out = explanations.build(ctx.user, a.document_id, a.lang)
    except explanations.NotFound:
        raise ToolError("not_found") from None
    cites = [i["citation"] for s in out["sections"] for i in s["items"] if i.get("citation")]
    return {"explanation": out, "citations": cites}


def list_medications(ctx: ToolContext, a: DocArgs) -> dict:
    d = own_document(ctx, a.document_id)
    meds, hidden = [], 0
    for m in store.load_medications(d["id"]):
        if m["review_status"] == "rejected":
            continue
        if injection.is_instruction_like(m["raw"]):
            hidden += 1
            continue
        meds.append({"id": m["id"], "page": m["page"], "raw": m["raw"][:300], "ambiguity": m["ambiguity"], "review_status": m["review_status"]})
    return {"document": {"id": d["id"], "filename": _short(d["filename"])}, "medicines": meds, "hidden_text": hidden,
            "citations": [{"kind": "document", "document_id": d["id"], "page": m["page"], "excerpt": m["raw"], "item": "medication", "item_id": m["id"]} for m in meds]}


def search_my_documents(ctx: ToolContext, a: SearchArgs) -> dict:
    ok, bad = injection.partition(assistant_engine.document_passages(ctx.user["id"]))
    ret = get_retriever()
    hits = [(ok[h.index], h.score) for h in ret.search(a.query, ok, k=a.k) if h.score >= ret.threshold] if ok else []
    seen, out = set(), []
    for p, s in hits:
        key = (p["document_id"], p["page"], " ".join(p["text"].split()))
        if key in seen:
            continue
        seen.add(key)
        out.append({"document_id": p["document_id"], "filename": _short(p["filename"]), "page": p["page"], "excerpt": p["text"][:300], "score": round(s, 3)})
    return {"hits": out, "quarantined_text": len(bad), "retrieval": {"mode": ret.mode, "threshold": ret.threshold},
            "citations": [{"kind": "document", "document_id": h["document_id"], "page": h["page"], "excerpt": h["excerpt"], "item": "text", "score": h["score"]} for h in out]}


def search_approved_knowledge(ctx: ToolContext, a: SearchArgs) -> dict:
    res = knowledge.search(a.query, k=a.k)
    d = res.to_dict(staff=False)
    return {**d, "citations": [{k: v for k, v in h.items() if k != "text"} for h in d["hits"]]}


def list_price_items(ctx: ToolContext, a: NoArgs) -> dict:
    rows = db.q("SELECT id,name,description,unit_price,currency,category FROM price_items WHERE active=1 AND approved_by!='' ORDER BY category,name LIMIT 100")
    return {"items": rows}


def list_quotes(ctx: ToolContext, a: NoArgs) -> dict:
    import json
    out = []
    for q in db.q("SELECT * FROM quotes WHERE patient_id=? ORDER BY created_at DESC LIMIT 20", (ctx.user["id"],)):
        eff = quotes.effective_status(q)
        row = {"id": q["id"], "status": q["status"], "effective_status": eff, "currency": q["currency"], "created_at": q["created_at"]}
        if eff == "approved":                                           # only a clinic-approved, unexpired quotation shows its lines and total
            row |= {"total": q["total"], "lines": [{"name": l["name"], "qty": l["qty"], "line_total": l["line_total"]} for l in json.loads(q["lines"])]}
        out.append(row)
    return {"quotes": out}


def list_notifications(ctx: ToolContext, a: NotificationArgs) -> dict:
    rows = db.q("SELECT id,kind,title,body,created_at,read_at FROM notifications WHERE user_id=?" + (" AND read_at IS NULL" if a.unread_only else "") + " ORDER BY created_at DESC LIMIT 20",
                (ctx.user["id"],))
    return {"notifications": rows}


# ---------------------------------------------------------------- write tools (run only after the person confirms)
def _confirmed(ctx: ToolContext) -> None:
    if not ctx.confirmed_action:
        raise ToolError("confirmation_required")


def request_quote(ctx: ToolContext, a: QuoteArgs) -> dict:
    _confirmed(ctx)
    try:
        return core.request_quote(ctx.user, [{"item_id": i.item_id, "qty": i.qty} for i in a.items], a.notes)
    except quotes.QuoteError as e:
        raise ToolError("invalid_arguments", str(e)) from None


def describe_quote(ctx: ToolContext, a: QuoteArgs) -> str:
    names = []
    for i in a.items:
        r = db.q1("SELECT name FROM price_items WHERE id=? AND active=1 AND approved_by!=''", (i.item_id,))
        if not r:
            raise ToolError("invalid_arguments", "One of the requested items is not on the clinic's approved price list.")
        names.append(f"{i.qty} x {r['name']}")
    return "Ask the clinic for a quotation for: " + ", ".join(names) + ". A clinic doctor reviews and approves it before you can see a price."


def request_clinician_review(ctx: ToolContext, a: ReviewArgs) -> dict:
    _confirmed(ctx)
    try:
        return core.request_review(ctx.user, a.document_id)
    except LookupError:
        raise ToolError("not_found") from None


def describe_review(ctx: ToolContext, a: ReviewArgs) -> str:
    d = own_document(ctx, a.document_id)
    return f'Ask the clinic team to look at your document "{_short(d["filename"])}".'


def flag_value(ctx: ToolContext, a: FlagArgs) -> dict:
    _confirmed(ctx)
    try:
        return core.flag_field(ctx.user, a.document_id, a.field_id, a.reason)
    except LookupError:
        raise ToolError("not_found") from None


def describe_flag(ctx: ToolContext, a: FlagArgs) -> str:
    d = own_document(ctx, a.document_id)
    f = db.q1("SELECT name FROM doc_fields WHERE id=? AND document_id=?", (a.field_id, d["id"]))
    if not f:
        raise ToolError("not_found")
    return f'Mark the "{_short(f["name"], 40)}" value in "{_short(d["filename"])}" as possibly wrong, so the clinic team checks it against your original.'


def mark_notifications_read(ctx: ToolContext, a: MarkReadArgs) -> dict:
    _confirmed(ctx)
    if a.all:
        return notify.mark_all_read(ctx.user["id"])
    if not notify.mark_read(ctx.user["id"], a.notification_id):
        raise ToolError("not_found")
    return {"unread": db.q1("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND read_at IS NULL", (ctx.user["id"],))["c"]}


def describe_mark_read(ctx: ToolContext, a: MarkReadArgs) -> str:
    if a.all:
        n = db.q1("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND read_at IS NULL", (ctx.user["id"],))["c"]
        return f"Mark all {n} unread notification(s) as read."
    if not db.q1("SELECT 1 x FROM notifications WHERE id=? AND user_id=?", (a.notification_id, ctx.user["id"])):
        raise ToolError("not_found")
    return "Mark this notification as read."


PATIENT = ("patient",)
TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("list_documents", "List your uploaded documents (name, type, date, review status).", "read", PATIENT, NoArgs, list_documents),
    Tool("get_document", "Show what was read from one of your documents (counts only).", "read", PATIENT, DocArgs, get_document),
    Tool("explain_document", "Explain what one of your documents says, with the page and text each statement comes from.", "read", PATIENT, ExplainArgs, explain_document, timeout=30.0),
    Tool("list_medications", "List the medicine entries printed on one of your documents, as written.", "read", PATIENT, DocArgs, list_medications),
    Tool("search_my_documents", "Find lines in your own documents that answer a question.", "read", PATIENT, SearchArgs, search_my_documents, timeout=20.0),
    Tool("search_approved_knowledge", "Find clinic-approved information that answers a question.", "read", PATIENT, SearchArgs, search_approved_knowledge, timeout=20.0),
    Tool("list_price_items", "Show the clinic's approved price list.", "read", PATIENT, NoArgs, list_price_items),
    Tool("list_quotes", "Show your quotations (price and lines only once the clinic has approved one).", "read", PATIENT, NoArgs, list_quotes),
    Tool("list_notifications", "Show your notifications.", "read", PATIENT, NotificationArgs, list_notifications),
    Tool("request_quote", "Ask the clinic for a quotation for items on the price list.", "write", PATIENT, QuoteArgs, request_quote, describe=describe_quote),
    Tool("request_clinician_review", "Ask the clinic team to look at one of your documents.", "write", PATIENT, ReviewArgs, request_clinician_review, describe=describe_review),
    Tool("flag_value", "Mark one extracted value as possibly wrong so the clinic checks it.", "write", PATIENT, FlagArgs, flag_value, describe=describe_flag),
    Tool("mark_notifications_read", "Mark one or all of your notifications as read.", "write", PATIENT, MarkReadArgs, mark_notifications_read, describe=describe_mark_read),
)}


def visible_to(user: dict) -> list[Tool]:
    return [t for t in TOOLS.values() if user.get("role") in t.roles]


def public_schema(t: Tool) -> dict:
    """What the person (and any planner) may know about a tool: name, purpose, effect and the shape of its arguments."""
    return {"name": t.name, "description": t.description, "effect": t.effect, "requires_confirmation": t.effect == "write", "arguments": t.args.model_json_schema().get("properties", {})}

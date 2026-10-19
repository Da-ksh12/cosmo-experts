"""The agent loop: one request in, one answer out, with every tool call permission-checked, bounded and recorded.

    safety classification (urgent / diagnosis / medicine advice end here, before any tool)  ->  intent  ->  fixed tool sequence for that intent  ->  composed answer
    ->  shared safety review  ->  trace

A tool that changes state is never run here. The agent PROPOSES it (propose()), the person sees what it would do in plain words, and only `confirm()` - a separate, authenticated request from
that same person - runs it, once. Nothing in a document, a tool result or an answer can call `confirm()`."""
from __future__ import annotations

import hashlib
import logging
import re
import time

from pydantic import BaseModel, Field, ValidationError

from .. import db
from ..ai import safety
from ..documents import languages
from . import compose, executor, planner, provider, store
from .tools import TOOLS, ToolContext, ToolError

log = logging.getLogger("cosmo.agent")
MAX_SECONDS = 60.0                                          # the whole turn; each tool also has its own, shorter limit
SLOW = executor.ToolResult("?", False, None, "timeout", "This took too long and was stopped.")


class AgentResponse(BaseModel):
    request_id: str
    trace_id: str
    status: str                                             # answered | abstained | clarification | needs_confirmation | refused | urgent | error
    answer: str
    intent: str
    provider: str
    reason: str = ""
    sources: list[dict] = Field(default_factory=list)       # citations for every statement that rests on a record
    steps: list[dict] = Field(default_factory=list)         # what was called and how each call ended (no arguments, no data)
    actions: list[dict] = Field(default_factory=list)       # proposed changes awaiting the person's confirmation
    labels: list[str] = Field(default_factory=list)
    disclaimers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    data: dict | None = None                                # the structured payload behind the answer, for the screen


class ActionNotFound(Exception):
    pass


class ActionConflict(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


def _failed(r: executor.ToolResult) -> compose.Answer:
    status = "clarification" if r.code == "invalid_arguments" else "error"
    return compose.Answer(r.message, status=status, reason=r.code)


class Session:
    """What the handlers use: the person (from the session), a way to call tools that records each call, and a way to propose changes."""

    def __init__(self, user: dict, request_id: str, trace_id: str, cancel=None):
        self.ctx = ToolContext(user=user, request_id=request_id, cancel=cancel)
        self.trace_id = trace_id
        self.steps: list[dict] = []
        self.deadline = time.monotonic() + MAX_SECONDS

    def call(self, name: str, args: dict | None = None) -> executor.ToolResult:
        if time.monotonic() > self.deadline or (self.ctx.cancel is not None and self.ctx.cancel.cancelled):
            r = executor.ToolResult(name, False, None, "timeout", SLOW.message, 0, "", TOOLS[name].effect if name in TOOLS else "read")      # the turn's time is spent: no further tool starts
            self.steps.append(r.step())
            return r
        r = executor.call(name, args or {}, self.ctx)
        self.steps.append(r.step())
        return r

    def propose(self, name: str, args: dict) -> compose.Answer:
        tool = TOOLS[name]
        if self.ctx.user.get("role") not in tool.roles:
            self.steps.append({"tool": name, "effect": "write", "status": "forbidden", "ms": 0, "args_sha256": "", "returned": {}})
            return compose.Answer(ToolError("forbidden").message, status="error", reason="forbidden")
        try:
            parsed = tool.args.model_validate(args)
            summary = tool.describe(self.ctx, parsed) if tool.describe else tool.description
        except ValidationError:
            self.steps.append({"tool": name, "effect": "write", "status": "invalid_arguments", "ms": 0, "args_sha256": "", "returned": {}})
            return compose.Answer(ToolError("invalid_arguments").message, status="clarification", reason="invalid_arguments")
        except ToolError as e:
            self.steps.append({"tool": name, "effect": "write", "status": e.code, "ms": 0, "args_sha256": "", "returned": {}})
            return compose.Answer(e.message, status="clarification" if e.code == "invalid_arguments" else "error", reason=e.code)
        canonical = parsed.model_dump(mode="json")
        sha = executor.args_hash(canonical)
        a = store.propose(self.ctx.user["id"], self.trace_id, name, canonical, sha, summary)
        self.steps.append({"tool": name, "effect": "write", "status": "proposed", "ms": 0, "args_sha256": sha[:16], "returned": {}})
        return compose.proposal({"id": a["id"], "tool": name, "summary": a["summary"], "expires_at": a["expires_at"], "state": a["state"]})


# ---------------------------------------------------------------- intent handlers: a fixed sequence of tool calls each
def _documents(s: Session):
    r = s.call("list_documents")
    return r, (r.data["documents"] if r.ok else [])


def h_help(s, msg):
    return compose.help_text()


def h_list_documents(s, msg):
    r = s.call("list_documents")
    return compose.documents(r.data) if r.ok else _failed(r)


def _pick(s, msg, what):
    r, docs = _documents(s)
    if not r.ok:
        return None, _failed(r)
    d, how = planner.pick_document(msg, docs)
    if d is None:
        return None, compose.which_document(docs, what)
    return d, None


def _lang(msg: str) -> str:
    return "hi" if languages.script_hint(msg) == "devanagari" else "en"


def h_explain(s, msg):
    d, stop = _pick(s, msg, "I can explain one document at a time.")
    if stop:
        return stop
    r = s.call("explain_document", {"document_id": d["id"], "lang": _lang(msg)})
    return compose.explanation(d, r.data) if r.ok else _failed(r)


def h_medications(s, msg):
    r, docs = _documents(s)
    if not r.ok:
        return _failed(r)
    d, how = planner.pick_document(msg, docs)
    if d is None and how == "ambiguous":
        rx = [x for x in docs if x["doc_type"] == "prescription"]
        d = rx[0] if rx else None                                  # several documents and none named: the newest prescription
        if d is None:
            return compose.which_document(docs, "Which one has the medicines?")
    if d is None:
        return compose.Answer(compose.NO_DOCS)
    m = s.call("list_medications", {"document_id": d["id"]})
    return compose.medications(d, m.data) if m.ok else _failed(m)


def h_quotes(s, msg):
    r = s.call("list_quotes")
    return compose.quotes(r.data) if r.ok else _failed(r)


def h_prices(s, msg):
    r = s.call("list_price_items")
    return compose.prices(r.data) if r.ok else _failed(r)


def h_notifications(s, msg):
    r = s.call("list_notifications", {"unread_only": not re.search(r"\ball\b", msg, re.I)})
    return compose.notifications(r.data) if r.ok else _failed(r)


def h_mark_read(s, msg):
    return s.propose("mark_notifications_read", {"all": True})


def h_review(s, msg):
    d, stop = _pick(s, msg, "I can ask the team to look at one document at a time.")
    return stop or s.propose("request_clinician_review", {"document_id": d["id"]})


def h_quote_request(s, msg):
    r = s.call("list_price_items")
    if not r.ok:
        return _failed(r)
    items = planner.match_items(msg, r.data["items"])
    if not items:
        a = compose.prices(r.data)
        a.text = "Which treatment would you like a quotation for? " + a.text.split("\n", 1)[-1]
        a.status, a.reason = "clarification", "items_not_specified"
        return a
    return s.propose("request_quote", {"items": items})


def h_flag(s, msg):
    d, stop = _pick(s, msg, "Which document has the value you want to flag?")
    if stop:
        return stop
    r = s.call("explain_document", {"document_id": d["id"]})
    if not r.ok:
        return _failed(r)
    fields = [(i["field_id"], i["name"]) for i in r.data["explanation"]["sections"][0]["items"] if i.get("field_id") and i.get("name")]
    low = msg.lower()
    hit = [f for f in sorted(fields, key=lambda f: -len(f[1])) if f[1].lower() in low]
    if len(hit) != 1:
        names = ", ".join(sorted({n for _, n in fields})[:12]) or "none were read"
        return compose.Answer(f'Which value do you mean? The results read from "{d["filename"]}" are: {names}.', status="clarification", reason="value_not_specified")
    return s.propose("flag_value", {"document_id": d["id"], "field_id": hit[0][0], "reason": "Marked by the patient as possibly wrong"})


def h_ask(s, msg):
    d = s.call("search_my_documents", {"query": msg})
    k = s.call("search_approved_knowledge", {"query": msg})
    if not d.ok and not k.ok:
        if "invalid_arguments" in (d.code, k.code):
            return compose.Answer("Please ask a slightly longer question.", status="clarification", reason="invalid_arguments")
        return _failed(d)
    kb = k.data if k.ok and k.data.get("status") == "answered" else None
    return compose.answer_from_records(msg, d.data if d.ok else None, kb, d.data.get("quarantined_text", 0) if d.ok else 0)


HANDLERS = {"help": h_help, "list_documents": h_list_documents, "explain_document": h_explain, "list_medications": h_medications, "list_quotes": h_quotes, "list_prices": h_prices,
            "request_quote": h_quote_request, "list_notifications": h_notifications, "mark_notifications_read": h_mark_read, "request_review": h_review, "flag_value": h_flag, "ask": h_ask}


# ---------------------------------------------------------------- the request
def run(user: dict, message: str, *, request_id: str = "", cancel=None) -> AgentResponse:
    t0 = time.monotonic()
    msg = " ".join((message or "").split())[:1000]
    rid = request_id or db.uid()
    tid = db.uid()
    s = Session(user, rid, tid, cancel)
    notes: list[str] = []
    prov_name = "none"
    cls = safety.classify_request(msg)
    if cls.kind == "urgent":
        intent, ans = "urgent", compose.Answer(safety.urgent_message(), status="urgent", reason="urgent")
    elif cls.refuse:
        intent, ans = cls.kind, compose.Answer(safety.REFUSE_MSG, status="refused", reason="clinical_decision")
    else:
        prov, note = provider.active()
        prov_name = prov.name
        if note:
            notes.append(note)
        try:
            it = prov.classify(msg)
        except Exception as e:                                       # a classifier that fails or is disabled hands over to the rules; it never decides alone
            log.warning("classifier %s failed (request %s): %s", prov.name, rid, type(e).__name__)
            it = planner.classify(msg)
            notes.append("classifier_fallback")
        intent = it.kind
        try:
            ans = HANDLERS[intent](s, msg)
        except Exception as e:                                       # a bug in a handler is an error answer, never a half-run action or an exception to the client
            log.error("agent handler %s failed (request %s): %s", intent, rid, type(e).__name__)
            ans = compose.Answer(ToolError("error").message, status="error", reason="error")
    rev = safety.review_text(ans.text, kind="education", evidence=ans.evidence)
    if not rev.allowed:
        ans = compose.Answer(rev.replacement or safety.UNSUPPORTED_MSG, status="abstained", reason="safety_policy")
    ms = round((time.monotonic() - t0) * 1000)
    store.save_trace(user["id"], rid, intent, prov_name, ans.status, s.steps, {"allowed": rev.allowed, "codes": rev.codes(), "labels": rev.labels}, ms,
                     hashlib.sha256(msg.encode()).hexdigest(), len(msg), trace_id=tid)
    db.audit(user["id"], "agent.chat", tid, {"intent": intent, "status": ans.status, "tools": [x["tool"] for x in s.steps]})
    return AgentResponse(request_id=rid, trace_id=tid, status=ans.status, answer=ans.text, intent=intent, provider=prov_name, reason=ans.reason, sources=ans.citations, steps=s.steps,
                         actions=ans.actions, labels=rev.labels + ["review_required"], disclaimers=rev.disclaimers, notes=notes, data=ans.data)


# ---------------------------------------------------------------- confirming or cancelling a proposed change
def confirm(user: dict, action_id: str, *, request_id: str = "") -> dict:
    act = store.get_action(user["id"], action_id)
    if act is None:
        raise ActionNotFound(action_id)
    if act["state"] == "executed":
        return {"id": action_id, "state": "executed", "result": act["result"], "replayed": True}
    if act["state"] == "failed":
        return {"id": action_id, "state": "failed", "result": act["result"], "replayed": True}
    if not store.claim(user["id"], action_id):
        cur = store.get_action(user["id"], action_id) or act
        if cur["state"] == "executed":
            return {"id": action_id, "state": "executed", "result": cur["result"], "replayed": True}
        msg = {"executing": "This is already being carried out.", "cancelled": "This was cancelled.", "expired": "This request expired. Please ask again.",
               "proposed": "This request expired. Please ask again."}.get(cur["state"], "This can no longer be confirmed.")
        raise ActionConflict(cur["state"] if cur["state"] != "proposed" else "expired", msg)
    ctx = ToolContext(user=user, request_id=request_id, confirmed_action=action_id)
    r = executor.call(act["tool"], act["args"], ctx, allow_write=True)
    if r.ok:
        store.finish(action_id, "executed", r.data)
        db.audit(user["id"], "agent.action.executed", action_id, {"tool": act["tool"], "args_sha256": act["args_sha256"][:16]})
        return {"id": action_id, "state": "executed", "result": r.data, "replayed": False}
    store.finish(action_id, "failed", {"code": r.code, "message": r.message})
    db.audit(user["id"], "agent.action.failed", action_id, {"tool": act["tool"], "code": r.code})
    return {"id": action_id, "state": "failed", "result": {"code": r.code, "message": r.message}, "replayed": False}


def cancel(user: dict, action_id: str) -> dict:
    act = store.get_action(user["id"], action_id)
    if act is None:
        raise ActionNotFound(action_id)
    if not store.cancel(user["id"], action_id):
        raise ActionConflict(act["state"], "This can no longer be cancelled.")
    db.audit(user["id"], "agent.action.cancelled", action_id, {"tool": act["tool"]})
    return {"id": action_id, "state": "cancelled"}

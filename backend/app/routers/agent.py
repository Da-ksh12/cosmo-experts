"""Transport for the secure agent (app/agent). The rules live in the agent package; this module authenticates, bounds the request and maps outcomes to HTTP.

* POST /chat runs one turn for the SIGNED-IN patient. It can propose a change; it can never carry one out.
* POST /actions/{id}/confirm is the only way a proposed change runs, and only the person it belongs to can call it. Nothing in a document, an answer or a tool result can make that call.
* Traces hold no message or record text; the person sees their own, administrators see all."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import consent, db
from ..agent import agent as agent_loop, provider, store, tools
from ..ai.resources import CancelToken
from ..security import current_user, require

router = APIRouter(prefix="/api/v1/agent")
ID = r"^[A-Za-z0-9_-]{1,64}$"


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    conversation_id: str | None = Field(default=None, pattern=ID)


@router.get("/tools")
def list_tools(user=Depends(current_user)):
    """The closed list of things the assistant can do for this person, and which of them change something (those need the person's confirmation)."""
    return {"tools": [tools.public_schema(t) for t in tools.visible_to(user)]}


@router.post("/chat", response_model=agent_loop.AgentResponse)
def chat(body: ChatIn, request: Request, user=Depends(consent.patient_consented)):
    cid = body.conversation_id
    if cid:
        c = db.q1("SELECT owner_id FROM conversations WHERE id=?", (cid,))
        if not c or c["owner_id"] != user["id"]:
            raise HTTPException(404, "Conversation not found.")
        db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "patient", body.message, "[]", db.now()))
    out = agent_loop.run(user, body.message, request_id=getattr(request.state, "request_id", ""), cancel=CancelToken(deadline=time.monotonic() + agent_loop.MAX_SECONDS + 5))
    if cid:
        db.x("INSERT INTO messages VALUES(?,?,?,?,?,?)", (db.uid(), cid, "assistant", out.answer, json.dumps(out.sources), db.now() + 0.001))
    return out


@router.get("/actions")
def my_pending_actions(user=Depends(require("patient"))):
    return {"actions": [{k: a[k] for k in ("id", "tool", "summary", "state", "created_at", "expires_at")} for a in store.list_actions(user["id"])]}


def _action_errors(fn):
    try:
        return fn()
    except agent_loop.ActionNotFound:
        raise HTTPException(404, "Action not found.") from None
    except agent_loop.ActionConflict as e:
        raise HTTPException(409, {"code": e.code, "message": e.message}) from None


@router.post("/actions/{action_id}/confirm")
def confirm_action(action_id: str, request: Request, user=Depends(consent.patient_consented)):
    """Carry out ONE proposed change, once. Repeating the call returns the first result."""
    if not _valid(action_id):
        raise HTTPException(404, "Action not found.")
    return _action_errors(lambda: agent_loop.confirm(user, action_id, request_id=getattr(request.state, "request_id", "")))


@router.post("/actions/{action_id}/cancel")
def cancel_action(action_id: str, user=Depends(require("patient"))):
    if not _valid(action_id):
        raise HTTPException(404, "Action not found.")
    return _action_errors(lambda: agent_loop.cancel(user, action_id))


def _valid(s: str) -> bool:
    import re
    return bool(re.fullmatch(ID, s or ""))


@router.get("/traces")
def my_traces(limit: int = 30, user=Depends(require("patient"))):
    return {"traces": store.list_traces(user["id"], limit)}


@router.get("/traces/{trace_id}")
def trace(trace_id: str, user=Depends(current_user)):
    t = store.get_trace(trace_id, user) if _valid(trace_id) else None
    if t is None:
        raise HTTPException(404, "Trace not found.")
    return t


@router.get("/providers")
def providers(user=Depends(require("admin"))):
    """Which providers may classify requests or write answers, and exactly why each one that is off is off."""
    from ..ai import gates
    ok, why = gates.llm_answers_status()
    return {"classifiers": [p.__dict__ for p in provider.statuses()], "model_drafted_answers": {"enabled": ok, "reasons": why}}

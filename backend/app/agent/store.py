"""Persistence for the agent: auditable traces and pending (state-changing) actions.

A TRACE records what the agent did for one request: the intent, which tools it called, how each ended, how long it took and how much came back. It does NOT hold the person's message,
any document text, any tool argument or any answer text: only a hash and the length of the message, and a hash of each call's arguments. It exists so that what the assistant did can be
audited and counted without the audit itself becoming another copy of health information.

An ACTION is a state-changing tool call the agent PROPOSED. It is stored with the validated arguments (they are needed to carry it out, and the person already supplied them), a plain-words
summary built by the server, and a state. The state moves forward only: proposed -> executing -> executed | failed, or proposed -> cancelled | expired. The move out of `proposed` is one
atomic UPDATE, so two confirmations of the same action cannot both run it."""
from __future__ import annotations

import json
import os

from .. import db

ACTION_TTL_S = 900


def ttl() -> int:
    try:
        return max(60, min(86400, int(os.environ.get("COSMO_AGENT_ACTION_TTL_S", ACTION_TTL_S))))
    except ValueError:
        return ACTION_TTL_S


def save_trace(user_id: str, request_id: str, intent: str, provider: str, outcome: str, steps: list[dict], safety: dict, duration_ms: int, message_sha256: str, message_chars: int,
               trace_id: str | None = None) -> str:
    tid = trace_id or db.uid()
    db.x("INSERT INTO agent_traces(id,user_id,request_id,created_at,intent,provider,outcome,steps,safety,duration_ms,message_sha256,message_chars) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
         (tid, user_id, request_id, db.now(), intent, provider, outcome, json.dumps(steps), json.dumps(safety), duration_ms, message_sha256, message_chars))
    return tid


def _trace(r: dict) -> dict:
    return {**r, "steps": json.loads(r["steps"] or "[]"), "safety": json.loads(r["safety"] or "{}")}


def list_traces(user_id: str, limit: int = 30) -> list[dict]:
    return [_trace(r) for r in db.q("SELECT * FROM agent_traces WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, max(1, min(limit, 100))))]


def get_trace(trace_id: str, user: dict) -> dict | None:
    """The owner's trace, or any trace for an administrator (traces hold no message or record text). Anyone else gets None, exactly as for a missing id."""
    r = db.q1("SELECT * FROM agent_traces WHERE id=?", (trace_id,))
    if not r or (r["user_id"] != user["id"] and user.get("role") != "admin"):
        return None
    return _trace(r)


# ---------------------------------------------------------------- actions
def _action(r: dict | None) -> dict | None:
    if not r:
        return None
    return {**r, "args": json.loads(r["args"]), "result": json.loads(r["result"]) if r.get("result") else None}


def propose(user_id: str, trace_id: str, tool: str, args: dict, args_sha: str, summary: str) -> dict:
    """Store a proposed action, or return the identical one that is still waiting (asking twice does not create two things to confirm)."""
    now = db.now()
    same = db.q1("SELECT * FROM agent_actions WHERE user_id=? AND tool=? AND args_sha256=? AND state='proposed' AND expires_at>?", (user_id, tool, args_sha, now))
    if same:
        return _action(same)
    aid = db.uid()
    db.x("INSERT INTO agent_actions(id,user_id,trace_id,tool,args,args_sha256,summary,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
         (aid, user_id, trace_id, tool, json.dumps(args), args_sha, summary[:500], "proposed", now, db.Epoch(now + ttl())))
    return _action(db.q1("SELECT * FROM agent_actions WHERE id=?", (aid,)))


def get_action(user_id: str, action_id: str) -> dict | None:
    return _action(db.q1("SELECT * FROM agent_actions WHERE id=? AND user_id=?", (action_id, user_id)))


def list_actions(user_id: str, state: str = "proposed") -> list[dict]:
    now = db.now()
    return [_action(r) for r in db.q("SELECT * FROM agent_actions WHERE user_id=? AND state=? AND (state!='proposed' OR expires_at>?) ORDER BY created_at DESC LIMIT 20", (user_id, state, now))]


def claim(user_id: str, action_id: str) -> bool:
    """proposed -> executing, once, and only before it expires. False = it was already claimed, finished, cancelled or has expired."""
    now = db.now()
    return db.x("UPDATE agent_actions SET state='executing', decided_at=? WHERE id=? AND user_id=? AND state='proposed' AND expires_at>?", (now, action_id, user_id, now)) == 1


def finish(action_id: str, state: str, result: dict) -> None:
    db.x("UPDATE agent_actions SET state=?, result=? WHERE id=? AND state='executing'", (state, json.dumps(result), action_id))


def cancel(user_id: str, action_id: str) -> bool:
    return db.x("UPDATE agent_actions SET state='cancelled', decided_at=? WHERE id=? AND user_id=? AND state='proposed'", (db.now(), action_id, user_id)) == 1


def expire_old() -> int:
    return db.x("UPDATE agent_actions SET state='expired' WHERE state='proposed' AND expires_at<=?", (db.now(),))

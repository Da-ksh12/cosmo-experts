"""Model lifecycle and release control: which model may answer, how it got there, how to take it away again, and a record of every step.

States (stored in the model's record; every change is an append-only event in `model_events` and an audit entry):

    imported ─┐
    experimental ─► evaluation_pending ─► review_pending ─► approved ─► active ─► rolled_back
                         └► evaluation_failed ◄┘                │         └► approved (superseded or deactivated; kept as a known-good fallback)
                                                    any state ► deprecated (final)

Rules the engine and the people operating it rely on:
* A model reaches patients ONLY as `active` AND `approved_for_defined_use` (a complete approval record: the five release requirements, a named clinical sign-off, and the evaluation gates passed).
  Nothing is ever substituted: if the active model is missing, damaged or deprecated the capability is unavailable.
* Imported or experimental models cannot be activated. Moving forward needs the evaluation gates (training/trainer.GATES, never edited to pass); activation needs the model file to match its recorded
  checksum, a recorded intended use, limitations, dataset reference and evaluation, and no open clinical issue.
* Activating a model keeps the previous one as `approved` (the known-good fallback). A rollback goes to the most recent known-good model whose file still verifies, or to nothing.
* A ROLLBACK IS TECHNICAL. It changes which model answers from now on. It does not change, correct or withdraw any result already issued - those still name the model that produced them, and that
  model's record and file are kept. If a model's output may have been clinically wrong, that is a CLINICAL ISSUE: reported separately, it blocks re-activation until closed, and `exposure` lists when
  the model was serving so that the affected period can be reviewed by a clinician.
* Automatic rollback uses only signals that are reliable without ground truth - the model's file failing its checksum, or a failure rate (errors and timeouts, never abstentions or rejected input)
  whose confidence-interval LOWER bound is high over enough runs - and is OFF unless the operator turns it on (COSMO_MODEL_AUTO_ROLLBACK=1).

The read functions used by the engine (`active_approved`, `research_models`, `derive_state`) keep their signatures."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from .. import db
from .schemas import ModelRef

STATES = ("imported", "experimental", "evaluation_pending", "evaluation_failed", "review_pending", "approved", "active", "deprecated", "rolled_back")
TRANSITIONS = {
    "imported": {"evaluation_pending", "deprecated"},
    "experimental": {"evaluation_pending", "deprecated"},
    "evaluation_pending": {"evaluation_failed", "review_pending", "experimental"},
    "evaluation_failed": {"evaluation_pending", "deprecated"},
    "review_pending": {"approved", "evaluation_failed", "experimental", "deprecated"},
    "approved": {"active", "deprecated", "evaluation_pending"},
    "active": {"approved", "rolled_back", "deprecated"},
    "rolled_back": {"evaluation_pending", "deprecated"},
    "deprecated": set(),
}
APPROVED_STATUS = "approved_for_defined_use"
TECHNICAL_ROLLBACK_NOTE = ("A technical rollback changes which model answers from now on. It does not change or correct any result already issued. If results from this model may have been "
                           "clinically wrong, report a clinical issue so that the period it was serving can be reviewed.")


class LifecycleError(Exception):
    def __init__(self, message: str, code: str = "refused"):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- read side used by the engine
def _ref(row: dict, card: dict, lifecycle: str) -> ModelRef:
    return ModelRef(model_id=row["id"], task=row["task"], lifecycle=lifecycle, architecture=str(card.get("architecture", ""))[:80],
                    artifact_sha256_prefix=str(card.get("artifact_sha256", ""))[:16])


def _setting(key: str) -> str | None:
    r = db.q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else None


def derive_state(status: str, is_active: bool, gates_failed: bool) -> str:
    """Lifecycle state of a model row that predates the explicit state."""
    if status == APPROVED_STATUS:
        return "active" if is_active else "approved"
    return "evaluation_failed" if gates_failed else "experimental"


def state_of(row: dict, card: dict | None = None, active_id: str | None = None) -> str:
    card = card if card is not None else json.loads(row["data"])
    explicit = (card.get("lifecycle") or {}).get("state")
    if explicit in STATES:
        return explicit
    return derive_state(row["status"], row["id"] == (active_id if active_id is not None else _setting(f"active_model:{row['task']}")), False)


_verified: dict[tuple, bool] = {}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_artifact(card: dict, *, required: bool = False) -> tuple[bool, str]:
    """Does the model file exist and match the checksum recorded for it? A record that names no file is unverifiable: refused when `required` (activation), tolerated otherwise (older
    records and test fixtures; serving re-checks the checksum every time it loads the file)."""
    p, want = card.get("artifact_path"), card.get("artifact_sha256")
    if not p:
        return (False, "no model file is recorded") if required else (True, "no model file is recorded")
    path = Path(p)
    try:
        st = path.stat()
    except OSError:
        return False, "the model file is missing"
    key = (str(path), st.st_mtime_ns, st.st_size, want)
    if key not in _verified:
        _verified[key] = bool(want) and _sha256_file(path) == want
    return (True, "the model file matches its recorded checksum") if _verified[key] else (False, "the model file does not match its recorded checksum")


def active_problem(task: str) -> str | None:
    """Why the task's active model cannot serve (None = nothing is wrong, or nothing is active)."""
    with db.context.as_system():
        mid = _setting(f"active_model:{task}")
        if not mid:
            return None
        row = db.q1("SELECT id,task,data,status FROM model_versions WHERE id=?", (mid,))
        if not row:
            return "the active model is not in the registry"
        if row["status"] != APPROVED_STATUS:
            return "the active model is not approved"
        card = json.loads(row["data"])
        st = state_of(row, card, mid)
        if st in ("deprecated", "rolled_back"):
            return f"the active model has been {st.replace('_', ' ')}"
        ok, why = verify_artifact(card)
        return None if ok else f"the active model cannot be used: {why}"


def active_approved(task: str) -> ModelRef | None:
    with db.context.as_system():
        mid = _setting(f"active_model:{task}")
        if not mid:
            return None
        row = db.q1("SELECT id,task,data,status FROM model_versions WHERE id=?", (mid,))
        if not row or row["status"] != APPROVED_STATUS or active_problem(task):
            return None                               # never another model in its place
        return _ref(row, json.loads(row["data"]), "active")


def research_models(task: str) -> list[ModelRef]:
    from ..training import trainer
    out = []
    with db.context.as_system():
        active = _setting(f"active_model:{task}")
        for row in db.q("SELECT id,task,data,status FROM model_versions WHERE task=? ORDER BY created_at DESC", (task,)):
            if row["status"] != "research_only":
                continue
            card = json.loads(row["data"])
            failed = any(not g["passed"] for g in trainer.evaluate_gates(card))
            out.append(_ref(row, card, state_of(row, card, active) if (card.get("lifecycle") or {}).get("state") else derive_state(row["status"], row["id"] == active, failed)))
    return out


# ---------------------------------------------------------------- the event record
_clock_lock = threading.Lock()
_last_event_at = 0.0


def _event_time() -> "db.Epoch":
    """Strictly increasing within the process. Two events of one operation (for example `evaluation_started` and `review_requested`) can fall into the same tick of the system clock, and
    the history is read back ordered by time: without this they could come back in the wrong order. (Found when the PostgreSQL run of the lifecycle API test once returned them swapped.)"""
    global _last_event_at
    with _clock_lock:
        t = max(float(db.now()), _last_event_at + 2e-6)
        _last_event_at = t
        return db.Epoch(t)


def _event(model_id: str, task: str, actor: str, event: str, from_state: str | None, to_state: str | None, detail: dict | None = None) -> None:
    with db.context.as_system():
        db.x("INSERT INTO model_events(id,model_id,task,at,actor,event,from_state,to_state,detail) VALUES(?,?,?,?,?,?,?,?,?)",
             (db.uid(), model_id, task, _event_time(), actor, event, from_state, to_state, json.dumps(detail or {}, default=str)))
        db.audit(actor, f"model.{event}", model_id, {"task": task, "from": from_state, "to": to_state, **(detail or {})})


def events(model_id: str) -> list[dict]:
    with db.context.as_system():
        rows = db.q("SELECT * FROM model_events WHERE model_id=? ORDER BY at, id", (model_id,))
    return [{**r, "detail": json.loads(r["detail"] or "{}")} for r in rows]


def _load(model_id: str) -> tuple[dict, dict]:
    row = db.q1("SELECT * FROM model_versions WHERE id=?", (model_id,))
    if not row:
        raise KeyError(model_id)
    return row, json.loads(row["data"])


def _save(row: dict, card: dict, status: str | None = None) -> None:
    db.x("UPDATE model_versions SET data=?, status=? WHERE id=?", (json.dumps(card, default=str), status or row["status"], row["id"]))


def _move(row: dict, card: dict, to: str, actor: str, event: str, detail: dict | None = None) -> str:
    """The one place a state changes: checks the transition, mirrors the legacy `status` column (approved/active models are approved_for_defined_use, everything else research_only),
    and records the event."""
    frm = state_of(row, card)
    if to != frm and to not in TRANSITIONS.get(frm, set()):
        raise LifecycleError(f"A model that is {frm.replace('_', ' ')} cannot become {to.replace('_', ' ')}.", "invalid_transition")
    card["lifecycle"] = {"state": to, "since": db.now(), "by": actor}
    _save(row, card, APPROVED_STATUS if to in ("approved", "active") else "research_only")
    row["status"] = APPROVED_STATUS if to in ("approved", "active") else "research_only"
    _event(row["id"], row["task"], actor, event, frm, to, detail)
    return frm


# ---------------------------------------------------------------- registration
def registered(model_id: str, task: str, actor: str, card: dict) -> None:
    """A model produced here enters the registry as `experimental`: research only, never served to patients, and the first line of its history."""
    _event(model_id, task, actor, "registered", None, "experimental", {"architecture": str(card.get("architecture", ""))[:60], "artifact_sha256_prefix": str(card.get("artifact_sha256", ""))[:16],
                                                                       "dataset_version": card.get("dataset_version_id")})


def register_import(task: str, artifact_path: str, actor: str, *, architecture: str, source: str, licence: str = "", notes: str = "") -> dict:
    """Record a model file brought in from elsewhere. It starts as `imported`: not evaluated, not approvable, never served to patients. The file must sit inside the model directory."""
    from ..config import MODEL_DIR
    path = Path(artifact_path).resolve()
    if MODEL_DIR.resolve() not in path.parents or not path.is_file():
        raise LifecycleError("The model file must be a file inside the application's model directory.", "invalid_artifact")
    mid = db.uid()
    card = {"model_id": mid, "task": task, "architecture": architecture[:80], "artifact_path": str(path), "artifact_sha256": _sha256_file(path), "source": source[:300], "dataset_license": {"note": licence[:300]},
            "test_metrics": {}, "baselines": {}, "intended_use": "Research only. Not evaluated. Not offered to patients.", "approval": None, "notes": notes[:500],
            "limitations": ["Imported model: no evaluation has been run on it here."], "resources": {"artifact_bytes": path.stat().st_size, "device": "cpu"},
            "lifecycle": {"state": "imported", "since": db.now(), "by": actor}}
    with db.context.as_system():
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (mid, task, json.dumps(card), "research_only", db.now()))
    _event(mid, task, actor, "imported", None, "imported", {"source": source[:120], "artifact_sha256_prefix": card["artifact_sha256"][:16]})
    return card


# ---------------------------------------------------------------- evaluation and review
def evaluate(model_id: str, actor: str) -> dict:
    """Run the evaluation gates on the model's recorded evidence. Pass -> `review_pending`; fail -> `evaluation_failed`, with each failed gate listed."""
    from ..training import trainer
    with db.context.as_system():
        row, card = _load(model_id)
        st = state_of(row, card)
        if st in ("deprecated",):
            raise LifecycleError("A deprecated model cannot be evaluated.", "invalid_transition")
        if st in ("active", "approved", "review_pending"):
            return {"model_id": model_id, "state": st, "gates": trainer.evaluate_gates(card), "note": "Already past evaluation."}
        if st != "evaluation_pending":
            _move(row, card, "evaluation_pending", actor, "evaluation_started")
        gates = trainer.evaluate_gates(card)
        failed = [g["gate"] for g in gates if not g["passed"]]
        card["gates_last_evaluated"] = {"at": db.now(), "failed": failed}
        if failed:
            _move(row, card, "evaluation_failed", actor, "evaluation_failed", {"failed_gates": failed})
        else:
            _move(row, card, "review_pending", actor, "review_requested", {"gates": len(gates)})
        return {"model_id": model_id, "state": state_of(row, card), "gates": gates, "failed_gates": failed}


def approve(model_id: str, actor: str, intended_use: str, requirements: dict, regulatory_note: str = "", signoff_by: str | None = None) -> dict:
    """Record an approval for a model in `review_pending`. It becomes `approved` only if the evaluation gates pass, the intended use is stated, all five release requirements are met and - when
    clinical sign-off is claimed - a verified clinician is named. Otherwise the model stays where it is and the record says exactly what is missing."""
    from .. import status as st
    from ..training import trainer
    with db.context.as_system():
        row, card = _load(model_id)
        if state_of(row, card) in ("experimental", "evaluation_failed", "imported", "rolled_back"):
            evaluate(model_id, actor)
            row, card = _load(model_id)
        gates = trainer.evaluate_gates(card)
        failed = [g["gate"] for g in gates if not g["passed"]]
        reasons = []
        if signoff_by is not None or requirements.get("clinical_signoff_recorded") is True:
            doc = db.q1("SELECT id FROM users WHERE id=? AND role='doctor' AND verified=1", (signoff_by or "",))
            if not doc:
                reasons.append("clinical sign-off must name a verified clinician")
        approval = st.Approval(actor, intended_use, requirements)
        eff = st.effective_status(APPROVED_STATUS, approval) if not failed else "research_only"
        if eff != APPROVED_STATUS:
            reasons.append("the release requirements are not all met" if not failed else "the evaluation gates failed")
        card["approval"] = {"approved_by": actor, "intended_use": intended_use, "requirements_met": requirements, "regulatory_note": regulatory_note, "clinical_signoff_by": signoff_by,
                            "effective_status": APPROVED_STATUS if (eff == APPROVED_STATUS and not reasons) else "research_only", "failed_evaluation_gates": failed,
                            "missing_requirements": [r for r in st.RELEASE_REQUIREMENTS if requirements.get(r) is not True], "refused_because": reasons}
        if card["approval"]["effective_status"] != APPROVED_STATUS:
            _save(row, card)
            _event(model_id, row["task"], actor, "approval_refused", state_of(row, card), state_of(row, card), {"reasons": reasons, "failed_gates": failed})
            db.audit(actor, "model.approval_request", model_id, card["approval"])
            return card["approval"]
        card["intended_use"] = intended_use
        _move(row, card, "approved", actor, "approved", {"intended_use": intended_use[:200], "clinical_signoff_by": signoff_by})
        db.audit(actor, "model.approval_request", model_id, card["approval"])
        return card["approval"]


# ---------------------------------------------------------------- activation, deactivation, rollback
def _history(task: str) -> list[str]:
    h = _setting(f"model_history:{task}")
    try:
        return list(json.loads(h)) if h else []
    except ValueError:
        return []


def activate(model_id: str, actor: str) -> dict:
    """Make an approved model the one that answers for its task. The previous one stays `approved` as the known-good fallback."""
    with db.context.as_system():
        row, card = _load(model_id)
        task = row["task"]
        st = state_of(row, card)
        if st != "approved" or row["status"] != APPROVED_STATUS:
            _event(model_id, task, actor, "activation_refused", st, st, {"why": "not approved"})
            raise PermissionError("Only a model with a complete approval record can be deployed.")
        problems = []
        ok, why = verify_artifact(card, required=True)
        if not ok:
            problems.append(why)
        for key, label in (("intended_use", "an intended use"), ("limitations", "its limitations"), ("approval", "an approval record")):
            if not card.get(key):
                problems.append(f"no {label} is recorded")
        if not (card.get("dataset_version_id") or card.get("dataset_id") or card.get("source")):
            problems.append("no dataset reference or source is recorded")
        if not (card.get("test_metrics") or card.get("evaluation_report")):
            problems.append("no evaluation is recorded")
        if card.get("clinical_issue_open"):
            problems.append("a clinical issue is open for this model")
        if problems:
            _event(model_id, task, actor, "activation_refused", st, st, {"problems": problems})
            raise PermissionError("This model cannot be activated: " + "; ".join(problems) + ".")
        prev = _setting(f"active_model:{task}")
        hist = _history(task)
        if prev and prev != model_id:
            prow, pcard = _load(prev)
            if state_of(prow, pcard, prev) == "active":
                _move(prow, pcard, "approved", actor, "deactivated", {"reason": "superseded", "by": model_id})
            elif prow["status"] == APPROVED_STATUS:
                pass
            hist.append(prev)
        elif prev == model_id:
            return {"task": task, "active": model_id, "previous": None, "note": "Already active."}
        db.set_setting(f"active_model:{task}", model_id)
        db.set_setting(f"model_history:{task}", json.dumps(hist))
        card["resources"] = {**(card.get("resources") or {}), "artifact_bytes": Path(card["artifact_path"]).stat().st_size if card.get("artifact_path") else None}
        _move(row, card, "active", actor, "activated", {"previous": prev, "artifact_sha256_prefix": str(card.get("artifact_sha256", ""))[:16]})
        db.audit(actor, "model.deploy", model_id, {"task": task, "previous": prev})
        return {"task": task, "active": model_id, "previous": prev}


def deactivate(task: str, actor: str, reason: str = "") -> dict:
    """Take the active model out of service WITHOUT replacing it: the capability becomes unavailable. (Nothing else answers in its place.)"""
    with db.context.as_system():
        mid = _setting(f"active_model:{task}")
        if not mid:
            raise LifecycleError("No model is active for this task.", "none_active")
        row, card = _load(mid)
        _move(row, card, "approved", actor, "deactivated", {"reason": reason[:200] or "operator"})
        db.x("DELETE FROM settings WHERE key=?", (f"active_model:{task}",))
        return {"task": task, "active": None, "deactivated": mid}


def rollback(task: str, actor: str, kind: str = "technical", reason: str = "") -> dict:
    """Return to the most recent known-good model whose file still verifies, or to none. The model rolled back from is kept (state `rolled_back`) with its record and file."""
    with db.context.as_system():
        cur = _setting(f"active_model:{task}")
        hist = _history(task)
        if not cur:
            if not hist:
                db.audit(actor, "model.rollback", task, {"to": None})
                return {"task": task, "active": None, "kind": kind, "note": "No model was active."}
            cur = None
        target, skipped = None, []
        while hist:
            cand = hist.pop()
            try:
                crow, ccard = _load(cand)
            except KeyError:
                skipped.append({"model": cand, "why": "not in the registry"})
                continue
            ok, why = verify_artifact(ccard)
            if crow["status"] == APPROVED_STATUS and state_of(crow, ccard, cand) in ("approved", "active") and ok:
                target = (cand, crow, ccard)
                break
            skipped.append({"model": cand, "why": why if not ok else "no longer approved"})
        if cur:
            row, card = _load(cur)
            _move(row, card, "rolled_back", actor, "rolled_back", {"kind": kind, "reason": reason[:200], "to": target[0] if target else None, "skipped": skipped})
        if target:
            tid, trow, tcard = target
            _move(trow, tcard, "active", actor, "activated", {"previous": cur, "via": "rollback"})
            db.set_setting(f"active_model:{task}", tid)
        else:
            db.x("DELETE FROM settings WHERE key=?", (f"active_model:{task}",))
        db.set_setting(f"model_history:{task}", json.dumps(hist))
        db.audit(actor, "model.rollback", task, {"to": target[0] if target else None, "kind": kind, "from": cur})
        return {"task": task, "active": target[0] if target else None, "rolled_back": cur, "kind": kind, "skipped": skipped, "note": TECHNICAL_ROLLBACK_NOTE}


def deprecate(model_id: str, actor: str, reason: str = "") -> dict:
    with db.context.as_system():
        row, card = _load(model_id)
        if state_of(row, card) == "active":
            raise LifecycleError("An active model must be rolled back or deactivated first.", "still_active")
        _move(row, card, "deprecated", actor, "deprecated", {"reason": reason[:200]})
        return {"model_id": model_id, "state": "deprecated"}


# ---------------------------------------------------------------- clinical issues are not technical rollbacks
def exposure(model_id: str) -> list[dict]:
    """When the model was serving: [{from, to, ended_by}] from its activation events. The windows a clinician would review if its output may have been wrong."""
    wins, start = [], None
    for e in events(model_id):
        if e["event"] == "activated" and e["to_state"] == "active":
            start = e["at"]
        elif start is not None and e["event"] in ("deactivated", "rolled_back"):
            wins.append({"from": start, "to": e["at"], "ended_by": e["event"], "kind": e["detail"].get("kind", "")})
            start = None
    if start is not None:
        wins.append({"from": start, "to": None, "ended_by": None, "kind": ""})
    return wins


def flag_clinical_issue(model_id: str, actor: str, description: str) -> dict:
    """Record that this model's output may have been clinically wrong. This is NOT a rollback: it does not change what is serving. It blocks (re-)activation until closed, and lists the periods
    to review. Whether to take the model out of service is a separate, explicit decision."""
    with db.context.as_system():
        row, card = _load(model_id)
        card["clinical_issue_open"] = {"opened_at": db.now(), "by": actor, "description": description[:500]}
        _save(row, card)
        st = state_of(row, card)
        _event(model_id, row["task"], actor, "clinical_issue_reported", st, st, {"description": description[:200]})
        return {"model_id": model_id, "clinical_issue_open": True, "periods_to_review": exposure(model_id),
                "note": "This does not take the model out of service. Roll back or deactivate it explicitly if it should stop answering."}


def close_clinical_issue(model_id: str, actor: str, note: str) -> dict:
    with db.context.as_system():
        row, card = _load(model_id)
        if not card.get("clinical_issue_open"):
            raise LifecycleError("No clinical issue is open for this model.", "none_open")
        card["clinical_issue_closed"] = {"closed_at": db.now(), "by": actor, "note": note[:500], "issue": card.pop("clinical_issue_open")}
        _save(row, card)
        st = state_of(row, card)
        _event(model_id, row["task"], actor, "clinical_issue_closed", st, st, {"note": note[:200]})
        return {"model_id": model_id, "clinical_issue_open": False}


# ---------------------------------------------------------------- monitoring: only signals that are reliable without ground truth
MIN_RUNS, FAILURE_LOWER_BOUND, WINDOW_SECONDS, KEEP = 50, 0.20, 3600.0, 500
_outcomes: dict[str, deque] = defaultdict(lambda: deque(maxlen=KEEP))
_lock = threading.Lock()


def record_outcome(task: str, ok: bool) -> None:
    """Called by the engine for every run that used the task's active approved model: ok=False only for an error or a timeout of the model itself. Abstentions, rejected input, forbidden
    requests and a busy service are not failures of the model and are never recorded here."""
    with _lock:
        _outcomes[task].append((time.time(), bool(ok)))


def wilson_lower(failures: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = failures / n
    d = 1 + z * z / n
    return max(0.0, ((p + z * z / (2 * n)) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d)


def evaluate_signals(task: str) -> dict:
    """Is there a reliable reason to stop the active model? Never from a handful of runs; never from anything that needs a ground truth we do not have."""
    cut = time.time() - WINDOW_SECONDS
    with _lock:
        recent = [ok for t, ok in _outcomes[task] if t >= cut]
    n, fails = len(recent), sum(1 for ok in recent if not ok)
    problem = active_problem(task)
    if problem:
        return {"task": task, "action": "rollback", "reason": problem, "evidence": {"integrity": False}, "deterministic": True}
    lb = wilson_lower(fails, n)
    if n >= MIN_RUNS and lb >= FAILURE_LOWER_BOUND:
        return {"task": task, "action": "rollback", "reason": f"{fails} of {n} runs in the last hour failed (the lower 95% bound of the failure rate is {lb:.2f}, the limit {FAILURE_LOWER_BOUND})",
                "evidence": {"runs": n, "failures": fails, "failure_rate_lower_bound": round(lb, 3)}, "deterministic": False}
    return {"task": task, "action": "none", "reason": "no reliable signal" if n >= MIN_RUNS else f"only {n} recent runs: too few to judge (need {MIN_RUNS})",
            "evidence": {"runs": n, "failures": fails, "failure_rate_lower_bound": round(lb, 3)}, "deterministic": False}


def maybe_auto_rollback(task: str) -> dict | None:
    """Act on a reliable signal - only when the operator has switched automatic rollback on. Otherwise the decision is recorded as a recommendation and nothing changes."""
    d = evaluate_signals(task)
    if d["action"] != "rollback":
        return None
    if os.environ.get("COSMO_MODEL_AUTO_ROLLBACK") != "1":
        return {**d, "executed": False, "note": "automatic rollback is off; an administrator can roll back"}
    out = rollback(task, "system:monitor", kind="automatic", reason=d["reason"])
    return {**d, "executed": True, "result": out}


def view(model_id: str) -> dict:
    """Everything an administrator needs to decide about one model: where it is, the gates, the approval record, the history, when it was serving, and whether its file still verifies."""
    from ..training import trainer
    with db.context.as_system():
        row, card = _load(model_id)
        ok, why = verify_artifact(card)
        active = _setting(f"active_model:{row['task']}")
    return {"model_id": model_id, "task": row["task"], "state": state_of(row, card, active), "status": row["status"], "is_active": active == model_id,
            "allowed_next_states": sorted(TRANSITIONS.get(state_of(row, card, active), set())), "gates": trainer.evaluate_gates(card), "approval": card.get("approval"),
            "clinical_issue_open": card.get("clinical_issue_open"), "intended_use": card.get("intended_use"), "limitations": card.get("limitations"), "resources": card.get("resources"),
            "dataset": {"id": card.get("dataset_id"), "version_id": card.get("dataset_version_id"), "version": card.get("dataset_version"), "source": card.get("source")},
            "artifact": {"sha256_prefix": str(card.get("artifact_sha256", ""))[:16], "verifies": ok, "detail": why}, "events": events(model_id), "exposure": exposure(model_id)}

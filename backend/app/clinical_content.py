"""Clinical content governance: what the patient assistant may say on a clinician's authority, and what it may not.

Rules enforced here (not in the UI):
* Draft content shipped with the software (`content/kb_drafts.json`, `content/urgent_policy.draft.json`) is NEVER used by the assistant.
  KB drafts are seeded as `draft`; the urgent-symptom draft is not read at all.
* A KB article is usable only when approved by a doctor with a reviewer attestation, and only while its text still matches the text that was
  approved (the SHA-256 recorded at approval). Editing an article returns it to draft.
* The urgent-symptom policy is the built-in conservative default unless `content/urgent_policy.approved.json` exists, validates, and carries an
  approval block whose content hash matches. A hash proves the file was not changed after the named person approved it; it is not a
  cryptographic signature, so the clinic's change control decides who may place the file (docs/clinical_review/APPROVAL_PROCEDURE.md)."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import db

CONTENT_DIR = Path(os.environ.get("COSMO_CONTENT_DIR") or Path(__file__).resolve().parent.parent / "content")

DEFAULT_URGENT = re.compile(r"\b(chest pain|can'?t breathe|cannot breathe|difficulty breathing|severe bleeding|unconscious|"
                            r"suicid|anaphyla|swelling of (the )?(face|lips|tongue|throat)|high fever|fainting)\b", re.I)
DEFAULT_URGENT_MSG = ("This may be urgent. Please contact your local emergency number or go to the nearest emergency service now, "
                      "or call your clinic. This assistant cannot assess emergencies. (Clinic escalation wording is pending clinician approval.)")

MAX_PATTERNS, MAX_PATTERN_LEN, MAX_MESSAGE_LEN = 300, 300, 700
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)[+*{]")          # (a+)+ style: catastrophic backtracking


def sha(obj) -> str:
    data = obj if isinstance(obj, bytes) else json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def article_hash(title: str, body: str, language: str = "en") -> str:
    return sha({"title": title.strip(), "body": body.strip(), "language": language})


# ---------------------------------------------------------------- knowledge base
def kb_drafts() -> list[dict]:
    p = CONTENT_DIR / "kb_drafts.json"
    return json.loads(p.read_text(encoding="utf-8"))["articles"] if p.is_file() else []


def seed_kb() -> int:
    """Insert the shipped drafts as DRAFT rows (never approved). Idempotent by id and by title. Returns the number inserted."""
    n = 0
    for a in kb_drafts():
        if db.q1("SELECT 1 x FROM kb_articles WHERE id=? OR title=?", (a["id"], a["title"])):
            continue
        db.x("INSERT INTO kb_articles(id,title,body,language,review_status,reviewed_by,version,content_origin,claims_to_verify) VALUES(?,?,?,?, 'draft','',1,'ai_draft',?)",
             (a["id"], a["title"], a["body"], a.get("language", "en"), json.dumps(a.get("claims_to_verify", []))))
        n += 1
    return n


def approved_articles() -> list[dict]:
    """Approved AND unmodified since approval. A row whose text no longer matches its approval hash (tampering, direct DB edit, legacy approval without a record) is excluded."""
    out = []
    for a in db.q("SELECT id,title,body,language,version,content_sha256 FROM kb_articles WHERE review_status='approved'"):
        if a["content_sha256"] and a["content_sha256"] == article_hash(a["title"], a["body"], a["language"] or "en"):
            out.append(a)
    return out


def approver_ok(user: dict) -> tuple[bool, str]:
    """In production only a clinician whose credentials an administrator has verified may approve clinical content."""
    if os.environ.get("COSMO_MODE") == "production" and not user.get("verified"):
        return False, "Approval of clinical content requires a verified clinician."
    return True, ""


# ---------------------------------------------------------------- urgent-symptom policy
@dataclass(frozen=True)
class Policy:
    regex: re.Pattern
    message: str
    source: str            # "built-in default" or "approved by <name>"
    approved: bool
    sha256: str = ""


_DEFAULT = Policy(DEFAULT_URGENT, DEFAULT_URGENT_MSG, "built-in default (no clinician-approved policy installed)", False)
_cache: dict = {"key": None, "policy": _DEFAULT, "problem": ""}


def validate_policy(doc: dict) -> Policy:
    """Raises ValueError with a reason if the approved-policy document is unusable. Never returns a partially valid policy."""
    pol, ap = doc.get("policy"), doc.get("approval")
    if not isinstance(pol, dict) or not isinstance(ap, dict):
        raise ValueError("missing policy or approval block")
    for k in ("reviewer_name", "reviewer_role", "registration_number", "approved_on", "content_sha256", "statement"):
        if not str(ap.get(k, "")).strip():
            raise ValueError(f"approval.{k} is required")
    if ap["content_sha256"] != sha(pol):
        raise ValueError("approval hash does not match the policy content")
    msg, pats = str(pol.get("message", "")).strip(), pol.get("patterns")
    if not msg or len(msg) > MAX_MESSAGE_LEN:
        raise ValueError("policy message missing or too long")
    if not isinstance(pats, list) or not pats or len(pats) > MAX_PATTERNS:
        raise ValueError("policy patterns missing or too many")
    compiled = []
    for p in pats:
        if not isinstance(p, str) or not p or len(p) > MAX_PATTERN_LEN or _NESTED_QUANTIFIER.search(p):
            raise ValueError("a policy pattern is empty, too long or has a nested quantifier")
        try:
            compiled.append(re.compile(p, re.I))
        except re.error as e:
            raise ValueError(f"invalid pattern: {e}") from None
    union = re.compile("|".join(f"(?:{c.pattern})" for c in compiled), re.I)
    return Policy(union, msg, f"approved by {ap['reviewer_name']} ({ap['reviewer_role']}) on {ap['approved_on']}", True, ap["content_sha256"])


def urgent_policy() -> Policy:
    p = CONTENT_DIR / "urgent_policy.approved.json"
    try:
        st = p.stat()
    except OSError:
        _cache.update(key=None, policy=_DEFAULT, problem="")
        return _DEFAULT
    key = (st.st_mtime_ns, st.st_size)
    if _cache["key"] == key:
        return _cache["policy"]
    try:
        pol, problem = validate_policy(json.loads(p.read_text(encoding="utf-8"))), ""
    except (ValueError, OSError) as e:                       # includes JSON errors; fail closed to the conservative default
        pol, problem = _DEFAULT, f"approved policy rejected: {e}"
    _cache.update(key=key, policy=pol, problem=problem)
    return pol


def status() -> dict:
    pol = urgent_policy()
    return {"urgent_policy": pol.source, "urgent_policy_approved": pol.approved, "urgent_policy_problem": _cache["problem"],
            "kb_approved": len(approved_articles()), "kb_total": (db.q1("SELECT COUNT(*) c FROM kb_articles") or {"c": 0})["c"]}

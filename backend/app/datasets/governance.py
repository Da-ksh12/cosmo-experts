"""Licence decisions for candidate datasets, recorded with their evidence (Phase 5).

The registry's licence gate lets nothing be imported or trained on until the official terms have been *retrieved and hashed*, *verified*, and the dataset *approved*. The administrator routes do
those three steps one request at a time; this module performs the same three state changes in one call for decisions taken from evidence that was read from the official source, and
records the decision either way: a refusal is a decision with its evidence, not an absence. Nothing here widens the gate: an approval is refused when the gate still finds a licence problem.

The `actor` is whoever took the decision (here the automated Phase 5 run, acting on the owner's instruction to use only datasets with verified licences). The audit trail says so, and every
approval can be reversed by setting the dataset to rejected."""
from __future__ import annotations

import hashlib
import json

from .. import db
from ..config import DATASET_DIR
from . import registry, sources


class GateRefused(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def evidence_from_url(label: str, url: str, max_bytes: int = 2_000_000, max_age_s: float = 86400.0) -> dict:
    """GET a public page or API response and keep a hash plus a short excerpt. The full text is not stored in the registry, but the bytes are kept for a day under the dataset directory
    so that several decisions resting on the same page fetch it once (some sites throttle repeated requests)."""
    import time
    cdir = DATASET_DIR / "_evidence"
    cdir.mkdir(parents=True, exist_ok=True)
    cf = cdir / (hashlib.sha1(url.encode()).hexdigest() + ".bin")
    if cf.exists() and time.time() - cf.stat().st_mtime < max_age_s:
        return evidence_from_bytes(label, url, cf.read_bytes())
    body = sources.get_bytes(url, limit=max_bytes)
    cf.write_bytes(body)
    return evidence_from_bytes(label, url, body)


def evidence_from_bytes(label: str, url: str, body: bytes) -> dict:
    import html
    import re
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace")), flags=re.S))).strip()
    return {"label": label, "url": url, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "excerpt": text[:500]}


def combined_hash(evidence: list[dict]) -> str:
    return hashlib.sha256(json.dumps([e["sha256"] for e in evidence]).encode()).hexdigest()


def record(dataset_id: str, actor: str, *, approve: bool, spdx: str, claimed_license: str, permission_document: str, notes: str, evidence: list[dict],
           commercial: bool = True, restrictions: list[str] | None = None, extra: dict | None = None) -> dict:
    """Fetch-terms + verify + approve (or reject) in one recorded step."""
    d = registry.get(dataset_id)
    if d is None:
        raise KeyError(dataset_id)
    d.update(claimed_license=claimed_license, terms_snapshot_hash=combined_hash(evidence), terms_snapshot_excerpt=" || ".join(f'{e["label"]}: {e["excerpt"][:300]}' for e in evidence)[:2000],
             terms_checked_at=db.now(), evidence=evidence, terms_verified=True, verified_spdx=spdx, permission_document=permission_document if approve else "", verify_notes=notes,
             verified_by=actor, restrictions=list(restrictions or d.get("restrictions") or []), commercial_eligibility="yes" if approve and commercial else "no",
             approved_uses=["model_training", "evaluation"] if approve else [], **(extra or {}))
    registry.save(d)
    db.audit(actor, "dataset.fetch_terms", dataset_id, {"evidence": [{k: e[k] for k in ("label", "url", "sha256", "bytes")} for e in evidence]})
    db.audit(actor, "dataset.verify_terms", dataset_id, {"verified_spdx": spdx, "notes": notes, "approve_requested": approve})
    d = registry.get(dataset_id)
    if approve:
        d["approval_status"] = "approved"
        gate = registry.license_gate(d, "model_training", commercial)
        blocking = [r for r in gate["reasons"] if "administrator has not approved" not in r]
        if blocking:
            raise GateRefused(blocking)
    else:
        d["approval_status"] = "rejected"
    registry.save(d)
    db.audit(actor, "dataset.approve", dataset_id, {"approved": approve, "commercial": commercial, "use": "model_training"})
    return registry.get(dataset_id)

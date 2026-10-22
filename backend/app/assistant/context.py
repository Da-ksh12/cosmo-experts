"""What the conversation already knows, worked out from the conversation itself, so a follow-up never needs the photo or the concern to be sent again.

Sources, in order: what the client attached to THIS message (the person tapped a question or an area), the assessment card that is in this conversation (the photo analysis the person
uploaded), the context stored in the previous answer card (route, topic, concern), and the person's own earlier messages (how long, which symptoms, which products). Nothing is read from
another person's conversation, and an analysis is only ever loaded through photo_analysis.store, which checks that it belongs to the signed-in person.

Nothing here is stored separately: it is derived on each turn from the messages, which are the record. That keeps deletion simple (delete the conversation and the context is gone) and means the
whole model-facing context of a turn is a handful of short facts, never the person's full history."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .. import db
from ..derm import lexicon, reference

MAX_HISTORY = 40            # messages read
MAX_TEXTS = 8               # earlier patient messages used for the facts the person stated


@dataclass
class Ctx:
    analysis_id: str | None = None
    analysis: dict | None = None                # the loaded analysis (loaded through the owner-checked store by the caller), or None
    analysis_ids: list[str] = field(default_factory=list)
    observation_id: str | None = None
    explicit_intent: str | None = None          # a question the person tapped (intent chosen by the screen)
    has_photo: bool = False
    prev_kind: str | None = None
    prev_topic: str | None = None
    concern: str | None = None
    texts: list[str] = field(default_factory=list)      # the person's earlier messages in this conversation, oldest first (not including the current one)
    prior_text: str = ""                                # the one immediately before the current message
    slots: lexicon.Slots = field(default_factory=lexicon.Slots)
    awaiting: list[str] = field(default_factory=list)   # follow-up questions the last answer asked (ids)
    last_card: dict | None = None

    @property
    def explicit_photo(self) -> bool:
        return bool(self.explicit_intent or self.observation_id)


def _sources(raw) -> list[dict]:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def derive(conversation_id: str, current_text: str, explicit: dict | None = None) -> Ctx:
    """Context for one turn. The caller has already checked that the conversation belongs to the signed-in person."""
    c = Ctx()
    if explicit:
        c.analysis_id = explicit.get("analysis_id") or None
        c.observation_id = explicit.get("observation_id") or None
        c.explicit_intent = explicit.get("intent") or None
    rows = db.q("SELECT role,content,sources FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?", (conversation_id, MAX_HISTORY))
    skipped_current = False
    patient_texts: list[str] = []
    for r in rows:
        src = _sources(r["sources"])
        if r["role"] == "patient":
            if not skipped_current and r["content"] == current_text:
                skipped_current = True                      # the message being answered was stored before the answer is built
                continue
            if len(patient_texts) < MAX_TEXTS:
                patient_texts.append(r["content"])
            continue
        card = next((s.get("card") for s in src if s.get("kind") == "card" and isinstance(s.get("card"), dict)), None)
        if card is not None and c.last_card is None:
            c.last_card = card
            cc = card.get("context") or {}
            c.prev_kind, c.prev_topic, c.concern = cc.get("route"), cc.get("topic"), cc.get("concern")
            c.awaiting = [f.get("id") for f in (card.get("follow_ups") or []) if isinstance(f, dict) and f.get("id")]
            if not c.analysis_id and cc.get("analysis_id"):
                c.analysis_id = cc["analysis_id"]
        ids = sorted([s for s in src if s.get("kind") == "photo_analysis" and isinstance(s.get("index"), int) and s.get("analysis_id")], key=lambda s: s["index"])
        if ids and not c.analysis_ids:
            c.analysis_ids = [s["analysis_id"] for s in ids]
    c.texts = list(reversed(patient_texts))
    c.prior_text = patient_texts[0] if patient_texts else ""
    if not c.analysis_id and c.analysis_ids:
        c.analysis_id = c.analysis_ids[0]
    c.has_photo = bool(c.analysis_ids or c.analysis_id)
    c.slots = lexicon.extract_slots(c.texts + [current_text], concern_of)
    return c


def concern_of(text: str) -> str | None:
    """The skin concern a message names (acne, dry skin, ...), if any: what separates one problem from the next in the same conversation."""
    return next((t for t, _ in reference.topics_in(text) if t in lexicon.CONCERN_TOPICS), None)

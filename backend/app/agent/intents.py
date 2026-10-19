"""Which intent does the person's message mean? Cue-word rules where they recognise an action, the nearest written example where they do not, and a guard so that a request to CHANGE something is never
chosen on resemblance alone.

1. The message is compared (embedding model already used for retrieval, run locally) with the hand-written examples in exemplars.py.
2. When the cue-word rules (planner.classify) recognise an action, their reading wins unless an example contradicts it strongly. Where they recognise nothing the closest example decides if it is
   close enough; below that, or when the embedding model is not installed, the request is a question (`ask`), which searches the person's records and abstains without evidence.
3. If the chosen intent changes state (a quotation, a review request, a flagged value, notifications marked read), the message must also contain the words such a request needs; otherwise the
   intent is not used. Even then nothing runs: the agent only proposes it, and the person confirms it separately.

Only the person's own message is ever compared. Nothing from a document or a tool result is."""
from __future__ import annotations

import re
import threading

import numpy as np

from . import planner
from .exemplars import EXEMPLARS

VERSION = "intents-2.0.0"
STRONG = 0.90                    # an example this close overrides the rules' reading of a request to change something
THRESHOLD = 0.75                 # cosine similarity to the closest example; chosen on the development examples, scored on a held-out set (docs/eval/agent.json)
WRITE_INTENTS = ("request_quote", "request_review", "flag_value", "mark_notifications_read")
# the words without which a request to change something is not understood as one
WRITE_CUES = {
    "request_quote": re.compile(r"\b(?:quot(?:e|es|ation|ations)|estimates?)\b", re.I),
    "request_review": re.compile(r"\b(?:review|reviewed|look|check|examine|opinion|second opinion|double check|see)\b", re.I),
    "flag_value": re.compile(r"\b(?:wrong|incorrect|mistake|mistaken|misread|error|not right|off|flag)\b", re.I),
    "mark_notifications_read": re.compile(r"\b(?:read|seen|dismiss|clear|mark)\b", re.I),
}


class ExampleClassifier:
    def __init__(self, examples=EXEMPLARS, embedder=None):
        self._examples = list(examples)
        self._embedder = embedder
        self._vecs: np.ndarray | None = None
        self._lock = threading.Lock()

    def _emb(self):
        if self._embedder is None:
            from ..assistant.retrieval import SentenceTransformerEmbedder
            self._embedder = SentenceTransformerEmbedder()
        return self._embedder

    @property
    def available(self) -> bool:
        return bool(self._emb().available)

    def _matrix(self) -> np.ndarray:
        with self._lock:
            if self._vecs is None:
                self._vecs = np.asarray(self._emb().embed_passages([u for u, _ in self._examples]))
            return self._vecs

    def nearest(self, message: str) -> tuple[str, float, str] | None:
        """(intent, similarity, the example it matched) for the closest example, or None when no embedding model is installed."""
        if not self.available:
            return None
        q = np.asarray(self._emb().embed_passages([" ".join((message or "").split())[:300]]))[0]
        sims = self._matrix() @ q
        i = int(np.argmax(sims))
        return self._examples[i][1], float(sims[i]), self._examples[i][0]


_default: ExampleClassifier | None = None


def get() -> ExampleClassifier:
    global _default
    if _default is None:
        _default = ExampleClassifier()
    return _default


def classify(message: str, clf: ExampleClassifier | None = None) -> planner.Intent:
    rule = planner.classify(message)
    try:
        hit = (clf or get()).nearest(message)
    except Exception:                                            # an embedding failure is not a reason to fail the request: the rules decide
        hit = None
    if rule.kind != "ask" and not (hit is not None and hit[1] >= STRONG and hit[0] != rule.kind):
        return rule                                              # the person used the words of an action (and no example contradicts that strongly): the rules' reading wins. Resemblance
                                                                 # alone is a weak signal - a shared test name ("ferritin") can pull a request toward a question about that test
    if hit is None or hit[1] < THRESHOLD:
        return rule
    kind = hit[0]
    if kind in WRITE_INTENTS and not WRITE_CUES[kind].search(message or ""):
        return rule
    return planner.Intent(kind, slots={"similarity": round(hit[1], 3)}, source="examples")

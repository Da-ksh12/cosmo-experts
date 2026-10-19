"""Intent classification: what is the person asking for? A closed list of intents, chosen by rules over the person's OWN message.

Only the person's message is read here. Text from documents, tool results or approved articles is never given to the classifier, so nothing a document says can pick an intent, a tool or a
document. The classifier returns an intent and a few typed slots (which document, which notification, which price-list items); the fixed sequence of tool calls for each intent lives in
agent.py. A language-model classifier can replace this one (provider.py) only by returning an intent from the same closed list, and only after it has passed its evaluation gates.

Rules run in order; the first that matches wins. Questions that are not requests for one of the listed actions fall to `ask`, which searches the person's documents and the approved articles.
Measured by eval/agent_eval.py on hand-written utterances (eval/agent_scenarios.py); the figures, including every first-run result on phrasing the rules had not seen, are in docs/eval/agent.json.
A wrong guess is cheap by design: it falls to retrieval (which abstains without evidence) or to a clarifying question, and nothing that changes state runs without the person's confirmation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INTENTS = ("help", "list_documents", "explain_document", "list_medications", "list_quotes", "list_prices", "request_quote", "list_notifications", "mark_notifications_read",
           "request_review", "flag_value", "ask")

DOC_WORDS = r"(?:documents?|reports?|prescriptions?|files?|results?|tests?|lab ?reports?|blood ?tests?|scans?)"
DOC_STRICT = r"(?:documents?|reports?|prescriptions?|files?|lab ?reports?|blood ?tests?|test ?reports?)"        # a whole document, not a single result
VALUE = r"(?:value|result|number|reading)"
WRONG = r"(?:wrong|incorrect|a mistake|mistaken|misread|not right|an error)"
QUOTE = r"(?:quot(?:e|es|ation|ations)|estimates?)"
MED = r"(?:medicines?|medications?|drugs?|tablets?|capsules?|pills?)"
WHO = r"(?:doctor|clinician|clinic|team|someone|physician)"


@dataclass
class Intent:
    kind: str
    slots: dict = field(default_factory=dict)
    source: str = "rules"


# Cue words rather than whole phrases: an action word, the thing it acts on, and (where it matters) a request word. First match wins; `help` is last so that "hello, explain my report" is an explanation.
RULES: tuple[tuple[str, re.Pattern], ...] = tuple((k, re.compile(rx, re.I)) for k, rx in (
    ("mark_notifications_read", r"\b(?:mark|set|clear)\b.{0,30}\b(?:notifications?|everything|all|them|alerts?)\b.{0,20}\b(?:read|seen)\b|\bclear\b.{0,15}\bnotifications?\b"),
    ("list_notifications", r"\bnotifications?\b|\b(?:alerts?|updates?|messages?)\b.{0,25}\b(?:from the clinic|for me)\b|\b(?:anything|something|any|what(?:'s| is)) new\b.{0,25}\b(?:clinic|for me)\b"),
    ("request_review", rf"\b(?:ask|have|get|let|request|want|need|please|can|could|would|will|like)\b.{{0,50}}\b{WHO}\b.{{0,50}}\b(?:review|reviewed|look|check|examine|see)\b"
                       rf"|\b(?:get|have)\b.{{0,30}}\b{DOC_WORDS}\b.{{0,15}}\breviewed\b|\brequest (?:a |an )?(?:{WHO} )?review\b"),
    ("flag_value", rf"\b(?:flag|report|mark)\b.{{0,40}}\b{VALUE}\b.{{0,40}}\b{WRONG}\b|\b{WRONG}\b.{{0,30}}\b{VALUE}\b|\b{VALUE}\b.{{0,25}}\b(?:is|was|looks|seems|are|were)\b.{{0,15}}\b{WRONG}\b"
                   rf"|\b{VALUE}\b.{{0,20}}\b(?:was|were|got)\b.{{0,10}}\bmisread\b"),
    ("request_quote", rf"\b(?:request|ask(?:ing)? for|get me|need|want|like|get|prepare|send|can i have|could you|can you|please)\b.{{0,40}}\b{QUOTE}\b|\b{QUOTE}\s+(?:for|of)\b"),
    ("list_prices", r"\b(?:show|list|see|view|tell me|what(?:'s| is| are)?|check)\b.{0,25}\b(?:prices?|price ?list|costs?)\b|\bprice ?list\b|\bhow much\b|\b(?:which|what)\b.{0,15}\b(?:treatments?|services?|procedures?)\b.{0,25}\b(?:offer|provide|have|available)\b"),
    ("list_quotes", rf"\b(?:my|any|all|have|status|approved|pending|show|list|where)\b.{{0,30}}\b{QUOTE}\b|\b{QUOTE}\b.{{0,30}}\b(?:status|approved|pending|do i have|so far)\b"),
    ("list_medications", rf"\b{MED}\b.{{0,50}}\b(?:listed|written|printed|list|lists|prescribed|mentioned)\b|\b(?:list|show)\b.{{0,15}}\b{MED}\b|\bwhat {MED}\b.{{0,30}}\b{DOC_WORDS}\b"),
    ("explain_document", rf"\b(?:explain|summar\w*|break down|understand|walk me through|go through|tell me about|read|interpret)\b.{{0,60}}\b{DOC_WORDS}\b|\bexplain\b.{{0,20}}\b(?:latest|last|recent|new)\b"
                         rf"|\bwhat (?:does|do|is in|'s in)\s+(?:my|the|this|that|these|those|our)\b.{{0,25}}\b{DOC_STRICT}\b"),
    ("list_documents", rf"\b(?:list|show|see|view)\b.{{0,25}}\b(?:my |all |the )?{DOC_WORDS}\b|\b(?:which|what|do i have|have i)\b.{{0,40}}\b{DOC_WORDS}\b.{{0,30}}\b(?:have|uploaded|stored|saved|account)\b"
                       rf"|\bwhat (?:have i|i have|i've)\b.{{0,15}}\b(?:uploaded|stored|saved|added)\b|\b(?:my|all) {DOC_WORDS}\b$|\bwhat (?:documents?|reports?|files?) do i have\b"),
    ("help", r"^\s*(?:hi|hello|hey|namaste|help)\b|\bwhat (?:are you able to|can you|could you|do you)\b.{0,10}\b(?:do|help)\b|\bwho are you\b|\b(?:need|want) (?:some )?help\b|\bhelp (?:me )?(?:with|please)\b|\bwhat you can (?:do|help)\b"),
))
LATEST = re.compile(r"\b(?:latest|last|most recent|newest|recent|new)\b", re.I)
EARLIEST = re.compile(r"\b(?:first|oldest|earliest)\b", re.I)
HEX_ID = re.compile(r"\b[0-9a-f]{32}\b")


def classify(message: str) -> Intent:
    text = " ".join((message or "").split())[:1000]
    for kind, rx in RULES:
        if rx.search(text):
            return Intent(kind)
    return Intent("ask")


def pick_document(message: str, docs: list[dict]) -> tuple[dict | None, str]:
    """Which of the person's documents is meant? Returns (document, how). `docs` is the person's own list, newest first, so a mistaken or hostile reference can only ever resolve to one of
    their own documents. how: explicit_id | name | latest | earliest | only_one | none | ambiguous."""
    if not docs:
        return None, "none"
    m = " ".join((message or "").split())
    for hid in HEX_ID.findall(m.lower()):
        for d in docs:
            if d["id"] == hid:
                return d, "explicit_id"
    low = m.lower()
    named = [d for d in docs if _stem(d.get("filename")) and _stem(d["filename"]) in low]
    if len(named) == 1:
        return named[0], "name"
    if EARLIEST.search(m) and not LATEST.search(m):
        return docs[-1], "earliest"
    if LATEST.search(m):
        return docs[0], "latest"
    if len(docs) == 1:
        return docs[0], "only_one"
    return None, "ambiguous"


def _stem(filename: str | None) -> str:
    s = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", (filename or "").strip()).lower()
    return s if len(s) >= 4 else ""


def match_items(message: str, items: list[dict]) -> list[dict]:
    """Price-list items the message names (longest names first, each at most once). Quantity is read only from an explicit 'N x name' or 'N name' just before the name; otherwise 1."""
    low = " " + " ".join((message or "").lower().split()) + " "
    out, used = [], []
    for it in sorted(items, key=lambda i: -len(i["name"])):
        name = it["name"].lower().strip()
        if len(name) < 3:
            continue
        i = low.find(" " + name)
        if i < 0 or any(a <= i < b for a, b in used):
            continue
        used.append((i, i + len(name) + 1))
        q = re.search(r"(\d{1,2})\s*(?:x|×|sessions? of|of)?\s*$", low[:i + 1])
        out.append({"item_id": it["id"], "qty": max(1, min(100, int(q.group(1)))) if q else 1})
    return out

"""Answers that use ONE photo-analysis session as their context.

When a question arrives with an analysis id (and optionally the id of the observation the person has selected on the photo), the assistant answers from THAT session's real data: the
measured values, the thresholds, the located areas, the limitations the backend declared. Nothing is generated: every sentence is assembled from the stored analysis (or is the fixed statement
that a photo check looks at the photo, not the skin), and the finished text goes through the shared safety policy like every other patient-facing answer.

Routing, in order:
1. an explicit intent from a suggested-question chip, or one recognised in the person's own words ("explain the highlighted area", "how can I take a better photo", "summary for my
   dermatologist", "what happens next", "compare with my previous photo");
2. a question about skin features (acne, redness, spots, dryness, ...): answered honestly with what is and is not available, never guessed;
3. anything else is NOT forced into this context: the normal assistant answers it, and the photo's observations are added to the passages it may cite (so "is my photo too dark?" finds the
   lighting measurement), clearly labelled as coming from the photo check.
General skincare information is never presented as being about the photo: it only ever comes from clinic-approved material and is labelled as general."""
from __future__ import annotations

import re
import time

from ..ai import safety
from . import store

INTENTS = ("measurements", "observation", "better_photo", "what_next", "summary", "compare", "capabilities", "care")
GENERAL_LABEL = "General information from clinic-approved material (not about your photo):"


class ContextError(Exception):
    """The analysis (or the observation) named by the request does not exist for this person. The message is safe to show."""


_RULES: list[tuple[str, re.Pattern]] = [
    ("summary", re.compile(r"\b(summar(?:y|ise|ize)|dermatologist|share (?:it|this)? ?with)\b", re.I)),
    ("compare", re.compile(r"\b(compare|previous photo|earlier photo|last photo|before and after|progress)\b", re.I)),
    ("capabilities", re.compile(r"\b(acne|pimples?|zits?|spots?|redness|red patches?|dry(?:ness)?|flak(?:y|ing)|pigment\w*|dark spots?|texture|pores?|wrinkles?|scars?|scarring|blemish\w*|"
                                r"detect|diagnos\w*|what(?:'s| is) wrong|skin (?:type|condition|problem|issue)s?|analy[sz]e my (?:skin|face))\b", re.I)),
    ("better_photo", re.compile(r"\b(better photo|take a better|take another|retake|re-take|clearer|improve (?:the|my) photo|blurry|too dark|lighting|resolution|how (?:do|can|should) i (?:take|photograph))\b", re.I)),
    ("what_next", re.compile(r"\b(what happens next|what next|next steps?|who (?:sees|reviews|looks)|will (?:a |the )?(?:doctor|clinician)|clinician review)\b", re.I)),
    ("measurements", re.compile(r"\b(what was measured|what did you (?:measure|check)|measurements?|what (?:was|were) checked|explain what was)\b", re.I)),
    ("observation", re.compile(r"\b(highlight\w*|outlined|this area|selected|marker|explain:?)\b", re.I)),
]


def classify(text: str, intent: str | None = None, has_observation: bool = False) -> str | None:
    if intent in INTENTS:
        return intent
    for name, rx in _RULES:
        if rx.search(text or ""):
            if name == "observation" and not has_observation:
                continue
            return name
    if has_observation and re.search(r"\b(explain|what is|what does|tell me about|why)\b", text or "", re.I):
        return "observation"
    return None


def load(owner: str, ctx: dict) -> tuple[dict, dict | None]:
    a = store.get(str(ctx.get("analysis_id", "")), owner)
    if a is None:
        raise ContextError("This photo assessment was not found.")
    obs = None
    if ctx.get("observation_id"):
        obs = next((o for o in a["observations"] if o["id"] == ctx["observation_id"]), None)
        if obs is None:
            raise ContextError("That area is not part of this assessment.")
    return a, obs


def _tone_word(o: dict) -> str:
    return {"ok": "fine", "attention": "needs attention", "info": "for information"}.get(o["tone"], "")


def _src(a: dict, o: dict | None = None, label: str = "", excerpt: str = "") -> dict:
    s = {"kind": "photo_analysis", "analysis_id": a["id"], "label": label or (o["label"] if o else "Photo assessment")}
    if o is not None:
        s.update(observation_id=o["id"], excerpt=(excerpt or (o["evidence"][0]["text"] if o["evidence"] else o["summary"]))[:300])
    elif excerpt:
        s["excerpt"] = excerpt[:300]
    return s


# ---------------------------------------------------------------- the answers (each returns the text, its sources and the observations to highlight)
def _measurements(a: dict) -> dict:
    obs = [o for o in a["observations"] if o["kind"] != "face_region"]
    lines = [f"- {o['label']} ({_tone_word(o)}): {o['summary']}" + (f" {o['evidence'][0]['text']}." if o["evidence"] else "") for o in obs]
    head = f"Here is what the photo check measured on this photo ({a['image']['region_label'] if a.get('image') else 'photo'}):"
    tail = "This describes the photo, not your skin. It is not a diagnosis."
    face = next((o for o in a["observations"] if o["kind"] == "face_region"), None)
    if face:
        tail = "The outlined area on the photo is where the checks treated the face. " + tail
    return {"answer": "\n".join([head, *lines, tail]), "sources": [_src(a, o) for o in obs[:6]], "highlight": [face["id"]] if face else []}


def _observation(a: dict, o: dict) -> dict:
    lines = [f"{o['label']} ({_tone_word(o)}): {o['summary']}"]
    lines += [f"- Measured: {e['text']}" for e in o["evidence"][:4]]
    if o.get("confidence"):
        c = o["confidence"]
        lines.append(f"- Confidence: {round(c['value'] * 100)}% ({'calibrated' if c['calibrated'] else 'not calibrated'}; {c['basis']})")
    else:
        lines.append("- Confidence: this is a measurement from a rule-based check, not a prediction, so there is no confidence score.")
    lines += [f"- Limit: {x}" for x in o["limitations"][:3]]
    if o.get("geometry"):
        lines.append("The outline on the photo shows where this was measured.")
    lines.append("This describes the photo, not your skin. It is not a diagnosis.")
    return {"answer": "\n".join(lines), "sources": [_src(a, o)], "highlight": [o["id"]] if o.get("geometry") else []}


def _better_photo(a: dict) -> dict:
    steps = [s for s in a["sections"]["next_steps"] if s["kind"] == "retake"]
    attention = [o for o in a["observations"] if o["tone"] == "attention"]
    if steps:
        lines = ["To get a photo the check can use:", f"- {steps[0]['detail']}"]
        lines += [f"- {o['label']}: {o['summary']}" for o in attention[:3]]
    else:
        lines = ["This photo passed the checks, so nothing needs fixing.", "- For photos that are easy to compare over time, use similar light, distance and angle each time."]
    return {"answer": "\n".join(lines), "sources": [_src(a, o) for o in attention[:3]] or [_src(a, label="Photo assessment")], "highlight": [o["id"] for o in attention if o.get("geometry")]}


def _what_next(a: dict) -> dict:
    cr = a.get("clinician_review") or {}
    img = a.get("image") or {}
    if img.get("kept"):
        lines = ["Your photo is saved privately. " + cr.get("message", "A clinician reviews accepted photos together with your history."),
                 "Nothing is diagnosed automatically: the clinician decides what, if anything, it means for you.",
                 "You can add another angle, or ask me to prepare a summary for your dermatologist."]
    else:
        lines = ["This photo was not kept, so nothing was sent to a clinician.", "Please retake it (I can tell you how) and it will be checked again."]
    return {"answer": "\n".join(lines), "sources": [_src(a, label="Photo assessment")], "highlight": []}


def _summary(a: dict) -> dict:
    img, out, m = a.get("image") or {}, a.get("outcome") or {}, {}
    when = time.strftime("%d %b %Y", time.localtime(img["taken_at"])) if img.get("taken_at") else time.strftime("%d %b %Y", time.localtime(a["created_at"]))
    lines = ["Photo summary for your dermatologist", f"- Photo: {img.get('region_label', 'photo')}, {when}.", f"- Photo check result: {out.get('headline', 'n/a')}."]
    for o in a["observations"]:
        if o["kind"] != "face_region" and o["evidence"]:
            lines.append(f"- {o['label']}: {o['summary']} ({o['evidence'][0]['text']})")
    lines.append("- Not assessed: skin features such as spots, redness, dryness, pigmentation or texture (no approved analysis exists for them).")
    lines += [f"- Limit: {x}" for x in a["sections"]["cannot"][:2]]
    lines.append("This summary describes the photo only. It is not a diagnosis. Please share it together with the photo.")
    return {"answer": "\n".join(lines), "sources": [_src(a, label="Photo assessment")], "highlight": []}


def _compare(a: dict) -> dict:
    return {"answer": ("I don't measure differences between photos. Your History screen can show two of your photos of the same area side by side: open History, then Progress photos, then Compare two photos. "
                       "A clinician interprets any change."),
            "sources": [_src(a, label="Photo assessment")], "highlight": [], "actions": [{"label": "Open History", "route": "/history?kind=photos"}]}


def _capabilities(a: dict) -> dict:
    notes = a.get("capabilities") or []
    names = ", ".join(n["name"].lower() for n in notes) or "skin features"
    lines = [f"I can't identify skin features in photos yet ({names}): no approved analysis exists for that, and I won't guess.",
             "What I can do: tell you whether the photo is clear enough, show where the checks looked, and explain each measurement.",
             "A clinician reviews accepted photos and can say what, if anything, they show."]
    return {"answer": "\n".join(lines), "sources": [_src(a, label="What can be analysed")], "highlight": []}


_BUILDERS = {"measurements": _measurements, "better_photo": _better_photo, "what_next": _what_next, "summary": _summary, "compare": _compare, "capabilities": _capabilities}


def build(intent: str, a: dict, obs: dict | None = None) -> dict | None:
    """The text, sources and highlight for one photo intent, built from the stored analysis only (before the safety review). None when the intent has no builder or needs an observation that was not given."""
    if intent == "observation":
        return _observation(a, obs) if obs is not None else None
    fn = _BUILDERS.get(intent)
    return fn(a) if fn else None


def answer(owner: str, text: str, ctx: dict) -> dict | None:
    """The photo-aware answer, or None when the question is not about the photo (the normal assistant then answers it). Raises ContextError for an unknown analysis or observation."""
    a, obs = load(owner, ctx)
    intent = classify(text, ctx.get("intent"), obs is not None)
    if intent is None or intent == "care":
        return None
    if intent == "observation":
        if obs is None:
            return None
        res = _observation(a, obs)
    elif intent == "compare" and not (a.get("image") or {}).get("kept"):
        res = {"answer": "There is no kept photo to compare, because this one was not saved. Please retake it first.", "sources": [_src(a, label="Photo assessment")], "highlight": []}
    else:
        res = _BUILDERS[intent](a)
    evidence = [a["image"]["region_label"] if a.get("image") else ""] + [o["summary"] for o in a["observations"]] + [e["text"] for o in a["observations"] for e in o["evidence"]] + a["sections"]["cannot"] + \
        [s["detail"] for s in a["sections"]["next_steps"]] + a["sections"]["meaning"]
    rev = safety.review_text(res["answer"], kind="image_assessment", evidence=evidence)
    if not rev.allowed:                                                # an assembled sentence that reads as a diagnosis or advice is withheld, never edited into something that still looks approved
        return {"answer": rev.replacement or safety.UNSUPPORTED_MSG, "sources": [], "status": "clinician_review_required", "abstained": True, "reason": "safety_policy", "mode": "analysis", "analysis_id": a["id"]}
    return {"answer": res["answer"], "sources": res["sources"], "status": "clinician_review_required", "abstained": False, "mode": "analysis", "analysis_id": a["id"], "intent": intent,
            "highlight": res.get("highlight", []), **({"actions": res["actions"]} if res.get("actions") else {})}


def passages(owner: str, ctx: dict) -> list[dict]:
    """The session's observations as passages the normal assistant may cite (labelled as coming from the photo check). Empty when the analysis is not this person's."""
    try:
        a, _ = load(owner, {"analysis_id": ctx.get("analysis_id")})
    except ContextError:
        return []
    out = []
    for o in a["observations"]:
        ev = " ".join(e["text"] for e in o["evidence"])
        out.append({"text": f"{o['label']}: {o['summary']} {ev}".strip(), "match": f"{o['label']} {o['summary']} {ev} photo check measurement", "kind": "photo_analysis",
                    "analysis_id": a["id"], "observation_id": o["id"], "label": o["label"]})
    return out


def kb_supports_care() -> bool:
    """Is there clinic-approved material that can answer a general skin-care question? Only then is the question offered (a suggestion that can only abstain is not a suggestion)."""
    try:
        from ..ai import knowledge
        from ..assistant.retrieval import get_retriever
        pool = [kp.pool_entry() for kp in knowledge.load("en").passages]
        if not pool:
            return False
        ret = get_retriever()
        return any(h.score >= ret.threshold for h in ret.search("What can I do to care for my skin?", pool, k=3))
    except Exception:
        return False

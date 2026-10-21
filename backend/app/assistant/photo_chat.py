"""Questions about the photo(s) of this conversation (category A).

The photo is resolved from the conversation (or from what the person tapped), loaded through photo_analysis.store (which checks that it is THEIRS), and answered from what the backend really
holds about it:
* a photo that is still being checked, failed, or was not usable gets an honest status and the way forward (retake, how), not a skin answer;
* a photo that passed gets what the checks measured, and what no check can say. Findings about the skin itself appear ONLY if an approved model produced them (none exists today, so the answer
  says so and asks what the person is noticing instead, as a question with real choices, then answers from the library about THAT, labelled as based on their words, never on the photo);
* a measurable question (what was measured, how to take a better photo, what happens next, an area the person tapped) is answered from the stored analysis exactly as before;
* a summary for the dermatologist lists the photo facts and, separately and labelled, what the person said.
Nothing is generated, located or scored here."""
from __future__ import annotations

from ..ai import safety
from ..derm import lexicon, pack as packmod, reference
from ..photo_analysis import answers, store
from . import compose
from .compose import Reply

MEASURABLE = ("measurements", "observation", "better_photo", "what_next", "compare")
HISTORY_QUESTIONS = ("How long has this been there, and has it changed?", "Is there itching, pain, swelling or bleeding?", "Which products are being used on it now?", "Have there been reactions to skincare products before?")
QUESTIONS_FOR_DERMATOLOGIST = ["What do you think this is, and how sure can you be from an examination?", "What routine do you suggest for my skin?", "Do I need any tests or a procedure?",
                               "How will we know whether it is improving, and when should I come back?"]


def _final(card: dict, **kw) -> Reply:
    return compose.finalize(card, screen_kind="image_assessment", **kw)


def load_analysis(owner_id: str, analysis_id: str | None) -> dict | None:
    return store.get(analysis_id, owner_id) if analysis_id else None


def _ctx(a: dict, concern: str | None) -> dict:
    return {"analysis_id": a["id"], "concern": concern}


def _observation_lines(a: dict, limit: int = 8) -> tuple[list[str], list[str]]:
    """(needs-attention lines, fine/for-information lines) about the PHOTO, from the stored observations."""
    attn, fine = [], []
    for o in a["observations"]:
        if o["kind"] == "skin_feature":
            continue
        line = f"{o['label']}: {o['summary']}"
        (attn if o["tone"] == "attention" else fine).append(line)
    return attn[:limit], fine[:limit]


def _findings(a: dict) -> list[dict]:
    return [o for o in a["observations"] if o["kind"] == "skin_feature"]


def _wrap(a: dict, res: dict, *, title: str, concern: str | None, route: dict, extra_sections: list[dict] | None = None, actions: list[dict] | None = None) -> Reply:
    """An answer built by photo_analysis.answers (text, sources, highlight and mode exactly as before), shown as a card as well. The text already passed the image-assessment policy there."""
    lines = [x for x in res["answer"].split("\n") if x.strip()]
    head, rest = (lines[0], lines[1:]) if lines else ("", [])
    items = [x.lstrip("- ").strip() for x in rest]
    card = compose.new_card("photo", title=title, summary=head, sections=[compose.section("answer", "", items)] + (extra_sections or []), context=_ctx(a, concern),
                            actions=actions or [compose.SUMMARY, compose.BOOK], limits=[compose.GENERAL_NOTE])
    extra_actions = [compose.act_nav("ra" + str(i), x["label"], x["route"]) for i, x in enumerate(res.get("actions") or []) if x.get("route")]
    if extra_actions:
        card["actions"] = extra_actions + card["actions"]
    abstained = bool(res.get("abstained"))
    extra = {"mode": "analysis", "intent": res.get("intent"), **({"actions": res["actions"]} if res.get("actions") else {})}
    return Reply(res["answer"], card, "abstained" if abstained else "answered", abstained, res.get("reason", ""), res.get("sources", []), highlight=res.get("highlight", []), analysis_id=a["id"],
                 extra=extra, route=route)


# ---------------------------------------------------------------- no photo / not usable / not finished
def no_photo(route: dict) -> Reply:
    card = compose.new_card("photo", title="I can't see a photo in this conversation",
                            summary="Take a photo or choose one from your gallery and I will check its quality and explain what it can and cannot show. Your photo stays private to your account.",
                            actions=[compose.TAKE_PHOTO], context={})
    return _final(card, status="needs_input", reason="no_photo", route=route)


def gone(route: dict) -> Reply:
    card = compose.new_card("photo", title="That photo assessment is no longer available", summary="It may have been deleted. A new photo can be added at any time.", actions=[compose.TAKE_PHOTO], context={})
    return _final(card, status="needs_input", reason="photo_unavailable", route=route)


def in_progress(a: dict, route: dict) -> Reply:
    stage = {"assessment": "I am checking the photo and looking for the face area."}.get(a.get("stage") or "", "Your photo is waiting its turn to be checked.")
    card = compose.new_card("photo", title="Your photo is still being checked", summary=stage + " The result will appear in the assessment above in a few seconds; you can keep asking general questions meanwhile.",
                            context=_ctx(a, None))
    return _final(card, route=route, reason="in_progress", analysis_id=a["id"])


def not_finished(a: dict, route: dict) -> Reply:
    out = a.get("outcome") or {}
    card = compose.new_card("photo", title=out.get("headline") or "The photo check did not finish", summary=a.get("error") or out.get("message") or "Nothing was saved. Please try again.",
                            actions=[compose.TAKE_PHOTO], context=_ctx(a, None))
    return _final(card, status="needs_input", reason="photo_check_failed", route=route, analysis_id=a["id"])


def unusable(a: dict, route: dict, concern: str | None) -> Reply:
    out = a.get("outcome") or {}
    attn, _ = _observation_lines(a)
    steps = [s for s in a["sections"]["next_steps"] if s["kind"] == "retake"]
    secs = [compose.section("found", "What the photo check found", attn, "avoid")]
    if steps:
        secs.append(compose.section("retake", "How to take a better photo", [steps[0]["detail"]], "care"))
    secs.append(compose.section("cannot", "What this check cannot tell", a["sections"]["cannot"][:2]))
    card = compose.new_card("photo", title=out.get("headline") or "This photo needs to be retaken", summary=out.get("message") or "", sections=secs, context=_ctx(a, concern),
                            actions=[compose.TAKE_PHOTO, compose.BOOK], limits=[compose.GENERAL_NOTE])
    return _final(card, route=route, analysis_id=a["id"], reason="photo_not_usable",
                            sources=[{"kind": "photo_analysis", "analysis_id": a["id"], "label": "Photo assessment"}])


# ---------------------------------------------------------------- a completed assessment
def describe(a: dict, route: dict, slots: lexicon.Slots, concern: str | None) -> Reply:
    """What the photo check found, what it cannot say, and a real question about what the person is noticing."""
    attn, fine = _observation_lines(a)
    findings = _findings(a)
    img = a.get("image") or {}
    secs: list[dict] = []
    highlight: list[str] = []
    if attn:
        secs.append(compose.section("found_attention", "Things in the photo that could be better", attn, "avoid"))
    secs.append(compose.section("found", "What the photo check measured", (fine or ["The photo check found nothing that needs attention in the photo itself."])[:5]))
    if findings:
        lines = []
        for o in findings[:6]:
            c = o.get("confidence")
            lines.append(f"{o['label']}: {o['summary']}" + (f" (confidence {round(c['value'] * 100)}%, {'calibrated' if c['calibrated'] else 'not calibrated'}: {c['basis']})" if c else ""))
            if o.get("geometry"):
                highlight.append(o["id"])
        secs.insert(0, compose.section("model_findings", "What the approved photo analysis found", lines))
    secs.append(compose.section("cannot", "What a photo and this check cannot tell", a["sections"]["cannot"][:3]))
    caps = [c for c in (a.get("capabilities") or []) if not c.get("available")]
    if not findings:
        summary = ("I checked your photo for quality and found the face area. I can't identify spots, marks, redness or conditions in a photo: no approved skin-analysis tool is available here yet, "
                   "and a photo alone cannot show how deep, how long or how serious something is.")
    else:
        summary = "Here is what the approved photo analysis and the quality check found. It is a description of the photo, not a diagnosis."
    card = compose.new_card("photo", title="What I can and can't tell from your photo", summary=summary, sections=secs, context=_ctx(a, concern),
                            follow_ups=[] if concern else [compose.concern_choices()],
                            actions=([compose.act_message("explain-concern", f"Explain {lexicon.TOPIC_LABEL.get(concern, concern)}", f"What is {lexicon.TOPIC_LABEL.get(concern, concern)}?")] if concern else [])
                            + [compose.act_message("pl", "What can a photo show?", "What can a photo show and what can't it?"), compose.SUMMARY, compose.BOOK],
                            limits=[compose.GENERAL_NOTE] + ([f"Not available here yet: {', '.join(c['name'].lower() for c in caps[:4])}."] if caps and not findings else []))
    rep = _final(card, route=route, analysis_id=a["id"], sources=[{"kind": "photo_analysis", "analysis_id": a["id"], "label": "Photo assessment"}])
    rep.highlight = highlight
    return rep


def summary_for_dermatologist(a: dict | None, route: dict, slots: lexicon.Slots, concern: str | None, texts: list[str]) -> Reply:
    """What a dermatologist can read in a minute: the photo facts (if there is a photo), what the person said (labelled, never a finding), what was not assessed, and questions to ask.
    With a photo the text keeps the wording of photo_analysis.answers (a reply the person may already have shared); the person's own words are added as one labelled line."""
    secs: list[dict] = []
    sources: list[dict] = []
    told = slots.summary()
    if concern:
        told = [f"main concern named: {lexicon.TOPIC_LABEL.get(concern, concern)}"] + told
    if a is not None:
        res = answers._summary(a)
        raw = [x for x in res["answer"].split("\n") if x.strip()]
        body, tail = raw[:-1], raw[-1:]
        if told:
            body.append("- What you told me (your own words, not a finding): " + "; ".join(told) + ".")
        text = "\n".join(body + tail)
        evidence = [a["image"]["region_label"] if a.get("image") else ""] + [o["summary"] for o in a["observations"]] + [e["text"] for o in a["observations"] for e in o["evidence"]] + a["sections"]["cannot"]
        rev = safety.review_text(text, kind="image_assessment", evidence=evidence)
        if not rev.allowed:
            text = rev.replacement or safety.UNSUPPORTED_MSG
        secs.append(compose.section("photo", "About the photo", [x.lstrip("- ").strip() for x in body[1:]]))
        sources = res["sources"]
    else:
        text = ""
    secs.append(compose.section("told", "What you told me (in your own words, not a finding)", told or ["Nothing yet. Describing what you notice, how long it has been there, symptoms and products makes this summary more useful."]))
    secs.append(compose.section("not_assessed", "Not assessed by this app", ["Any skin condition or diagnosis.", "Spots, marks, redness, dryness or texture in a photo (no approved analysis exists for them)."]))
    secs.append(compose.section("questions", "Questions you could ask", QUESTIONS_FOR_DERMATOLOGIST))
    card = compose.new_card("photo", title="Summary for your dermatologist", summary="This is a short, factual summary you can show or send. It describes the photo and repeats what you said; it is not a diagnosis.",
                            sections=secs, context={"analysis_id": a["id"] if a else None, "concern": concern},
                            actions=[compose.act_nav("book-share", "Book and share this summary", "/book?share=1" + (f"&analysis={a['id']}" if a else "")), compose.BOOK],
                            limits=["Please share it together with the photo."])
    rep = _final(card, route=route, analysis_id=a["id"] if a else None, sources=sources)
    if text:
        rep.text = text
        rep.extra["mode"] = "analysis"
    return rep


# ---------------------------------------------------------------- the entry point
def reply(owner_id: str, text: str, ctx, route, route_dict: dict, found: reference.Found | None, tri) -> Reply:
    """The answer to a question in the photo category. `ctx` is assistant.context.Ctx (analysis already loaded into ctx.analysis, or None), `route` the router.Route."""
    a = ctx.analysis
    if not ctx.has_photo:
        return no_photo(route_dict)
    if a is None:
        return gone(route_dict)
    concern = route.topic if route.topic in lexicon.CONCERN_TOPICS else ctx.concern
    intent = ctx.explicit_intent if ctx.explicit_intent in answers.INTENTS else None
    if not intent and route.sub == "summary":
        intent = "summary"
    if not intent:
        c = answers.classify(text, None, ctx.observation_id is not None)
        intent = c if c in MEASURABLE + ("summary",) else None
    if intent == "summary":
        return summary_for_dermatologist(a, route_dict, ctx.slots, concern, ctx.texts)
    status = a.get("status")
    if status in ("queued", "processing"):
        return in_progress(a, route_dict)
    if status in ("failed", "cancelled"):
        return not_finished(a, route_dict)
    if intent in MEASURABLE:
        obs = next((o for o in a["observations"] if o["id"] == ctx.observation_id), None) if ctx.observation_id else None
        ask = {"analysis_id": a["id"], "intent": intent, **({"observation_id": obs["id"]} if obs else {})}
        res = answers.answer(owner_id, text, ask)
        if res is not None:
            return _wrap(a, res, title={"measurements": "What the photo check measured", "observation": obs["label"] if obs else "This area", "better_photo": "Taking a better photo",
                                        "what_next": "What happens next", "compare": "Comparing photos"}[intent], concern=concern, route=route_dict)
    if status in ("rejected", "uncertain") and not (found and route.topic):
        return unusable(a, route_dict, concern)
    if route.sub != "detect" and found is not None and (route.topic or ctx.concern) and route.request is None:
        return compose.education(found, text, slots=ctx.slots, concern=concern, analysis_id=a["id"], tri=tri, route=route_dict, photo_present=True)
    return describe(a, route_dict, ctx.slots, concern)

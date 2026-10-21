"""The assistant's front door for a patient's message: understand what is being asked, then ask the part of the system that can really answer it.

    safety (warning signs, the clinic's urgent policy)  ->  what is it about (router)  ->  the handler for that category  ->  one reply (text, card, sources, actions)

The old pipeline sent every question to one retrieval over the person's documents and the clinic-approved articles, so a skin question or a photo question found nothing and ended in
"I couldn't find anything". Now:
    photo       -> the photo of this conversation (photo_chat)             booking/services -> the clinic's own configuration (records_chat)
    education   -> the dermatology library (derm.reference), no document   report          -> the person's report, or the agent's tools (records_chat)
    history / care plan -> the person's own records                         general / unrelated -> a short, honest scope message
    a diagnosis or a medicine is asked for -> what can be said, never a bare refusal
Retrieval over documents and approved articles (engine.answer) is still used where it is the right tool: a question about a value in a report, a quotation, or a clinic article that is not part of
the library. Nothing here can change anything: a state-changing request is only ever proposed, and the person confirms it through the agent's confirmation step."""
from __future__ import annotations

import logging

from .. import clinical_content
from ..derm import lexicon, reference, triage as triage_mod
from . import compose, context as ctxmod, engine, photo_chat, records_chat, router
from .compose import Reply

log = logging.getLogger("cosmo.assistant")


def _from_engine(out: dict, route_dict: dict) -> Reply:
    """An answer of the retrieval pipeline (documents, approved articles, quotations) as a reply. Its status is only 'answered' or 'abstained': 'clinician review required' is reserved for escalation."""
    extra = {k: out[k] for k in ("notice", "quarantined_text", "llm_mode", "mode", "model", "dropped_unverified_claims", "intent", "actions") if k in out and k != "actions"}
    abstained = bool(out.get("abstained"))
    card = compose.new_card("report" if any(s.get("kind") == "document" for s in out.get("sources", [])) else "education", title="", summary=out["answer"], context={})
    rep = Reply(out["answer"], card, "abstained" if abstained else "answered", abstained, out.get("reason", ""), out.get("sources", []), highlight=out.get("highlight", []), analysis_id=out.get("analysis_id"),
                extra=extra, route=route_dict)
    if out.get("actions"):
        card["actions"] = [compose.act_nav("a" + str(i), a["label"], a["route"]) for i, a in enumerate(out["actions"]) if a.get("route")]
    return rep


def _engine(user: dict, conversation_id: str, text: str, explicit: dict | None, route_dict: dict) -> Reply:
    return _from_engine(engine.answer(user["id"], conversation_id, text, explicit), route_dict)


def _looks_like_slot_answer(text: str, ctx: ctxmod.Ctx) -> bool:
    """A short statement that answers a question the last answer asked ("It has been there for about 4 weeks.", "It is itchy.")."""
    t = (text or "").strip()
    if not (ctx.awaiting or ctx.concern) or "?" in t or len(t.split()) > 14:
        return False
    new = lexicon.extract_slots([t])
    return bool(new.duration_days is not None or new.symptoms or new.none_of_symptoms or new.products or new.prior_reaction is not None)


def slot_ack(text: str, ctx: ctxmod.Ctx, route: router.Route, route_dict: dict) -> Reply | None:
    """Thank the person for an answer to a follow-up question, say what changes because of it, and ask the next question (if any). Uses only the person's words and the library."""
    topic = ctx.concern or ctx.prev_topic
    res = reference.by_topic(topic)
    if res is None or topic not in lexicon.CONCERN_TOPICS:
        return None
    slots = ctx.slots
    a = res.article
    secs: list[dict] = []
    heed = {"pain", "swelling", "bleeding", "spreading", "discharge", "fever", "blisters", "change"} & slots.symptoms
    if heed:
        items = res.sections.get("see_clinician", [])[:3] + res.sections.get("urgent", [])[:1]
        secs.append(compose.section("because", "Because of what you mentioned", [f"You mentioned: {', '.join(sorted(heed))}. These are worth mentioning to a clinician, and some are a reason not to wait."] + items, "avoid"))
    elif slots.duration_days is not None and slots.duration_days >= 42 and topic in ("acne", "acne-marks", "pigmentation", "redness", "eczema"):
        secs.append(compose.section("because", "Because of how long this has lasted", ["It has been present for a while. Care at home that has not helped after several weeks is a good reason to ask a dermatologist."] + res.sections.get("see_clinician", [])[:2]))
    else:
        secs.append(compose.section("because", "What stays the same", ["The gentle routine and the habits to avoid in the library still apply. Noting this helps a clinician if you decide to book."]))
    if slots.pregnant:
        secs.append(compose.section("contraindications", "When this advice may not apply", ["Because you mentioned pregnancy or breastfeeding: ask a doctor or pharmacist before starting any active ingredient."], "avoid"))
    lab = lexicon.TOPIC_LABEL.get(topic, topic)
    card = compose.new_card("education", title="Thanks, I have noted that", summary=f"This helps me keep the answer about {lab} relevant to you. It does not change what a photo can show.", sections=secs,
                            context={"topic": topic, "concern": topic, "analysis_id": ctx.analysis_id}, noted=slots.summary(), follow_ups=compose.follow_ups(topic, slots),
                            actions=[compose.act_message("routine", "Suggest a gentle routine", f"Suggest a gentle routine for {lab}"), compose.act_message("avoid", "What should I avoid?", f"What should I avoid with {lab}?"),
                                     compose.SUMMARY, compose.BOOK],
                            review={"tier": res.tier, "label": res.label}, limits=[compose.GENERAL_NOTE])
    if route.triage.escalated():
        card["escalation"] = compose.escalation(route.triage)
    return compose.finalize(card, route=route_dict, analysis_id=ctx.analysis_id)


def respond(user: dict, conversation_id: str, text: str, explicit: dict | None = None, request_id: str = "") -> Reply:
    """The reply to one message of a patient's conversation. The caller has already checked that the conversation is theirs and, when `explicit` names a photo assessment, that it is theirs too."""
    ctx = ctxmod.derive(conversation_id, text, explicit)
    if ctx.analysis_id:
        try:
            ctx.analysis = photo_chat.load_analysis(user["id"], ctx.analysis_id)
        except Exception:                                    # an analysis that cannot be read is treated as gone, never guessed
            log.exception("photo analysis could not be loaded")
            ctx.analysis = None
    route = router.classify(text, has_photo=ctx.has_photo, prev_kind=ctx.prev_kind, prev_topic=ctx.prev_topic, prior_text=ctx.prior_text, slots=ctx.slots, explicit_photo=ctx.explicit_photo)
    rd = route.public()
    pol = clinical_content.urgent_policy().regex.search(text or "")
    # ---- G: warning signs and emergencies end the normal flow
    if pol or route.kind == "urgent":
        tri = route.triage if route.triage.level in ("emergency", "urgent") else triage_mod.Triage("emergency", ["urgent-symptom-policy"], ["You mentioned something that the clinic's urgent-symptom policy treats as urgent."])
        return compose.urgent(tri, route=rd, policy_hit=bool(pol), analysis_id=ctx.analysis_id)
    # ---- a diagnosis or a medicine: say what can be said
    if route.request:
        found = reference.find(text, concern=ctx.concern)
        concern = route.topic if route.topic in lexicon.CONCERN_TOPICS else ctx.concern
        return compose.declined(route.request, found, text, slots=ctx.slots, concern=concern, analysis_id=ctx.analysis_id, tri=route.triage, route=rd, photo_present=ctx.has_photo)
    if _looks_like_slot_answer(text, ctx) and route.kind in ("education", "photo", "general", "unrelated"):
        ack = slot_ack(text, ctx, route, rd)
        if ack is not None:
            return ack
    kind = route.kind
    if kind == "photo":
        found = reference.find(text, concern=ctx.concern) if (route.topic or ctx.concern) else None
        return photo_chat.reply(user["id"], text, ctx, route, rd, found, route.triage)
    if kind == "booking":
        if route.sub == "quote":
            return _engine(user, conversation_id, text, explicit, rd)
        return records_chat.booking_reply(user, text, rd, route.sub or "appointment", route.triage)
    if kind == "report":
        rep = records_chat.report(user, text, rd, request_id)
        if rep is not None:
            return rep
        out = engine.answer(user["id"], conversation_id, text, explicit)                 # a value in the person's report, or a clinic article about reports
        if out.get("abstained") and out.get("reason") not in ("clinical_decision", "urgent") and not records_chat.has_reports(user["id"]):
            return records_chat.no_reports(rd)                                           # nothing to read yet: say what to do, not "nothing found"
        return _from_engine(out, rd)
    if kind == "care_plan":
        return records_chat.care_plan(user, rd)
    if kind == "history":
        if route.sub == "summary":
            return photo_chat.summary_for_dermatologist(None, rd, ctx.slots, ctx.concern, ctx.texts)
        return records_chat.history(user, rd)
    if kind == "education":
        found = reference.find(text, concern=ctx.concern, prefer=route.topic if route.inherited else None)
        if found is not None:
            concern = route.topic if route.topic in lexicon.CONCERN_TOPICS else ctx.concern
            return compose.education(found, text, slots=ctx.slots, concern=concern, analysis_id=ctx.analysis_id, tri=route.triage, route=rd, photo_present=ctx.has_photo)
        out = engine.answer(user["id"], conversation_id, text, explicit)             # a clinic article that is not part of the library, or the person's own documents
        if not out.get("abstained") and out.get("reason") != "clinical_decision":
            return _from_engine(out, rd)
        return compose.no_library_answer(text, route=rd, mode=reference.reference_mode())
    if kind == "general":
        return compose.general(route=rd, greeting=True)
    out = engine.answer(user["id"], conversation_id, text, explicit)
    if not out.get("abstained") and out.get("reason") != "clinical_decision":
        return _from_engine(out, rd)
    return compose.unrelated(route=rd)


def library_state() -> dict:
    """Whether the skin library can answer a general question right now (so the screen does not suggest questions that would only lead to 'not available')."""
    try:
        shown = reference.resolve_all()
    except Exception:
        log.exception("library state could not be read")
        shown = {}
    approved = sum(1 for r in shown.values() if r.tier == "approved")
    return {"available": bool(shown), "mode": reference.reference_mode(), "approved": approved, "labelled_reference": len(shown) - approved}


def starters(user: dict) -> list[dict]:
    from .. import db
    has_docs = bool(db.q1("SELECT 1 x FROM documents WHERE owner_id=? AND deleted=0 AND status='processed' LIMIT 1", (user["id"],)))
    return compose.starters(has_docs, library=library_state()["available"])

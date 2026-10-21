"""Answer cards: the structured, plain-language answers the chat shows, built from the dermatology library and the person's own facts, never from a model.

A card (assistant-card/1) holds: a title, a short summary, sections of statements, the plain meaning of terms, an escalation banner ONLY when the warning-sign rules raised one, the review label
of the library (clinic-approved, or "not reviewed by the clinic's dermatologist yet"), the sources (publisher, title, link, the date the page showed), follow-up questions that fill in what the
assistant could use to be more careful, and actions that each do something real (send a question, open a screen). `context` records what the next turn needs (route, topic, concern, photo).

Every card is also rendered to plain text (the `answer`), which is what is read aloud, searched and shown by an older screen. The text passes the shared safety policy for skincare guidance
(ai/safety.py): no diagnosis, no medicine change, no prescription-only drug, no dose, no certainty claim, no emergency wording except the clinic's own. Nothing here invents a finding, a
location, a score or a price."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import quote

from ..ai import safety
from ..derm import lexicon, pack as packmod, reference, triage as triage_mod

log = logging.getLogger("cosmo.assistant")
CARD_V = 1
OGL_NOTICE = "Contains public sector information licensed under the Open Government Licence v3.0."
MEDLINE_CREDIT = "Source: MedlinePlus, National Library of Medicine."
GENERAL_NOTE = "General information. It is not a diagnosis or a treatment plan."
LABEL = lexicon.TOPIC_LABEL
SEC = packmod.SECTION_TITLES


# ---------------------------------------------------------------- the reply every handler returns
@dataclass
class Reply:
    text: str
    card: dict | None = None
    status: str = "answered"                   # answered | abstained | escalated | needs_input | needs_confirmation
    abstained: bool = False
    reason: str = ""
    sources: list[dict] = field(default_factory=list)       # the chips older screens show (document, quotation, knowledge_base, photo_analysis ...)
    highlight: list[str] = field(default_factory=list)
    analysis_id: str | None = None
    actions: list[dict] = field(default_factory=list)       # proposals that need the person's confirmation (agent)
    extra: dict = field(default_factory=dict)
    route: dict = field(default_factory=dict)

    def payload(self) -> dict:
        out = {"answer": self.text, "sources": self.sources, "status": self.status, "abstained": self.abstained, "route": self.route}
        if self.reason:
            out["reason"] = self.reason
        if self.card is not None:
            out["card"] = self.card
        if self.highlight:
            out["highlight"] = self.highlight
        if self.analysis_id:
            out["analysis_id"] = self.analysis_id
        if self.actions:
            out["actions"] = self.actions
        out.update(self.extra)
        return out


# ---------------------------------------------------------------- actions (each one does something real)
def act_message(aid: str, label: str, text: str, **extra) -> dict:
    return {"id": aid, "label": label, "kind": "message", "text": text, **extra}


def act_nav(aid: str, label: str, route: str) -> dict:
    return {"id": aid, "label": label, "kind": "navigate", "route": route}


BOOK = act_nav("book", "Book a dermatologist appointment", "/book")
UPLOAD_REPORT = act_nav("upload-report", "Upload a medical report", "/reports")
TAKE_PHOTO = act_nav("take-photo", "Take or upload a photo", "/photo")
SUMMARY = act_message("summary", "Prepare a summary for my dermatologist", "Prepare a summary for my dermatologist")


def concern_choices() -> dict:
    return {"id": "concern", "text": "What are you noticing?", "choices": [
        {"label": "Pimples or bumps", "reply": "I have pimples or bumps on my skin."},
        {"label": "Dark marks or uneven tone", "reply": "I have dark marks or uneven skin tone."},
        {"label": "Redness or irritation", "reply": "My skin is red and irritated."},
        {"label": "Dryness or flaking", "reply": "My skin is dry and flaky."},
        {"label": "An itchy rash", "reply": "I have an itchy rash."},
        {"label": "A mole or spot that changed", "reply": "I have a mole that has changed."},
    ]}


def follow_ups(topic: str | None, slots: lexicon.Slots) -> list[dict]:
    """Questions that would let the next answer be more careful, only for a concern, only about what the person has not already said, at most two at a time."""
    if topic not in lexicon.CONCERN_TOPICS:
        return []
    out = []
    if slots.duration_days is None:
        out.append({"id": "duration", "text": "How long has this been there?", "choices": [
            {"label": "About a week", "reply": "It has been there for about 1 week."}, {"label": "About a month", "reply": "It has been there for about 4 weeks."},
            {"label": "A few months", "reply": "It has been there for about 3 months."}, {"label": "More than a year", "reply": "It has been there for more than a year."}]})
    if not slots.known_symptoms():
        out.append({"id": "symptoms", "text": "Is there itching, pain, swelling or bleeding, or has it changed quickly?", "choices": [
            {"label": "Itching", "reply": "It is itchy."}, {"label": "Pain", "reply": "It is painful."}, {"label": "Swelling", "reply": "There is swelling."},
            {"label": "Bleeding", "reply": "It bleeds sometimes."}, {"label": "None of these", "reply": "None of these symptoms."}]})
    if not slots.products:
        out.append({"id": "products", "text": "Which products are you using on it now? Type them below, or choose:", "choices": [{"label": "No products", "reply": "I am not using any products on it."}]})
    if slots.prior_reaction is None:
        out.append({"id": "reactions", "text": "Have you reacted to skincare products before?", "choices": [
            {"label": "Yes", "reply": "I have reacted to skincare products before."}, {"label": "No", "reply": "I have never reacted to skincare products."}]})
    return out[:2]


# ---------------------------------------------------------------- the card
def new_card(route: str, *, title: str = "", summary: str = "", sections: list[dict] | None = None, context: dict | None = None, **kw) -> dict:
    card = {"v": CARD_V, "route": route, "title": title, "summary": summary, "sections": sections or [], "context": {"route": route, **(context or {})}}
    for k, v in kw.items():
        if v not in (None, [], {}, ""):
            card[k] = v
    return card


def section(sid: str, title: str, items: list[str], tone: str = "info") -> dict:
    return {"id": sid, "title": title, "items": [i for i in items if i], "tone": tone}


def source_entries(pk: packmod.Pack, a: packmod.Article) -> tuple[list[dict], list[str]]:
    srcs = pk.source_list(a)
    notes = []
    if any(s["terms"] == "ogl_v3" for s in srcs):
        notes.append(OGL_NOTICE)
    if any(s["id"].startswith("medlineplus") for s in srcs):
        notes.append(MEDLINE_CREDIT)
    return srcs, notes


def render_text(card: dict) -> str:
    """The card as plain text (read aloud, searched, and shown by a screen that does not know cards). Sources come last so that a reader sees the answer first."""
    out: list[str] = []
    esc = card.get("escalation")
    if esc:
        out += [esc.get("message") or esc.get("reason") or "", ""]
    if card.get("title"):
        out.append(card["title"])
    if card.get("summary"):
        out.append(card["summary"])
    if card.get("noted"):
        out.append("What you told me: " + "; ".join(card["noted"]) + ".")
    for s in card.get("sections", []):
        if not s.get("items"):
            continue
        out += ["", (s["title"] + ":") if s.get("title") else ""]
        out += [f"- {i}" for i in s["items"]]
    for q in card.get("follow_ups", []):
        out += ["", q["text"]]
    rv = card.get("review")
    if rv and rv.get("label"):
        out += ["", rv["label"]]
    if card.get("limits"):
        out += [""] + card["limits"]
    srcs = card.get("sources") or []
    if srcs:
        out += ["", "Sources: " + "; ".join(f"{s['publisher']}: {s['title']}" for s in srcs)]
    out += [n for n in card.get("attributions", [])]
    return "\n".join(x for x in out).strip()


def screen(text: str, evidence: list[str] | None = None, kind: str = "skincare_guidance") -> tuple[bool, str, list[str]]:
    """The shared safety policy over the finished text: (allowed, text to use, codes). 'skincare_guidance' for general skin education (no doses, no strengths, no prescription drugs);
    'image_assessment' for a description of a photo (a model's calibrated confidence is a percentage, not a dose)."""
    rev = safety.review_text(text, kind=kind, evidence=evidence or [])
    if rev.allowed:
        return True, text, []
    log.warning("assistant card withheld by the safety policy: %s", ",".join(rev.codes()))
    return False, rev.replacement or safety.UNSUPPORTED_MSG, rev.codes()


def _book_with_photo(card: dict, analysis_id: str | None) -> None:
    """When the conversation has a photo assessment, 'book' opens the booking screen with that assessment offered (the person still chooses whether to share it)."""
    aid = analysis_id or (card.get("context") or {}).get("analysis_id")
    if not aid:
        return

    def fix(acts: list[dict]) -> list[dict]:
        out = []
        for a in acts:
            if a.get("kind") == "navigate" and (a.get("route") or "").split("?")[0] == "/book" and "analysis=" not in a["route"]:
                a = {**a, "route": a["route"] + ("&" if "?" in a["route"] else "?") + "analysis=" + quote(str(aid), safe="")}
            out.append(a)
        return out
    if card.get("actions"):
        card["actions"] = fix(card["actions"])
    if (card.get("escalation") or {}).get("actions"):
        card["escalation"]["actions"] = fix(card["escalation"]["actions"])


def finalize(card: dict, *, status: str = "answered", abstained: bool = False, reason: str = "", sources: list[dict] | None = None, route: dict | None = None,
             analysis_id: str | None = None, extra: dict | None = None, screened: bool = True, screen_kind: str = "skincare_guidance") -> Reply:
    """`screened=False` is for cards made of the person's own records or the clinic's own words (a report's printed values, a clinician's care plan, the clinic's contact details), whose
    statements were checked where they were made or are the clinician's own; the guidance policy would wrongly treat 'Hemoglobin 10 g/dL' as a dose."""
    _book_with_photo(card, analysis_id)
    text = render_text(card)
    ok, shown, codes = screen(text, None, screen_kind) if screened else (True, text, [])
    if not ok:                                                    # never edit generated text into something that still looks reviewed: replace it with the standard message
        c = new_card(card["route"], title="", summary=shown, context=card.get("context"), actions=[BOOK])
        return Reply(shown, c, "abstained", True, "safety_policy", [], route=route or {}, extra={"codes": codes})
    return Reply(text, card, status, abstained, reason, sources or [], route=route or {}, analysis_id=analysis_id, extra=extra or {})


# ---------------------------------------------------------------- education (B)
def _wanted(found: reference.Found, text: str, route_sub: str | None) -> list[str]:
    wants = lexicon.wanted_sections(text)
    if found.resolved.article.topic == "warning-signs" and "see_clinician" not in wants:
        wants = wants + ["see_clinician", "urgent"]
    return wants


def education(found: reference.Found, text: str, *, slots: lexicon.Slots, concern: str | None, analysis_id: str | None, tri: triage_mod.Triage | None = None,
              route: dict | None = None, photo_present: bool = False, topic_hint: str | None = None) -> Reply:
    pk = packmod.load()
    r = found.resolved
    a = r.article
    wants = _wanted(found, text, None)
    sections: list[dict] = []
    shown_ids: list[str] = []
    for sec in packmod.SECTION_ORDER:
        if sec in wants and r.sections.get(sec):
            sections.append(section(sec, SEC[sec], r.sections[sec], "urgent" if sec == "urgent" else "avoid" if sec == "avoid" else "care" if sec == "self_care" else "info"))
            shown_ids.append(sec)
    if r.sections.get("clinic_text"):                          # the clinician rewrote the article: their approved text is what is shown
        sections.append(section("clinic_text", "From the clinic", r.sections["clinic_text"]))
        shown_ids.append("clinic_text")
    sec2 = found.secondary
    if sec2 is not None:
        take = [s for s in ("self_care", "avoid", "monitor") if s in wants and sec2.sections.get(s)] if found.secondary_role == "concern" else \
               [s for s in packmod.SECTION_ORDER if s in wants and s not in ("what_it_is", "see_clinician", "urgent") and sec2.sections.get(s)][:1]
        for s in take:
            ttl = f"For {LABEL.get(sec2.article.topic, sec2.article.title.lower())}" if found.secondary_role == "concern" else f"{sec2.article.title}: {SEC[s].lower()}"
            sections.append(section(f"{sec2.article.topic}:{s}", ttl, sec2.sections[s], "avoid" if s == "avoid" else "care" if s == "self_care" else "info"))
    notes: list[str] = []
    if slots.pregnant:
        notes.append("Because you mentioned pregnancy or breastfeeding: ask a doctor or pharmacist before starting any active ingredient, as some are not suitable at this time.")
    if slots.child:
        notes.append("This information is written for adults and older children. For a baby or young child, ask a doctor or pharmacist before using any product.")
    if a.contraindications and (set(shown_ids) & {"self_care", "avoid"} or a.topic in ("ingredients", "routine")):
        notes += a.contraindications
    if notes:
        sections.append(section("contraindications", "When this advice may not apply", notes, "avoid"))
    topic = a.topic
    cn = topic if topic in lexicon.CONCERN_TOPICS else (concern if concern in lexicon.CONCERN_TOPICS else None)
    srcs, attrib = source_entries(pk, a)
    noted = slots.summary() if cn else []
    summary = r.summary
    if photo_present and cn and topic in lexicon.CONCERN_TOPICS:
        summary = f"You asked about {LABEL.get(topic, topic)}. This is general information from the library, not a finding from your photo. " + summary
    card = new_card("education", title=a.title, summary=summary, sections=sections, context={"topic": topic, "concern": cn, "analysis_id": analysis_id},
                    terms=[{"term": t.term, "plain": t.plain} for t in a.terms] if "what_it_is" in shown_ids else [], noted=noted,
                    review={"tier": r.tier, "label": r.label, "articles": [{"id": a.id, "title": a.title, "version": r.version, "reviewed_at": r.reviewed_at}] +
                            ([{"id": sec2.article.id, "title": sec2.article.title, "version": sec2.version, "reviewed_at": sec2.reviewed_at}] if sec2 is not None else [])},
                    sources=srcs, attributions=attrib, limits=[GENERAL_NOTE] + [x for x in a.limitations if len(x) < 220][:1])
    if sec2 is not None:                                       # the second article's sources count as well
        s2, at2 = source_entries(pk, sec2.article)
        have = {s["id"] for s in srcs}
        card["sources"] = srcs + [s for s in s2 if s["id"] not in have]
        card["attributions"] = sorted(set(attrib) | set(at2))
        if sec2.tier == "reference" and r.tier == "approved":
            card["review"]["tier"], card["review"]["label"] = "reference", reference.REFERENCE_LABEL
    # follow-ups and actions that do something
    card["follow_ups"] = follow_ups(cn, slots)
    acts: list[dict] = []
    lab = LABEL.get(cn or topic, "this")
    if cn:
        if "self_care" not in shown_ids:
            acts.append(act_message("routine", "Suggest a gentle routine", f"Suggest a gentle routine for {lab}"))
        if "avoid" not in shown_ids:
            acts.append(act_message("avoid", "What should I avoid?", f"What should I avoid with {lab}?"))
        if "monitor" not in shown_ids:
            acts.append(act_message("monitor", "How do I keep track of it?", f"How do I keep track of {lab}?"))
        if "see_clinician" not in shown_ids:
            acts.append(act_message("see", "When should I see a doctor?", f"When should I see a doctor about {lab}?"))
        acts.append(SUMMARY)
    elif topic in ("sun-protection", "routine", "ingredients", "habits"):
        acts.append(act_message("what-for", "I have a skin concern", "I have a skin concern I would like to talk about"))
    acts.append(BOOK)
    if photo_present is False and cn:
        acts.append(TAKE_PHOTO)
    card["actions"] = acts[:6]
    if tri is not None and tri.escalated():
        card["escalation"] = escalation(tri)
    reply = finalize(card, route=route, analysis_id=analysis_id,
                     sources=[{"kind": "knowledge_base", "article_id": x["id"], "title": x["title"], "version": x.get("version", 1), "provenance": "approved_reference"} for x in card["review"]["articles"]]
                     if r.tier == "approved" else
                     [{"kind": "derm_source", "source_id": s["id"], "publisher": s["publisher"], "title": s["title"], "url": s["url"]} for s in card["sources"][:6]],
                     extra={"mode": "derm_library", "tier": card["review"]["tier"]})
    return reply


def escalation(tri: triage_mod.Triage) -> dict:
    """The banner for a raised level: what was noticed, why it matters and the one next step. Written as an explanation, never as an alarm."""
    title = {"emergency": "This needs emergency help now", "urgent": "This should be looked at today", "prompt": "Worth booking an appointment soon"}[tri.level]
    acts = []
    if tri.level in ("urgent", "prompt"):
        acts.append(act_nav("book-urgent" if tri.level == "urgent" else "book", "Book an appointment" if tri.level == "prompt" else "Ask the clinic for a same-day appointment",
                            "/book?urgency=urgent" if tri.level == "urgent" else "/book"))
    if tri.article:
        acts.append(act_message("read-more", "Read about these signs", "What are the warning signs on the skin?"))
    return {"level": tri.level, "title": title, "reason": " ".join(tri.reasons), "message": triage_mod.message(tri), "rules": tri.rules, "sources": tri.sources, "actions": acts}


# ---------------------------------------------------------------- urgent (G)
def urgent(tri: triage_mod.Triage, *, route: dict, policy_hit: bool = False, analysis_id: str | None = None) -> Reply:
    """An emergency stops the normal flow: the clinic's urgent message and the reason, nothing else. A same-day level keeps a short explanation and the next step."""
    esc = escalation(tri)
    if tri.level == "emergency":
        card = new_card("urgent", title="", summary="", escalation=esc, context={"analysis_id": analysis_id}, actions=[act_message("read-more", "Read about these signs", "What are the warning signs on the skin?")])
        return finalize(card, status="escalated", abstained=True, reason="urgent", route=route, analysis_id=analysis_id, extra={"escalation": tri.level})
    pk = None
    sections: list[dict] = []
    try:
        res = reference.resolve_all().get(tri.article) if tri.article else None
        pk = packmod.load()
    except packmod.PackError:
        res = None
    if res is not None:
        sections = [section("urgent", SEC["urgent"], res.sections.get("urgent", []), "urgent")] if res.sections.get("urgent") else []
        sections += [section("see_clinician", SEC["see_clinician"], res.sections.get("see_clinician", [])[:4])] if res.sections.get("see_clinician") else []
    srcs: list[dict] = []
    attrib: list[str] = []
    if res is not None and pk is not None:
        srcs, attrib = source_entries(pk, res.article)
    card = new_card("urgent", title="Why I am raising this", summary="", escalation=esc, sections=sections, sources=srcs, attributions=attrib, context={"analysis_id": analysis_id},
                    review={"tier": res.tier, "label": res.label} if res is not None else None, actions=[BOOK, act_message("read-more", "Read about these signs", "What are the warning signs on the skin?")])
    return finalize(card, status="escalated", abstained=False, reason="warning_signs", route=route, analysis_id=analysis_id, extra={"escalation": tri.level})


# ---------------------------------------------------------------- a request that cannot be granted: a diagnosis or a medicine (still helpful)
DIAGNOSIS_SUMMARY = ("I can't diagnose a skin condition, and no photo or description can establish one; that needs a clinician who examines you. "
                     "What I can do is share general information about what you are asking about and how a dermatologist would look into it.")
MEDICINE_SUMMARY = ("I can't recommend or choose medicines or doses; a doctor who has examined you decides that. I can explain gentle, non-prescription skincare in general, "
                    "describe what kinds of ingredients exist, and help you prepare for the appointment.")


def declined(kind: str, found: reference.Found | None, text: str, *, slots: lexicon.Slots, concern: str | None, analysis_id: str | None, tri: triage_mod.Triage | None, route: dict,
             photo_present: bool) -> Reply:
    summary = DIAGNOSIS_SUMMARY if kind == "diagnosis" else MEDICINE_SUMMARY
    sections: list[dict] = []
    srcs: list[dict] = []
    attrib: list[str] = []
    review = None
    ctx_topic = None
    if found is not None:
        r = found.resolved
        ctx_topic = r.article.topic
        if kind == "diagnosis":
            sections = [section("what_it_is", SEC["what_it_is"], r.sections.get("what_it_is", [])[:3]), section("photo_limits", SEC["photo_limits"], r.sections.get("photo_limits", [])[:2]),
                        section("see_clinician", SEC["see_clinician"], r.sections.get("see_clinician", [])[:4])]
        else:
            sections = [section("self_care", SEC["self_care"], r.sections.get("self_care", [])[:5], "care"), section("see_clinician", SEC["see_clinician"], r.sections.get("see_clinician", [])[:3])]
        sections = [s for s in sections if s["items"]]
        srcs, attrib = source_entries(packmod.load(), r.article)
        review = {"tier": r.tier, "label": r.label, "articles": [{"id": r.article.id, "title": r.article.title, "version": r.version}]}
    acts = [BOOK, SUMMARY] + ([concern_choices_action()] if found is None else [])
    card = new_card("education", title="What I can and can't do here", summary=summary, sections=sections, sources=srcs, attributions=attrib, review=review, limits=[GENERAL_NOTE],
                    context={"topic": ctx_topic, "concern": ctx_topic if ctx_topic in lexicon.CONCERN_TOPICS else concern, "analysis_id": analysis_id}, actions=acts,
                    follow_ups=[] if found is not None else [concern_choices()])
    if tri is not None and tri.escalated():
        card["escalation"] = escalation(tri)
    rep = finalize(card, status="abstained", abstained=True, reason="clinical_decision", route=route, analysis_id=analysis_id,
                   sources=[] if found is None else [{"kind": "derm_source", "source_id": s["id"], "publisher": s["publisher"], "title": s["title"], "url": s["url"]} for s in srcs[:4]])
    return rep


def concern_choices_action() -> dict:
    return act_message("describe", "Tell me what you are noticing", "I would like to describe a skin concern")


# ---------------------------------------------------------------- not covered / out of scope / general (H)
def no_library_answer(text: str, *, route: dict, reason: str = "no_evidence", mode: str = "") -> Reply:
    summary = ("I don't have reviewed information on that yet, and I would rather not guess. I can explain general skin and skincare topics, check the quality of a photo, "
               "or explain a medical report; or you can book an appointment so a dermatologist can answer it properly.")
    if mode == "off":
        summary = ("The clinic's reviewed skin library has not been published yet, so I can't answer general skin questions from it. You can still upload a report, take a photo for the clinic, "
                   "or book an appointment for a dermatologist's advice.")
    card = new_card("education", title="", summary=summary, actions=[BOOK, TAKE_PHOTO, UPLOAD_REPORT], follow_ups=[], limits=[], context={})
    return finalize(card, status="abstained", abstained=True, reason=reason, route=route)


def failed() -> Reply:
    """The honest answer when something broke inside the assistant: no guess, and the ways forward."""
    card = new_card("general", title="", summary="I couldn't answer that just now. Please try again in a moment. If it keeps happening, you can book an appointment or ask the clinic.",
                    actions=[BOOK], context={})
    return finalize(card, status="abstained", abstained=True, reason="error", route={"kind": "error"}, screened=False)


def unrelated(*, route: dict) -> Reply:
    card = new_card("general", title="", summary="That is outside what I can help with. I'm here for skin and skincare questions, your photos and reports, and the clinic's services and appointments.",
                    actions=starter_actions(), context={})
    return finalize(card, status="abstained", abstained=True, reason="out_of_scope", route=route)


INTRO = ("I can explain skin and skincare topics in plain language, check the quality of a photo and explain what it can and cannot show, "
         "explain a medical report you upload, and help you with the clinic's services and appointments. I cannot diagnose or choose medicines.")


def general(*, route: dict, greeting: bool) -> Reply:
    card = new_card("general", title="How I can help", summary="Hello. " + INTRO, actions=starter_actions(), context={})
    return finalize(card, route=route)


def starter_actions() -> list[dict]:
    return [act_message("s-routine", "Suggest a gentle skincare routine", "Suggest a gentle skincare routine"), act_message("s-sunscreen", "How do I choose a sunscreen?", "How do I choose and apply a sunscreen?"),
            act_message("s-acne", "I have pimples. What can help?", "I have pimples. What can help?"), TAKE_PHOTO, UPLOAD_REPORT, BOOK]


def starters(has_docs: bool, library: bool = True) -> list[dict]:
    """The suggestions shown before the first message. Each one sends a real question or opens a real screen. Questions the skin library answers are left out while it has nothing to show."""
    out = starter_actions() if library else [TAKE_PHOTO, UPLOAD_REPORT, BOOK]
    if has_docs:
        out.insert(3 if library else 1, act_message("s-report", "Explain my latest report", "Explain my latest report"))
    return out

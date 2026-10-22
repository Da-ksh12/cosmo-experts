# The assistant: how a patient's message is understood and answered

Audience: engineers and the clinic's technical lead. For the library itself see `KNOWLEDGE_LAYER.md`, for photos `PHOTO_ANALYSIS.md`, for appointments `BOOKING.md`, for the tool-using agent
`AI_AGENT_AND_TOOL_SECURITY.md`.

The assistant is a **skin-education, photo-quality, report-explanation and clinic-help guide**. It does not diagnose, does not choose medicines or doses, produces no finding, spot, mark or
score from a photo (no skin-analysis model is approved; see `PHOTO_ANALYSIS.md`), and never says an appointment is booked when it is only requested.

## 1. What was wrong (the trace, Phase 1)

| Question | Finding (before this work) |
|---|---|
| Which code answered a message? | `POST /api/conversations/{id}/messages` -> `assistant/engine.answer`: one retrieval over (1) the person's own document text, (2) clinician-**approved** knowledge-base articles, (3) the person's approved quotations. Anything else abstained. |
| Why did "what is on my face?" with a photo end in "I couldn't find anything in your documents or the clinic's approved information" + "Clinician review required"? | Free text about a photo had **no route**: only tapping a question on the photo card (a structured `context`) reached photo-aware answers. A typed question went to document retrieval, found nothing (there were no approved articles: all were drafts, and drafts are never used), and abstained. The status badge for an abstention was the generic `clinician_review_required`. A second, separate "things I can do" card on the same screen repeated the agent's answers. |
| Is an image-capable service configured? | The only image code is rule-based photo-quality checks (focus, light, size, framing, covering) and a classical OpenCV Haar face finder, used to locate the face area. It runs on the server; no image leaves the machine and no external image provider exists. |
| Which skin tasks exist, are they evaluated, approved? | The capability registry lists acne/skin-condition/lesion tasks. No model is approved for patients. The Phase 5 models are experimental research artefacts, disabled for patient-facing output. The face finder uses a skin-colour filter that was **not tested across skin tones**; this is stated to the person. |
| Can the system point at spots or marks? | Only at what it measured: the face area found by the detector and a covered/glare region found by the quality check. No lesion or mark localisation exists, so none is shown. |
| Booking, report and doctor-review APIs? | Report explanation (`/api/documents/{id}/explanation`), clinician review requests and care plans existed. **No booking API existed**; no calendar is connected today. |

Root cause: not wording. A single retrieval pipeline was the only door, an empty approved library made it abstain on everything skin-related, and photos had no free-text path.

## 2. The new flow

```
POST /api/conversations/{id}/messages            (patient, consent required, conversation must be theirs)
  -> routers/patient.ask       stores the message, calls the dispatcher, stores the answer + its card
     -> assistant/dispatch.respond
         1. context.derive          what the conversation already knows (photo, last topic, what the person said)
         2. router.classify         warning signs first (derm/triage), then A-H category
         3. clinic urgent policy    the clinic's own emergency wording, checked before everything
         4. a diagnosis / medicine request -> compose.declined  (says what can be said, never a bare refusal)
         5. handler by category
         6. compose.finalize        card -> text -> shared safety policy (ai/safety) -> Reply
```

| Cat. | Meaning | Handler | Needs a document? |
|---|---|---|---|
| A | about the photo in this conversation | `photo_chat.reply` (resolves the authorised photo, checks it is the person's, builds from the stored analysis; combines the library when a concern is named) | no |
| B | general skin / skincare education | `derm.reference.find` -> `compose.education` | **no** |
| C | a medical report | `records_chat.report` (explains from printed values; the agent's tools for lists and proposals) | the person's own |
| D | history / "what do you have on me" / a summary for the dermatologist | `records_chat.history`, `photo_chat.summary_for_dermatologist` | no |
| E | care plan | `records_chat.care_plan` (the clinician's own text) | no |
| F | booking, services, prices, contact | `records_chat.booking_reply` (the clinic's own configuration; never invented) | no |
| G | warning signs / emergency | `compose.urgent` (clinic policy message verbatim + the reason) | no |
| H | greeting or unrelated | `compose.general` / `compose.unrelated` | no |

The previous retrieval (`engine.answer`) is still used where it is the right tool: a question about a value in a report, a quotation, or a clinic article that is not part of the library.

### Context
Nothing is stored separately. Each turn the context is **derived from the conversation's messages**: the photo assessment in the conversation (re-read through `photo_analysis.store`, which checks
the owner), the context saved in the previous answer card (route, topic, concern), and the person's own earlier messages for what they said about the concern in focus (how long, which symptoms,
which products). Facts about a concern belong to that concern: when the conversation moves to a different concern (`derm/lexicon.EPISODE_FAMILY`) they start again, so "4 weeks" said about
pimples is never applied to dry hands. Facts about the person (earlier reactions, pregnancy, a child) stay. Deleting a conversation deletes its context.

## 3. Answer cards (`assistant-card/1`)

Each answer is a plain-data card built by `assistant/compose.py` and stored with the message in `messages.sources` as `{"kind":"card","card":{...}}` (no schema change). The screen only draws it.

| Field | Meaning |
|---|---|
| `route`, `context` | category and what the next turn needs (topic, concern, analysis id, document id) |
| `title`, `summary`, `sections[]` | the answer: sections have `id`, `title`, `items`, `tone` (info / care / avoid / urgent) |
| `escalation` | present **only** when the warning-sign rules raised a level: `level` (emergency / urgent / prompt), `title`, `reason`, `message`, `rules`, `sources`, `actions` |
| `noted` | what the person said that shaped the answer, in neutral words |
| `terms` | plain meaning of terms (when the card explains what something is) |
| `follow_ups[]` | questions that would make the next answer more careful, each with real choices (a choice is a sentence the person could have typed); at most two, never about what they already said |
| `actions[]` | `message` (send a question), `navigate` (open a screen), `external` (https / tel), `report` (open the report), `confirm` (a change the agent proposed: nothing happens until the person presses Yes) |
| `review` | the honest label: clinic-approved library text, "not reviewed by the clinic's dermatologist yet", the person's own record, the clinician's own plan, the clinic's own details |
| `sources[]`, `attributions[]`, `limits[]` | publisher, title, link and the date the page showed; the licence notices the sources require; one line saying it is general information |
| `report` | for a report: each value as printed, with unit, **printed** range, status (`outside` only when the report printed a range or flag; otherwise `unknown`: "No range printed") and page |

`render_text(card)` produces the plain text (read aloud, searched, shown by an older screen). Text passes the shared policy `ai/safety.review_text` (`skincare_guidance` kind: no diagnosis, no medicine
change, no prescription-only drug, no dose or strength, no certainty claim, no emergency wording except the clinic's). A photo card is screened as `image_assessment` (a model's calibrated
percentage is not a dose). Cards made of the person's own records or the clinic's own words are not screened as guidance, by design: a report's "Hemoglobin 10 g/dL" would be taken for a dose.
If a card is ever withheld by the policy, the person gets the standard message plus the way to book. If anything inside the assistant raises, `compose.failed()` answers honestly instead of a stack trace.

## 4. Warning signs and escalation (`derm/triage.py`)

Levels: **emergency** (the clinic's urgent-symptom policy message, verbatim, is the answer; the reason is a separate field shown beneath it), **urgent** (same-day: reason, the next step, a booking
shortcut marked same-day, and the relevant "signs that need prompt help" from the library), **prompt** (worth booking soon, with the reason), **routine** (nothing is raised). 22 phrase rules
(`triage.RULES`) and one duration rule (`P-persistent-rash`: a rash, itch, patch, redness or dryness the person says has lasted three weeks; acne has its own rule at eight weeks) read the
person's own words, all 23 listed for the clinicians; a negated mention ("no fever") does not count; an earlier message can complete a sign described across two messages but cannot raise the same alarm again
on its own. Escalation is never the default fallback: an ordinary question raises nothing, and "Clinician review required" is no longer a generic abstention label. All rules are
`pending_clinician_review` (`DERM_REVIEW_PACKET.md`, Part B); a non-match is never evidence that a situation is safe.

## 5. Photos

See `PHOTO_ANALYSIS.md`. In short: the photo path resolves the authorised photo of the conversation (or the one the person tapped), never reads another person's, runs nothing new, and answers from
the stored analysis (what the checks measured, what the photo cannot show, whether a clinician should look and why) combined with the library once the person says what they are noticing. After
a photo the screen asks "What are you noticing?" with real choices; the answer is the library's general guidance and says that it is **not a finding from the photo**.

## 6. Security and privacy

* The conversation must belong to the signed-in patient (404 otherwise); an `analysis_id` sent by the client is checked with `photo_analysis.load(owner, ...)` before anything is stored or answered.
* Cards carry no storage address or credential: the only URLs are public publisher pages, in-app routes and the clinic's own https booking page or `tel:` number.
* No patient text or image is sent to an external service; no new provider was added. The assistant needs no model service at all.
* The audit trail records that a question was asked and how it was routed (`assistant.ask`: abstained, reason, route, triage level) **without the text**; booking records what was filled in, not the values.
* Appointment requests are patient-owned rows protected by row-level security (migration `0010`), included in retention/erasure (`appointment_requests` category) and in `delete-my-data`.
* A photo summary reaches the clinic only if the patient ticks the box on one request; the clinic opens it through one audited endpoint and sees nothing else of the photos.
* Rate limits and consent apply as to every patient endpoint (`COSMO_MODE=production` enforces consent over HTTP).

## 7. Configuration

| Variable | Effect |
|---|---|
| `COSMO_KB_REFERENCE=labelled|off` | show the unreviewed library with its label (default **labelled** in development, **off** when `COSMO_MODE=production`) |
| `COSMO_CONTENT_DIR` | where the clinical content (`derm/`, policy files) lives |
| Clinic details (workspace form) | stored in `settings` (`clinic_profile`): name, phone, WhatsApp, e-mail, address, hours, an optional https booking page, a note |

## 8. What is tested, and what is not

* `tests/test_derm_knowledge.py` (library, review tiers, rules, slots), `test_assistant_router.py` (every category and the edge cases), `test_assistant_dispatch.py` (the endpoint end to end: education with
  no document, photo questions, reports, escalation, declines, the production default), `test_booking.py`, PostgreSQL row-level-security cases in `test_pg_rls.py`, and `e2e/assistant-guide.mjs` (a real
  browser: education, follow-ups, banners, a report with a confirm-before-write proposal, booking and the clinic's answer, and with a face photo you supply the whole photo journey).
* Tests that use a **controlled test double** (an analysis built by hand to exercise a code path no real model reaches today) are marked as such: they prove rendering and rules, not what any model can see.
* Retrieval quality over the library was measured on a small written question set (`docs/eval/derm_retrieval.json`, evidence class `synthetic_fixture`): it says how the mechanism behaves, nothing about
  clinical content or real patient wording.

## 9. Known limits

* The library text is not yet approved by the clinic's dermatologist (see `clinical_review/DERM_REVIEW_PACKET.md`). Until then it is shown only with its label, and not at all in production by default.
* Routing and topic detection are keyword/rule based (English, Hinglish, Hindi words); a message in other words may be answered as "outside what I can help with" or find no library answer. That is reported honestly,
  never guessed. Answers are in English.
* No calendar is connected: availability is never shown and an appointment is never confirmed by the software.
* Nothing here was run on a phone or an emulator.

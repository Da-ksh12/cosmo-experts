# Photo assessment: what the system measures, what it does not, and the contract

Audience: engineers (backend and app) and the clinic's medical lead. Code: `backend/app/photo_analysis/` (schema, builder, service, store, answers), `backend/app/routers/photo_analysis.py`,
`backend/app/assistant/photo_chat.py`; app: `frontend/lib/photoAnalysis.ts`, `frontend/components/PhotoAnalysisCard.tsx`, `ImageViewer.tsx`.

## 1. In one paragraph

A person takes or chooses 1 to 4 photos of one area. The server checks each photo **as a photograph** (focus, light, size, framing, covering, glare, compression, and - for a face - whether exactly one face
is found and how large it is) and returns a typed assessment: what was measured with the value and the threshold it was compared with, where on the photo (only for what was really located: the face area, a
covered or glared region), what the photo and these checks **cannot** tell, whether a clinician should look and why, and what the person could say to make the next answer more useful. The same
assessment is the context of the conversation: the person can ask about it in their own words, say what they are noticing, get the library's general guidance for that, prepare a summary for a dermatologist, or
book. **No skin finding is produced**: nothing says what a spot, mark, redness or texture is, nothing is counted, scored or outlined as a lesion.

## 2. What exists and what is missing (the capability record)

| Capability | State | Notes |
|---|---|---|
| Photo-quality checks (`image.quality`: `suitability`, `quality-v2`) | **available**, rule based | Some thresholds were validated on skin close-ups; the face rules were **not validated on real face photographs**. Advisory unless an observation says it is validated |
| Face finder (`face.detect`, OpenCV Haar cascade) | available, rule based | Used only to locate the face area for the checks. Uses a skin-colour filter that **was not tested across skin tones**: it may miss a face more often on darker skin. When a face is "not found" the person is told the check may be wrong and can ask the clinic to look instead |
| Acne-like features, pigmentation, redness/irritation, dryness/scaling, texture/scarring, hair thinning, scalp findings, other concern | **not available** | No approved model exists. Listed under `capabilities` in every assessment as not available; nothing is produced |
| Lesion, dermoscopy and common-condition classifiers | **research only** | Phase 5 experimental models (`docs/PHASE_5_TRAINING_REPORT.md`). They are disabled for patient-facing output and are never run for a patient |
| Localisation of spots, marks or lesions | **missing** | No model supports it. The viewer draws only geometry the backend measured; it draws none for skin features |
| Skin-tone-stratified evaluation, calibrated confidence | **missing** | A confidence appears only when a calibrated score exists; the rule-based checks have none and say so |

What would unblock a real skin analysis: a dataset with clinician labels and licences that allow the use, a model trained and evaluated at patient level and stratified by skin tone, calibration, a clinician
agreement study, and an approval record in the model registry (`docs/MODEL_EVALUATION_AND_RELEASE.md`). Until a model is approved, `skin_feature` observations cannot exist: the schema refuses guidance
that claims findings without `basis = model_findings`, and the builder only reads findings from a model the registry reports as approved for patients.

## 3. The API (patients only, their own sessions only)

| Method and path | What |
|---|---|
| `POST /api/v1/photo-analyses` | multipart: `region` (`face`, `hair_scalp`, `body`, `other`), `files` (1-4), optional `conversation_id`; optional `Idempotency-Key` header. Creates one session per photo, recorded in a conversation. 202 (200 on an idempotent replay). Consent required |
| `GET /api/v1/photo-analyses/{id}` | the session's real, current state |
| `POST /api/v1/photo-analyses/{id}/cancel` | stop a session that has not finished; a photo already stored is taken back out |

Another person's id answers 404, exactly like an unknown id. Photos are served separately by `GET /api/images/{id}/file` under the same rule. A photo that **fails the check is not stored** (data
minimisation); the app shows its own copy while the person is on that screen.

## 4. The contract (`photo-analysis/1`, contract minor 1)

Typed with Pydantic (`schema.py`, unknown fields refused) and mirrored in `frontend/lib/photoAnalysis.ts`. The app validates geometry again and drops (never repairs) any that fails.

* `api_version` = `photo-analysis/1`; `contract_minor`: 0 = no guidance block, 1 = guidance (**additive**: an older reader ignores it).
* `id`, `conversation_id`, `message_id`, `index`, `status` (`queued`, `processing`, `completed`, `uncertain`, `rejected`, `failed`, `cancelled`), `stage` while processing (only stages that really happen), `error`.
* `image`: `id`, `kept`, `url`/`thumb_url` (API paths that need the patient's own sign-in; never a storage address), `width`, `height`, `region`, `taken_at`.
* `transform`: the size of the stored photo **as displayed** and its EXIF orientation (already applied); `server_modified` is always false (the server never crops, rotates or resizes the stored photo).
* **Coordinates**: fractions (0 to 1) of that displayed size, origin top-left, y down. Geometry is a `point`, `box` or `polygon` (3 to 64 points). Every shape is validated: finite numbers in range, boxes with a positive size inside the photo.
* `outcome`: route, verdict (`accepted`/`uncertain`/`rejected`), the check's reason key, headline and message, and whether a clinician reviews it.
* `provenance[]`: for each source of an observation, the capability, version, kind (`rule_based`/`model`), approval and whether its thresholds were validated, with a plain note.
* `observations[]`: `kind` (`photo_quality`, `framing`, `face_region`, `skin_feature`), `group` (`observe` or `area`), `label`, `tone` (`ok`, `attention`, `info`), `summary`, `evidence[]` (text, metric, value, threshold), `limitations`, optional
  `confidence` (`value`, `calibrated`, `basis`), optional `geometry`, `provenance` index, `validated`. **`skin_feature` can exist only from an approved model - none exists today.**
* `sections`: `observe` and `areas` (observation ids), `meaning` (cautious statements), `cannot` (what the photo and these checks cannot establish: it always includes that a photo cannot show vitamin levels, hormones or internal illness, and that colour-based checks can look different on darker skin and were not tested across skin tones), `next_steps`, `questions` (only ones this assessment can answer).
* `capabilities[]`: each skin task, whether it is available, its status and a note (see section 2).
* `clinician_review`: `pending`, `reviewed` or `not_applicable` (the photo was not kept), with a message.
* **`guidance` (contract 1.1)**:
  * `basis`: `photo_quality_only` (today always) or `model_findings`;
  * `visible`: ids of what the checks measured **in the photo**;
  * `may_explain`: what a visible feature of the skin may relate to - filled only from an approved model's findings, empty otherwise;
  * `cannot_establish`: what the photo and these checks cannot tell;
  * `history`: what the person could tell a clinician (or the assistant) - how long, itching/pain/swelling/bleeding, products in use, earlier reactions;
  * `clinician_advice`: `recommended`, `level` (`routine`/`prompt`/`urgent`/none) and a reason. It is **never a reassurance**: "not recommended by this check" is not "nothing is wrong". For an accepted photo it says the check cannot tell whether anything needs attention and names the signs that should be looked at (painful, spreading, bleeding, changing, lasting more than a few weeks);
  * `knowledge[]`: library topics the person can open from the assessment (what a photo can show; when to see a doctor) with the question the app sends.
* `disclaimers[]`: the standing "not a diagnosis" and review notes.

Everything in an assessment comes from something the backend measured or resolved. Nothing is filled in to look complete.

## 5. In the conversation

* The assessment is recorded as a message with one source per photo; the screen draws the interactive card (photo viewer with real geometry only, what was observed, guidance, next steps, questions).
* **Free text works.** A typed question about the photo is routed to `photo_chat` (not to document retrieval): the authorised photo is resolved from the conversation (or from the area the person tapped, checked with `photo_analysis.load(owner, ...)`),
  and the answer is built from the stored analysis. Questions the checks can answer (what was measured, a better photo, what happens next, comparing with an earlier photo) are answered by `photo_analysis/answers.py`.
* A question asking the assistant to *find, name or point at* something ("find the acne", "what is this spot?") gets a plain statement that it cannot identify spots, marks, redness or conditions because no approved skin-analysis tool exists, what it *can* say about the photo, and the next step.
* After a photo the assistant asks "What are you noticing?" with real choices. The reply is the library's general guidance for that concern, labelled *not a finding from your photo*, with follow-up questions (how long, symptoms, products, earlier reactions) and actions.
* "Prepare a summary for my dermatologist" combines the photo check with what the person said (labelled "What you told me") and states that it is not a diagnosis.
* Photo answers pass the image-assessment policy (a model's calibrated percentage is not a dose; no diagnosis; no certainty claim). An unusable photo gets retake advice and, if a concern is named, the library's guidance.
* Booking from a conversation that has a photo opens the booking screen with that assessment offered. Sharing the summary with the request is an explicit, off-by-default choice (`BOOKING.md`).

## 6. Privacy and safety

* The photo stays on the server (no external image provider, no model is called for a patient's skin). Cancel removes it; a failed check never stores it; a deleted photo is no longer served.
* The assessment contains no storage path or credential. While an upload is on its way (or has failed, so it can be retried or retaken) the app holds the photo in memory on the phone; the dismissal of a failed upload releases it.
* Every statement that could be read as a finding is either measured (with its threshold) or absent. Warning signs the person *types* are handled by the triage rules like any other message.

## 7. Tests

`backend/tests/test_photo_analysis.py` (contract, geometry validation, orientation, rejected and uncertain photos, cancel, idempotency, ownership, no skin finding, guidance block) and
`backend/tests/test_assistant_dispatch.py` (photo questions in the conversation). Tests marked CONTROLLED TEST DOUBLE hand-build an analysis or a model result to exercise a code path no real model reaches
today; they prove rendering and rules and **nothing about what any model can see**. `e2e/assistant-guide.mjs` runs the whole journey in a real browser when you supply a face photo you may use
(`COSMO_E2E_FACE`); none is stored in the repository. Nothing here was run on a phone or an emulator.

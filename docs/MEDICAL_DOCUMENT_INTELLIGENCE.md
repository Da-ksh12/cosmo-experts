# Medical document intelligence (Phase 3)

What happens to a report or prescription a patient uploads: how it is read, what is extracted, how it is explained, what is retrieved from it, and what the system refuses to do. Code:
`backend/app/documents/`, `backend/app/ai/{citations,injection,knowledge,provenance}.py`, `backend/app/services/explanations.py`, `backend/app/assistant/`. Tests are listed in section 9.

**Everything here is a draft for clinician review.** No part of it has been evaluated on real patients' documents (none were available); every figure below comes from synthetic fixtures the project
wrote. Nothing is clinically validated, clinically approved or production-ready.

## 1. The pipeline (`documents/pipeline.py`, `docpipe-2.0.0`)

validate -> extract text (embedded text, or OCR for scans and photos) -> segment -> classify -> extract fields with source traceability. Everything runs locally; nothing leaves the machine.

* **Hostile files first** (`documents/safety.py`): active PDF content, embedded files, zip and XML bombs and absurd page or image sizes are rejected before any text extraction or OCR. This is parser
  hardening and a content-type policy, not malware scanning (see STATUS_REPORT.md, section 0, for the scanner position). Accepted types: PDF, JPEG, PNG, plain text, DOCX.
* **OCR** is bounded (one page may not run longer than 40 s). Languages come from `COSMO_OCR_LANGS` intersected with the Tesseract packs that are *installed*: a requested pack that is missing is
  reported, never silently replaced. **Hindi OCR is experimental and the Hindi pack is not installed on this machine**, so Hindi documents have not been read by this system.
* **Document types**: laboratory report, prescription, histopathology report, consultation note, treatment history, other/uncertain. A classification is a guess shown with its basis, not a diagnosis.
* **Rules the extractor keeps**: the exact source value and unit are preserved and never converted; reference ranges are only those printed in the source; "outside range" is computed only against a
  printed range and labelled as such; nothing without a source page and text is ever emitted as an extracted fact.

### Provenance on every item

Each extracted lab value carries: the page, the line text, character spans (line, value, unit, printed range), the box on the page for scans, how the text was obtained (embedded or OCR, with the OCR
confidence), the extractor version (`docpipe-2.0.0`), a normalised reading *beside* (never instead of) the printed text, a list of stated ambiguities, and a review status
(`unreviewed | confirmed | corrected | rejected`). A clinician's correction of a value is stored with the before and after values and the reviewer (`field_corrections`, audited);
a corrected medicine component keeps the original reading, drops its source position (it no longer points at the page) and says who corrected it.

## 2. Header facts (`documents/structured.py`)

Typed facts the document itself prints: report, collection and document dates, the laboratory or provider, referring or reporting clinician names, an explicit allergy statement, an explicit follow-up
instruction.

* A fact exists **only when the document says it.** No allergy line is *not* "no allergies"; no follow-up wording is *not* "no follow-up".
* A date whose day/month order cannot be known (`12/03/2024`) is **not** resolved: both readings are recorded and the ISO form is left empty. Two-digit years are not guessed.
* Clinician names are copied whole as printed, including initials without a full stop ("Dr. R K Sharma"); no credential is verified. A word that is not a name - a job title ("Consultant Dermatologist"),
  "Self", a bare "Dr" or "MD" - is not recorded as a clinician. (The browser run of this phase showed a first version recording "Dr. A Example, MD" as just "Dr"; fixed, with tests.)
* **The patient's own identifiers (name, age, ids, phone) are deliberately not extracted.** No feature needs them; not collecting them is data minimisation.

## 3. Medicines on prescriptions (`documents/medication.py`, `medparse-1.1.1`)

A rule-based parser that finds lines that look like a medicine entry and splits them into what the document prints: name, form, strength(s), dose, a pattern such as `1-0-1`, route, frequency,
duration, instructions, and anything it could not place. Every component keeps its original text and position.

It never recommends, starts, stops, changes or adjusts anything; never substitutes, translates or "corrects" a name (no brand/generic table exists; a name in brackets is recorded as printed, not asserted
equal); never fills a missing component; never converts units or chooses between readings. Ambiguous shorthand (a `5/7` duration, a `1-0-1` slot pattern, `OD` beside an eye medicine, a bare number after
a name) keeps its raw text, gets a stated ambiguity code and no (or an explicitly assumed) normalisation. Shorthand expansion (`BD` -> "twice daily") uses a fixed lexicon and is recorded as an expansion.
Every entry is `needs_review`. A doctor can confirm, reject or correct an entry (`POST /api/clinic/medications/{id}/review`); the original reading is kept.

**A defect found by re-running the extraction evaluation, and fixed (`medparse-1.1.0` -> `1.1.1`).** `serum` is both a skincare form and the first word of many laboratory test names. Version 1.1.0 took any line
that opened with it for a medicine entry, so in a laboratory report every row such as "Serum Iron", "Serum Creatinine" or "Serum Sodium" became a "medicine" and was **dropped from the results**
(the first re-run of the evaluation below read 0.955 of the digital rows instead of 1.00: all six misses were Serum Iron). Nothing had measured it because the fixtures had no such row. Now a line that opens with
`serum` is a medicine entry only if it also says something about *use* (a strength, dose, frequency, duration or how to apply it) and prints no laboratory unit such as mg/dL or ng/mL. Regression tests
(`tests/test_medication.py`: laboratory rows stay results, both at parser level and through the whole pipeline; a skincare serum with directions is still a medicine) and four laboratory rows in the
development fixtures guard it. One boundary is deliberately unchanged: in a document *classified as a prescription*, an unprefixed line with a concentration and the abbreviation "HS" (as in "Serum HS-CRP 1.2 mg/L")
is still read as an entry by the general rule.

**Evidence (synthetic fixtures only, `docs/eval/medication_parser.json`):** 34 development lines and 26 held-out lines written by the project. The held-out set was written after tuning on the
development set, and **the first run on it found problems that were then fixed**: first-run component precision 0.992, recall 0.939 (8 missed components, 1 spurious, one whole entry
`T. Zincovit 1 tab OD after lunch` not detected). After the fixes the held-out set scores precision 1.0 and recall 1.0 with 0 of 42 must-stay-empty components filled; because the fixes were made on
those same lines this is **not an independent estimate**. The numbers say the rules do what they were written to do; they say nothing about accuracy on real prescriptions, handwriting or other layouts.

## 4. Explanations (`documents/explain.py`, `explain-2.0.0`; built by `services/explanations.py`)

Template-based on purpose: every sentence is a fixed safe phrase or a direct statement of what the document says, with its page and a citation to the exact line. No clinical interpretation is invented.
`GET /api/documents/{id}/explanation?lang=en|hi|hinglish` and the engine capability `document.explain` call the same service, so access rule, audit record, untrusted-text handling and safety screening are
identical.

**Section contract** (pinned by a test; the screens find sections by `id`, never by position): `found`, `uncertain`, `questions`, then, when present, `facts` (dates and details printed on the document),
`medicines` (entries as printed) and `background` (clinic-approved passages with citations; English only). Each section has an `id`, a `title`, a `summary` and `items`; an item carries its `citation`.

* **Every statement is checked against its citation** before it is shown (`ai/citations.py`): the cited line must contain the test name, number, unit, printed range and printed flag the sentence
  states, or carry a clinician's correction. A statement that fails is left out, counted, and the count is shown ("N statement(s) could not be matched ... and were left out").
* **Abstention**: when nothing structured could be read, the explanation says so (`abstained`) and sends the person to the clinic. It does not fill the gap.
* **Contradictions** between two documents' printed values for the same test and date are reported, not resolved.
* **Instruction-like text** in a document (section 6) is left out and counted; the person is told something in the document looked like an instruction to software.
* The final text is reviewed by the shared safety policy (`ai/safety.py`); sentences that are verbatim quotations of the document are allowed and labelled as such.
* **Languages**: English templates are the only reviewed wording by the project's author. Hindi and Hinglish templates exist, carry every number, unit, date and printed text of the English sentence
  (a test compares them), and are labelled **unreviewed** (`translation_reviewed: false`). See [VOICE_AND_MULTILINGUAL.md](VOICE_AND_MULTILINGUAL.md).
* **Spoken text** is generated from the same statements (`speech/spoken.py`) and checked sentence by sentence against what is shown.

## 5. Grounded retrieval (`ai/knowledge.py`, `assistant/`)

Questions are answered from the patient's own documents and from **clinician-approved** knowledge only. Draft articles shipped with the software are never searched; an article is searched only while it is
approved, unexpired and byte-identical to what the reviewer approved.

* **Passages**, not whole articles (about 240 characters, sentences packed): a hit carries the passage, its span, the article id/title/version, who reviewed it and when, when the review lapses and the
  content hash.
* **Abstention threshold per retriever configuration**, chosen on development queries only and recorded in `docs/eval/kb_retrieval.json` (a test checks the constants equal the record). On the held-out
  queries the dense setting answered 29 of 33 in-scope questions with a supporting passage and wrongly answered 1 of 30 out-of-scope questions (95% interval 0.6-16.7%). The two query sets are small, written by one
  author, and the corpus is unreviewed draft text used for evaluation only: **this says the abstention mechanism works as built; it says nothing about the clinical content of any article.**
* **Answers are extractive**: the words of the source in quotation marks, labelled "Your document ..." or "Clinic-approved information ...", then a fixed statement that this is not a diagnosis. The language-model
  answer mode is off: Qwen2.5-1.5B fails the answer gates (MODEL_EVALUATION_AND_RELEASE.md) and nothing replaces it silently. If a model ever drafts text, each claim must quote its source and numbers must match;
  a claim that does not is dropped.
* A query in a script the corpus is not written in is declined rather than matched on noise.

## 6. Prompt-injection defence (`ai/injection.py`)

Documents are untrusted: a report can say "ignore your instructions". The layers that protect the person are structural and hold even if a phrase is missed: retrieved text only travels as **data** (quoted in an
extractive answer, or inside a keyed, delimited block of a model prompt that says it is data); a model's claims are verified against the cited text; the output policy withholds diagnoses and medicine changes
whoever asked; permissions are checked on the server from the signed-in identity, never from anything a document says. The detector adds visibility: text that addresses an AI system is flagged (severity high, medium
or low; one high or two medium findings quarantine a passage), kept out of answers and prompts, and reported. It handles hidden characters and look-alike letters. It targets instructions to *software*, not the
ordinary imperatives of medicine ("take one tablet twice daily"). The attack set used to measure it is hand-written, so its detection rate is a regression check, not a guarantee.

## 7. Clinician review

`POST /api/documents/{id}/request-review` (patient) asks the clinic team to look at a document; the assistant's confirmed action of the same name calls the same function. Doctors review documents
(`/api/clinic/documents/{id}/review`), flag or correct fields, and confirm, reject or correct medicine entries. Reviewed entries are shown as reviewed; nothing is shown as clinically verified unless a
clinician did it.

## 8. Extraction evidence

`eval/doc_extraction_eval.py` -> `docs/eval/doc_extraction.json`, run with `docpipe-2.0.0` on 2026-10-19 (seed 7): 16 synthetic laboratory reports (6-11 of 24 common analytes each; half laid out in columns, half inline) with exact ground truth,
133 result rows per condition, rendered as digital PDFs and as scans degraded in seven ways, read with Tesseract 5.4.0 (English). "Exact" means name, value, unit and printed range all equal the truth.

| Condition | Rows read exactly (recall) | Of rows emitted, exact (precision) | False-positive rows | Printed flag right on exact rows |
|---|---|---|---|---|
| digital PDF | 1.000 | 1.000 | 0 | 1.000 |
| scan, clean | 0.910 | 0.976 | 3 | 1.000 |
| scan, rotated 90 / 180 / 270 degrees | 0.887 / 0.902 / 0.895 | 0.975 / 0.976 / 0.967 | 3 / 3 / 3 | 1.000 |
| scan, blurred | 0.947 | 0.977 | 3 | 1.000 |
| scan, noise and heavy JPEG | 0.887 | 0.975 | 3 | 1.000 |
| scan, low resolution (100 dpi) | 0.925 | 0.969 | 4 | 1.000 |

No crashes in 128 documents; every document was classified as a laboratory report; reading a scan takes about 6-8 s on this CPU (a digital PDF about 0 s).

**This is the same as the `docpipe-1.0.0` record on every accuracy field, and that took a fix.** The first re-run on `docpipe-2.0.0` read only 0.955 of the digital rows and 0.84-0.90 of the scanned rows: the medication parser of
section 3 was taking "Serum Iron" rows for skincare serums. That record was kept out of the repository and replaced by the run above after the fix. The lesson recorded here: a change to one extractor must re-run the evaluation of
the others that share a document.

What this does and does not show. The reports are rendered by the project from a fixed catalogue of analytes with plain layouts; the scans are those reports rasterised and degraded by code. Nothing is a real laboratory's
layout, stamp, handwriting, table, multi-column page or phone photograph, and no real patient document was read. The evaluation scores only the result rows; header facts and medicine parsing are measured by their own
fixtures and tests. Treat these figures as evidence that the mechanism works on clean, simple inputs, not as an accuracy to expect from real reports.

## 9. What is not done, and where the tests are

* **Not done**: no real patient documents were used; no handwriting; table and multi-column layout beyond the synthetic reports; Hindi OCR (pack not installed); no brand/generic medicine mapping (by design);
  no drug-interaction or dose checking (by design: the system does not advise on medicines); no clinical interpretation of a value (by design).
* **Tests**: `test_documents_structured.py`, `test_medication.py`, `test_ai_explain.py`, `test_ai_knowledge.py`, `test_ai_injection.py`, `test_grounded.py`, `test_assistant_grounding.py`, `test_upload_safety.py`,
  `test_agent.py` (document tools), `test_authz_idor.py` (document access). Evaluation runners: `eval/doc_extraction_eval.py`, `eval/medication_eval.py`, `eval/kb_retrieval_eval.py`.

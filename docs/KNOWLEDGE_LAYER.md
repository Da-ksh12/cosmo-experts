# The dermatology and skincare knowledge layer

Audience: engineers and the clinic's medical lead. Code: `backend/app/derm/` (pack, lexicon, reference, triage). Content: `backend/content/derm/`. Review: `docs/clinical_review/DERM_REVIEW_PACKET.md`.

The layer is a **versioned, source-traceable library of plain-language skin and skincare information**. It is how the assistant answers "what helps pimples?", "how do I choose a sunscreen?" or
"what does a gentle routine look like?" without needing any uploaded document. It is *education*: it never diagnoses, never chooses a medicine, dose or strength, and says what it cannot know.

## 1. What is in it

| Part | Where | What |
|---|---|---|
| Sources registry | `content/derm/sources.json` | 28 pages the articles rest on: publisher, title, https URL, the date the page itself showed (`page_reviewed`), its own next-review date, the date a person opened it (`accessed`), `source_review_overdue`, and the reuse terms that were checked (`terms.basis`, link, date) |
| Articles | `content/derm/articles/*.json` | 17 articles: acne, marks and scars after acne, pigmentation, redness and irritation, rosacea, dry skin and the barrier, eczema, reactions to products, sun protection, routine basics, ingredients, habits that worsen irritation, keeping track of a concern, when to see a clinician, warning signs, moles and spots, and what a photo can and cannot show |
| Rules | `app/derm/triage.py` | the warning-sign rules (see `ASSISTANT_ARCHITECTURE.md` section 4) |

Each article carries: `id` (`derm.*`), `version`, `language`, `topic`, `title`, `audience`, `keywords`, a `summary`, `sections` (what it is, what may contribute, gentle self-care, what to avoid,
how to keep track, when to see a clinician, signs that need prompt help, what a photo cannot show), plain meanings of `terms`, `contraindications` (shown with self-care advice), `limitations`,
`evidence_type`, `source_ids`, `claims_to_verify` (the statements a clinician must confirm), a `review` record and `authored` (who wrote it and when).

### Sources and licences
Counts today: 12 NHS (England) pages (Open Government Licence v3.0), 6 US-government pages (MedlinePlus, NIAMS, CDC, FDA: public domain), and 10 American Academy of Dermatology pages (copyrighted:
terms forbid copying or ingesting the text). The articles are **original wording**; sources are summarised, never copied, and no images are used.

| `terms.basis` | What the system does |
|---|---|
| `ogl_v3` | attribution notice is attached to the answer: "Contains public sector information licensed under the Open Government Licence v3.0." Adapted wording is not presented as the NHS's own |
| `us_gov_public_domain` | MedlinePlus credit is attached: "Source: MedlinePlus, National Library of Medicine." |
| `restricted_facts_only` | **no wording is taken**: widely held facts are restated in original words, the link is given for the reader, and each such statement is also on the clinician's list to verify. If the clinic wants closer wording it must ask the owner for permission |

Nothing was scraped. Each page was opened and read by a person on the `accessed` date; a page that could not be opened is not listed. The AAD terms page itself could not be opened (the summary came
from a search result) - the registry says so. `source_review_overdue: true` on 3 sources means the page's own review date had passed when it was read; they are flagged in the clinic panel for a re-check.

## 2. Guarantees the loader enforces (`pack.load`)

An article that names a source not in the registry, has no limitations, no claims to verify, an unknown topic/section, an over-long statement, or a review block that claims more than the record
supports **does not load** - and a pack that does not load is never served (the assistant then says the library is unavailable). The pack itself can only ever carry
`pending_clinician_review`; it cannot name a reviewer. Every statement also passes the shared safety policy in a test (`skincare_guidance`: no diagnosis, no medicine change, no prescription-only
drug, no dose or strength, no certainty claim, no emergency wording).

## 3. Review, tiers and what a patient sees

`pack.seed_drafts()` inserts each article into the clinic's review table (`kb_articles`) as a **draft** (`content_origin = ai_draft`, with its claims to verify and source note) using the
deterministic text from `pack.render_body()`. It never overwrites a clinician's edit or approval. Approval is the existing workflow: a signed-in doctor reads the exact text and the statements to
verify, names the source checked, ticks the attestation; the system stores the SHA-256 of the exact title and body and a re-review date. An edit returns the article to draft.

`reference.resolve_all()` decides, on every request, what may be shown:

| Tier | When | Label shown with the answer |
|---|---|---|
| `approved` | the row is approved, not past its re-review date, and its hash matches the stored text | "Reviewed and approved by the clinic." If the doctor rewrote the text, **their** text is what is shown |
| `reference` | not approved (or lapsed), and the reference mode is `labelled` | "General information based on the sources listed below. The clinic's dermatologist has not reviewed this wording yet." |
| not shown | not approved and the mode is `off` (the default under `COSMO_MODE=production`) | the person is told plainly that the clinic's reviewed skin library has not been published yet; photos, reports and booking still work |

`COSMO_KB_REFERENCE=labelled|off` overrides the default either way. The clinic sees all of this in the workspace panel "Dermatology library and warning-sign rules" (`GET /api/clinic/derm-library`).

## 4. Finding the right article (`reference.find`)

1. **By topic keywords** (English, Hinglish, Hindi). A concern (acne, pigmentation, ...) is the subject when the message names one; a general topic (routine, sunscreen, what to avoid) is added as a second article. When the
   message names only a general topic and the conversation has established a concern, the concern article tailors the answer.
2. **By retrieval**, only when no keyword identified a topic: the best passage over the pack, accepted only above a threshold measured on a written question set, and only with the dense or hybrid retriever.
   Lexical matching alone is **not** used as a fallback (its answers were wrong too often on the held-out set).
3. **By context**, for a short follow-up that names nothing: the concern the conversation established.
4. Otherwise nothing: the assistant says it has no reviewed information on that yet, and offers the next useful actions (book, photo, report). It does not guess.

### Measured retrieval quality (`eval/derm_retrieval_eval.py` -> `docs/eval/derm_retrieval.json`)
Evidence class `synthetic_fixture`: 70 hand-written questions (35 in scope, 35 out of scope, half development and half held-out; the development half chooses the abstention threshold, the held-out half is scored once).
It describes how the mechanism behaves on a small written set and says **nothing** about the clinical content of any article or about real patient wording.

| Retriever | Threshold | Development: correct answers / wrongly answered out-of-scope | Held-out: correct answers / wrongly answered | AUROC (held-out) |
|---|---|---|---|---|
| lexical | 0.2601 | 48.6% / 5% | 37.1% / 15% | 0.836 (not used) |
| dense | 0.6917 | 71.4% / 5% | 80.0% / 0% | 0.987 |
| hybrid (0.5) | 0.4412 | 68.6% / 5% | 71.4% / 10% | 0.944 |

`REF_THRESHOLDS` in `reference.py` equal these recorded values (a test fails if they drift) and a second test fails when the pack changes without the evaluation being re-run
(`python eval/derm_retrieval_eval.py --out ../docs/eval`, with `COSMO_EMBED_MODEL_DIR` set).

## 5. How to change the library

* **Edit or add an article**: change the JSON under `content/derm/articles/`, keep `claims_to_verify`, bump `version`, run the library tests and the retrieval evaluation, regenerate the review
  packet (`python -m scripts.export_derm_review`). A changed article text means a new hash: any earlier approval no longer matches and the article returns to the reference tier until re-approved.
* **Update a source**: change `sources.json` (dates, link, terms) after opening the page again; never record a date you did not see.
* **Retire an article**: remove it from the pack; the review row stays for the audit trail.
* **Add a language**: needs a clinician who reads that language to verify it; today the articles are English only (topic *detection* understands Hinglish and Hindi words).

## 6. What it does not do

It does not interpret a person's photo, report or symptoms as a condition. It does not recommend a product by brand, a prescription medicine or a dose. It does not replace the dermatologist, and no article
has yet been approved by one.

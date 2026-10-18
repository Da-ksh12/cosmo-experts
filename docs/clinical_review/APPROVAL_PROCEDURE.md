# How clinical content is approved (and what stops unapproved content being used)

Audience: the clinic's medical lead and whoever administers the deployment.

## Current state
Nothing has been clinically approved. The software ships **drafts** only (`backend/content/`). The patient assistant answers from the patient's own documents,
from approved quotations, and from knowledge-base articles that a doctor has approved. At present **no article is approved**, and the urgent-symptom redirect is a small
built-in default, not a clinician-approved policy.

## Knowledge-base articles
1. Drafts appear in the clinic workspace under "Clinically reviewed knowledge base", marked "software-drafted, not yet reviewed".
2. A signed-in **doctor** opens **Review**, reads the exact text and the "statements to verify", edits if needed (an edit returns the article to draft and raises its version),
   names the source(s) checked, ticks the attestation and presses **Approve this text**. Administrators and patients cannot approve.
3. The system records reviewer, time, source note, the attestation text and the SHA-256 of the exact title and body (audit action `kb.approve`).
4. The assistant uses an article only while it is approved **and** its stored text still matches the approved hash. A direct database edit, or an approval from before this
   mechanism existed, disables the article rather than silently keeping it.
5. With `COSMO_MODE=production` the approver must also be a **verified clinician** (an administrator records credentials under "Verify a clinician").

## Urgent-symptom policy
* The application never reads `content/urgent_policy.draft.json`.
* Without an approved file it uses the built-in default (chest pain, breathing difficulty, severe bleeding, unconsciousness, suicide, anaphylaxis, facial/throat swelling, high fever, fainting).
* To install an approved policy: the reviewer edits the draft into `{ "message": ..., "patterns": [...] }`, approves it, and the administrator runs
  `python -m scripts.make_approved_policy reviewed.json --name ... --role ... --registration ... --date ... --confirm-reviewer-approved`.
  This writes `content/urgent_policy.approved.json` containing the reviewer's details and the hash of the exact content.
* At load time the application validates the approval fields, the hash, pattern count/length, regex validity and nested-quantifier backtracking risk. **Any failure falls back to the built-in default**
  and is shown at `GET /api/clinic/content-status`.
* Limit: the hash proves the content was not altered after approval; it is **not** a digital signature. Who may place that file on the server is a matter for the clinic's change control and file permissions.

## Not covered by software
Clinical correctness, completeness of emergency phrases, language coverage beyond what a clinician verifies, regulatory classification, and periodic re-review dates are the clinic's responsibility.
Keyword matching cannot understand context; a non-match is never evidence that a situation is safe.

## Regenerating the review packet
`cd backend && python -m scripts.export_review_packet` rewrites `REVIEW_PACKET.md`, `kb_review.csv` and `urgent_policy_review.csv`.

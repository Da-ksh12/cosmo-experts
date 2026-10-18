"""Build the clinician review packet from the shipped draft content. Nothing is approved by running this.

    python -m scripts.export_review_packet            -> docs/clinical_review/{REVIEW_PACKET.md, kb_review.csv, urgent_policy_review.csv}
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from app import clinical_content as cc

OUT = Path(__file__).resolve().parents[2] / "docs" / "clinical_review"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    arts = cc.kb_drafts()
    pol = json.loads((cc.CONTENT_DIR / "urgent_policy.draft.json").read_text(encoding="utf-8"))
    pol_hash = cc.sha({"message": pol["proposed_message"], "patterns": [p for c in pol["categories"] for p in c["patterns"]]})

    with open(OUT / "kb_review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["article_id", "title", "language", "content_sha256", "decision (approve / approve with edits / reject)", "edited_text_if_any", "source_checked", "reviewer_comments"])
        for a in arts:
            w.writerow([a["id"], a["title"], a.get("language", "en"), cc.article_hash(a["title"], a["body"], a.get("language", "en")), "", "", "", ""])
    with open(OUT / "urgent_policy_review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["category", "title", "patterns", "decision (keep / change / remove)", "requested_changes", "comments"])
        for c in pol["categories"]:
            w.writerow([c["id"], c["title"], " | ".join(c["patterns"]), "", "", ""])

    L = ["# Clinical review packet (DRAFT, nothing here is approved)", "",
         "Prepared for: a qualified clinician (the clinic's medical lead or a delegate) who will correct, approve or reject the content below.", "",
         "**Status:** every item is a software-written draft. The patient assistant does **not** use any of it. The assistant currently answers only from the patient's own "
         "documents, from clinician-approved articles (none are approved yet) and from approved quotations, and uses a small built-in emergency-redirect default.", "",
         "## What we need from you", "",
         "1. For each knowledge-base article (Part A): approve, edit or reject, and note the source you checked it against.",
         "2. For the urgent-symptom policy (Part B): confirm or change the categories, phrases and the message, and answer the open questions.",
         "3. Tell us which languages must be supported and who verifies Hindi/Hinglish wording (Part C).", "",
         "How approval is recorded and enforced: see `APPROVAL_PROCEDURE.md`. Sign-off page: `SIGNOFF_TEMPLATE.md`. Spreadsheet versions: `kb_review.csv`, `urgent_policy_review.csv`.", "",
         "Files reviewed (hashes identify the exact draft version):", "",
         f"* `backend/content/kb_drafts.json`: {len(arts)} articles",
         f"* `backend/content/urgent_policy.draft.json`: content hash `{pol_hash[:16]}…`", "",
         "## Part A: knowledge-base articles", ""]
    for i, a in enumerate(arts, 1):
        L += [f"### A{i}. {a['title']}", "", f"`{a['id']}`, language `{a.get('language', 'en')}`, text hash `{cc.article_hash(a['title'], a['body'], a.get('language', 'en'))[:16]}…`", "",
              "> " + a["body"], "", "Statements to verify:", ""]
        L += [f"- [ ] {c}" for c in a["claims_to_verify"]]
        L += ["", "Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________", ""]
    L += ["## Part B: urgent-symptom policy", "", f"**Proposed patient message** (needs your approval and the real emergency number/clinic contact):", "",
          "> " + pol["proposed_message"], "", "**Design decisions that need your answer:**", ""]
    L += [f"- [ ] {q}" for q in pol["design_decisions_for_reviewer"]]
    L += ["", "**Limits of the mechanism (please read):**", ""] + [f"- {x}" for x in pol["_meta"]["limits_of_the_mechanism"]] + [""]
    for c in pol["categories"]:
        L += [f"### B: {c['title']} (`{c['id']}`)", "", f"Why: {c['rationale']}", "", "Phrases that trigger it (regular expressions):", ""]
        L += [f"- `{p}`" for p in c["patterns"]]
        L += ["", "Should trigger: " + "; ".join(f"“{t}”" for t in c["example_positive"])]
        if c["example_negative"]:
            L += ["", "Should not trigger: " + "; ".join(f"“{t}”" for t in c["example_negative"])]
        if c.get("known_false_positives"):
            L += ["", "Known false alarms (accepted by the draft): " + "; ".join(f"“{t}”" for t in c["known_false_positives"])]
        L += ["", "Questions:", ""] + [f"- [ ] {q}" for q in c["review_questions"]] + ["", "Decision: [ ] keep  [ ] change  [ ] remove", ""]
    lang = pol["language_candidates_NEEDS_NATIVE_SPEAKER_CLINICIAN"]
    L += ["## Part C: Hindi / Hinglish", "", lang["status"], ""]
    for k, v in lang["hinglish_latin_script"].items():
        L += [f"- {k}: " + "; ".join(f"“{t}”" for t in v)]
    L += ["", f"Devanagari: {lang['devanagari']}", "", "## What the software tests already prove (and what it cannot)", "",
          "Proven by `backend/tests/test_clinical_content.py`: drafts are never used; approval needs a doctor, an attestation and a source note; approval is bound to the exact text "
          "(an edit or a direct database change withdraws it); a tampered or malformed approved-policy file is ignored in favour of the built-in default; the draft patterns match "
          "their own examples and cover everything the built-in default covers.", "",
          "**Not proven and not provable by software:** clinical correctness of any sentence, the right set of emergency phrases, or that keyword matching catches real patient wording. "
          "Matching is on typed or transcribed text, cannot understand context or negation, and a non-match must never be read as 'safe'.", ""]
    (OUT / "REVIEW_PACKET.md").write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT} ({len(arts)} articles, {len(pol['categories'])} urgent categories)")


if __name__ == "__main__":
    main()

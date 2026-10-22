"""Build the clinician review packet for the dermatology library and its warning-sign rules from the shipped content. Nothing is approved by running this.

    python -m scripts.export_derm_review   -> docs/clinical_review/{DERM_REVIEW_PACKET.md, derm_library_review.csv, derm_rules_review.csv}

Approving an article is done in the clinic workspace (Clinically reviewed knowledge base): the clinician sees the exact text, ticks the attestation and names the source they checked it against.
The hash printed here is the hash that approval binds to, so a clinician can confirm that the text they read is the text that is approved.
"""
from __future__ import annotations

import csv
from pathlib import Path

from app import clinical_content as cc
from app.derm import pack, triage

OUT = Path(__file__).resolve().parents[2] / "docs" / "clinical_review"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pk = pack.load()
    rules = triage.review_sheet()
    arts = sorted(pk.articles.values(), key=lambda a: a.id)

    def bound_hash(a: pack.Article) -> str:
        return cc.article_hash(a.title, pack.render_body(a), a.language or "en")

    with open(OUT / "derm_library_review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["article_id", "title", "topic", "version", "content_sha256 (what approval binds)", "sources", "statements_to_verify", "decision (approve / approve with edits / reject)", "edited_text_if_any",
                    "source_checked", "reviewer_comments"])
        for a in arts:
            w.writerow([a.id, a.title, a.topic, a.version, bound_hash(a), " | ".join(a.source_ids), len(a.claims_to_verify), "", "", "", ""])
    with open(OUT / "derm_rules_review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "level", "when it fires", "what the person is told (reason)", "article shown", "sources", "decision (keep / change / remove)", "requested_changes", "comments"])
        for r in rules:
            w.writerow([r["id"], r["level"], r["trigger"], r["reason_shown"], r["article"] or "", " | ".join(r["sources"]), "", "", ""])

    L = ["# Dermatology library review packet (DRAFT, nothing here is approved)", "",
         "Prepared for: the clinic's dermatologist (or a delegate), who will correct, approve or reject the content below.", "",
         f"**Status:** every article and every warning-sign rule is software-written and **pending clinician review** (pack `{pack.VERSION}`, fingerprint `{pk.sha256[:16]}…`). "
         "Until an article is approved in the clinic workspace, patients see it only in the labelled *reference* tier (\"General information based on the sources listed below. The clinic's "
         "dermatologist has not reviewed this wording yet.\"), and that tier is **off by default when the app runs in production** (`COSMO_KB_REFERENCE=labelled` turns it on).", "",
         "## What we need from you", "",
         "1. For each article (Part A): read the exact text, check the statements listed under *Statements to verify* against the sources, then approve, edit or reject it in the clinic workspace "
         "(*Clinically reviewed knowledge base*). An approval is bound to the exact text (the hash below) and lapses on the re-review date. An edit returns the article to draft.",
         "2. For each warning-sign rule (Part B): confirm, change or remove it. These decide when the assistant says *this should be looked at today* or *worth booking soon*.",
         "3. Confirm that the way the sources are used is acceptable to the clinic (Part C).", "",
         "Sign-off page: `SIGNOFF_TEMPLATE.md`. Spreadsheet versions: `derm_library_review.csv`, `derm_rules_review.csv`. How approval is recorded: `APPROVAL_PROCEDURE.md`.", "",
         "## Part A: articles", ""]
    for i, a in enumerate(arts, 1):
        L += [f"### A{i}. {a.title}", "", f"`{a.id}`, topic `{a.topic}`, version {a.version}, text hash that approval binds: `{bound_hash(a)[:16]}…`", "", f"> {a.summary}", ""]
        for sec in pack.SECTION_ORDER:
            if a.sections.get(sec):
                L += [f"**{pack.SECTION_TITLES[sec]}**", ""] + [f"- {x}" for x in a.sections[sec]] + [""]
        if a.contraindications:
            L += ["**When this advice may not apply (shown with self-care advice)**", ""] + [f"- {x}" for x in a.contraindications] + [""]
        L += ["**Limits the article states**", ""] + [f"- {x}" for x in a.limitations] + [""]
        L += ["**Sources**", ""]
        for s in pk.source_list(a):
            L.append(f"- {s['publisher']}: {s['title']} <{s['url']}> (page checked {s['page_reviewed'] or 'date not shown'}; reuse basis `{s['terms']}`"
                     f"{'; **source re-check overdue**' if s['source_review_overdue'] else ''})")
        L += ["", "**Statements to verify before approving**", ""] + [f"- [ ] {c}" for c in a.claims_to_verify]
        L += ["", "Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________", ""]
    L += ["## Part B: warning-sign rules", "",
          "Each rule reads the person's own words (a negated mention such as \"no fever\" does not count). A rule that fires shows the reason below and the next step. **Emergency wording is never written "
          "by the software**: it is the clinic's urgent-symptom policy message (see `REVIEW_PACKET.md`, Part B), shown exactly. A non-match must never be read as \"safe\".", ""]
    for r in rules:
        L += [f"### B: `{r['id']}` ({r['level']})", "", f"When it fires: {r['trigger']}", "", f"What the person is told: {r['reason_shown']}", "",
              f"Article shown: `{r['article'] or '-'}`. Sources: {', '.join(r['sources']) or '-'}.", "", "Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________", ""]
    L += ["## Part C: how the sources are used", "",
          "Every article states its sources (publisher, page, the date the page itself showed, and the reuse basis that was checked): UK Open Government Licence v3.0 pages (attribution is shown "
          "with the answer), US government public-domain pages (MedlinePlus credit is shown), and a few publishers whose terms allow only facts to be restated (those statements are written in "
          "the software's own words, are not copied, and carry no quotation). Nothing was scraped; each page was read by a person at the date listed. Please tell us if any source is not "
          "acceptable to the clinic.", "",
          "## What the software tests already prove (and what it cannot)", "",
          "Proven by `backend/tests/test_derm_knowledge.py`: the pack loads only when every source exists and every article has limits and claims to verify; the pack can never mark itself "
          "approved; an unreviewed article is never shown without its label (and not at all when the reference tier is off); an approved article is shown as approved only while its hash matches "
          "and its approval has not lapsed; no statement gives a diagnosis, a dose, a prescription-only drug or a certainty claim; the warning-sign rules fire on their own examples and not on "
          "their negations.", "",
          "**Not proven and not provable by software:** that any sentence is clinically correct or suitable for your patients, that the rules catch everything that matters, or that keyword "
          "matching understands every way a person describes a problem. That is exactly what this review is for.", ""]
    (OUT / "DERM_REVIEW_PACKET.md").write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT} ({len(arts)} articles, {len(rules)} rules)")


if __name__ == "__main__":
    main()

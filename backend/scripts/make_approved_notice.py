"""Package a LAWYER-APPROVED privacy notice as content/consent_notice.approved.json (see app/consent.py).

Records who approved which exact text; it decides nothing. Refuses without the reviewer's details and --confirm-reviewer-approved.
    python -m scripts.make_approved_notice approved_notice.md --name "A. Counsel" --role "Legal counsel" --registration "BAR-123" --date 2026-11-01 --confirm-reviewer-approved"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import clinical_content as cc
from app import consent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notice", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--registration", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--confirm-reviewer-approved", action="store_true")
    ap.add_argument("--out", type=Path, default=cc.CONTENT_DIR / "consent_notice.approved.json")
    a = ap.parse_args(argv)
    if not a.confirm_reviewer_approved:
        print("refused: pass --confirm-reviewer-approved only when the named reviewer has approved this exact text", file=sys.stderr)
        return 2
    text = a.notice.read_text(encoding="utf-8")
    if not text.strip() or "DRAFT" in text.upper().split("\n", 1)[0]:
        print("refused: the notice is empty or still marked DRAFT", file=sys.stderr)
        return 2
    doc = {"text": text, "approval": {"reviewer_name": a.name, "reviewer_role": a.role, "registration_number": a.registration, "approved_on": a.date,
                                      "statement": "I reviewed this exact privacy notice and approve it for use with patients.", "content_sha256": consent._sha(text)}}
    a.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {a.out} (sha256 {doc['approval']['content_sha256'][:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

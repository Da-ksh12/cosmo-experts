"""Package a CLINICIAN-REVIEWED urgent-symptom policy as `content/urgent_policy.approved.json`.

This tool does not decide anything. It records, in the file the application validates, who approved which exact content. Run it only after the
named clinician has reviewed and approved the policy; it refuses to run without the reviewer's details and an explicit confirmation flag.

    python -m scripts.make_approved_policy reviewed_policy.json --name "Dr A" --role "Medical director" --registration "MC-1234" \
        --date 2026-11-01 --confirm-reviewer-approved

`reviewed_policy.json` is {"message": "...", "patterns": ["regex", ...]} as edited and approved by the reviewer (not the raw draft).
The application re-validates everything at load time (hash, patterns, size, backtracking) and falls back to the built-in default if anything is wrong."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import clinical_content as cc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("policy", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--registration", required=True, help="the reviewer's professional registration number")
    ap.add_argument("--date", required=True, help="approval date, YYYY-MM-DD")
    ap.add_argument("--confirm-reviewer-approved", action="store_true", help="the named reviewer has approved exactly this content")
    ap.add_argument("--out", type=Path, default=cc.CONTENT_DIR / "urgent_policy.approved.json")
    a = ap.parse_args(argv)
    if not a.confirm_reviewer_approved:
        print("refused: pass --confirm-reviewer-approved only when the named reviewer has approved this exact content", file=sys.stderr)
        return 2
    pol = json.loads(a.policy.read_text(encoding="utf-8"))
    doc = {"policy": {"message": pol["message"], "patterns": pol["patterns"]},
           "approval": {"reviewer_name": a.name, "reviewer_role": a.role, "registration_number": a.registration, "approved_on": a.date,
                        "statement": "I reviewed this exact urgent-symptom policy and approve it for use by the patient assistant.", "content_sha256": ""}}
    doc["approval"]["content_sha256"] = cc.sha(doc["policy"])
    cc.validate_policy(doc)                                    # raises ValueError if it would be rejected at load time
    a.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {a.out} (sha256 {doc['approval']['content_sha256'][:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

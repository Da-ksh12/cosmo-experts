"""Record the Phase 5 licence decisions, with evidence read from the official sources at run time.

    python -m scripts.phase5_governance [--out ../docs/eval/dataset_decisions.json]

Approved (licence permits the project's intended, commercial, use; evidence hashed): ISIC contributor-licensed subsets (CC-0, CC-BY), SCIN, DERM12345.
Refused (licence or access terms do not permit it, or there is none): the ISIC archive as a whole (it includes the NC pool), HAM10000, Fitzpatrick17k, DDI, ACNE04.
The decision is recorded by the automated Phase 5 run on the owner's instruction; each approval is reversible by setting the dataset to rejected."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402
from app.datasets import governance as G, registry, sources as S  # noqa: E402

ACTOR = "phase5-agent"
RAW = "https://raw.githubusercontent.com/"


def isic_evidence(kind: str) -> list[dict]:
    q = S.ISIC_QUERIES[kind]
    fac = S.get_bytes(S.ISIC_API + "images/facets/?" + urllib.parse.urlencode({"query": q, "fields": "copyright_license"}))
    sample = S.get_bytes(S.ISIC_API + "images/search/?" + urllib.parse.urlencode({"query": q, "limit": 3}))
    return [G.evidence_from_url("ISIC Terms & Conditions (sections 3.1, 4.2)", "https://www.isic-archive.com/terms-conditions"),
            G.evidence_from_bytes("ISIC API: licence facet of the subset query (only CC-0 and CC-BY may be non-zero)", S.ISIC_API + "images/facets/?query=" + urllib.parse.quote(q), fac),
            G.evidence_from_bytes("ISIC API: first records of the subset (licence and attribution per image)", S.ISIC_API + "images/search/?query=" + urllib.parse.quote(q), sample)]


ISIC_PERMISSION = ("ISIC Terms & Conditions (sections 3.1 and 4.2): images are made available under the Contributor License chosen by the contributor (CC0, BY or BY-NC), and site resources may be used for AI "
                   "training and evaluation subject to the applicable licences. This dataset contains only images whose API licence field is CC-0 or CC-BY (the query is recorded in selection.json and every "
                   "listed record is re-checked). ISIC does not state the version of CC-BY; every CC-BY version permits commercial use with attribution, kept per image in attribution.csv. The site terms "
                   "(3.1) restrict commercial use of the Site itself, and the contributor licence governs the images: legal review is recommended before any commercial deployment.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / "docs" / "eval" / "dataset_decisions.json")
    ap.add_argument("--only", action="append", default=None, help="re-record only this decision (isic_subsets, scin, derm12345, isic_archive, ham10000, fitzpatrick17k, ddi, acne04); repeatable. The file is always rebuilt from the registry.")
    a = ap.parse_args(argv)
    db.init()
    registry.seed()
    decisions, failures = [], []

    def step(name, fn):
        """One decision per step: a page that cannot be fetched (a throttled site) blocks that decision only, and is reported."""
        if a.only and name not in a.only:
            return
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failures.append({"dataset": name, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            print(f"FAILED    {name:16} {str(e)[:120]}")

    def done(rec, why):
        decisions.append({"dataset": rec["id"], "name": rec["name"], "decision": rec["approval_status"], "verified_spdx": rec.get("verified_spdx"), "why": why,
                          "evidence": [{k: e[k] for k in ("label", "url", "sha256", "bytes")} for e in rec.get("evidence", [])]})
        print(f"{rec['approval_status']:9} {rec['id']:16} {why[:110]}")

    def d_isic_subsets():
        for ds, kind, extra in (("isic-dermoscopy", "dermoscopy", "256 px official thumbnails of dermoscopic images"), ("isic-clinical", "clinical", "full-size clinical photographs, PAD-UFES-20's own images excluded")):
            rec = G.record(ds, ACTOR, approve=True, spdx="LicenseRef-ISIC-Contributor-CC0-or-CC-BY", claimed_license="Per image: CC-0 or CC-BY (contributor licence), version of CC-BY not stated by ISIC",
                           permission_document=ISIC_PERMISSION, notes=f"Subset: {extra}. Licence verified from ISIC's own terms and API.", evidence=isic_evidence(kind),
                           restrictions=["attribution per image", "no attempt to re-identify", "CC-BY-NC images are never listed or downloaded"])
            done(rec, "contributor licence CC-0 / CC-BY only; AI training allowed subject to those licences")

    step("isic_subsets", d_isic_subsets)

    def d_scin():
        scin_ev = [G.evidence_from_url("SCIN Data Use License (LICENSE)", RAW + "google-research-datasets/scin/main/LICENSE"),
                   G.evidence_from_url("SCIN README", RAW + "google-research-datasets/scin/main/README.md"),
                   G.evidence_from_url("SCIN dataset schema", RAW + "google-research-datasets/scin/main/dataset_schema.md")]
        rec = G.record("scin", ACTOR, approve=True, spdx="LicenseRef-SCIN-Data-Use-License", claimed_license="SCIN Data Use License (worldwide, royalty-free, irrevocable; attribution; no re-identification)",
                       permission_document=("SCIN Data Use License, section 2(a): a worldwide, royalty-free, irrevocable licence to reproduce and Share the Licensed Material and produce Adapted Material in all media; "
                                            "section 3: conditions are attribution when Sharing and a prohibition on re-identifying data subjects; there is no non-commercial or share-alike clause "
                                            f"(LICENSE sha256 {scin_ev[0]['sha256']}). Google's FAQ states that downloaders may use the images for commercial, research and development purposes. "
                                            "A custom licence, not an SPDX identifier: legal review is recommended before commercial use."),
                       notes="Licence text read in full from the official repository.", evidence=scin_ev, restrictions=["no attempt to re-identify contributors", "attribution when sharing", "images may be sensitive or graphic"])
        done(rec, "SCIN Data Use License: no non-commercial clause; conditions are attribution and no re-identification")

    step("scin", d_scin)

    def d_derm12345():
        dv = S.get_bytes(S.DATAVERSE + "api/datasets/:persistentId/?" + urllib.parse.urlencode({"persistentId": S.DERM_DOI}), limit=5_000_000)
        lic = json.loads(dv)["data"]["latestVersion"].get("license") or {}
        if lic.get("name") != "CC BY 4.0":
            raise SystemExit(f"DERM12345 no longer states CC BY 4.0 ({lic}); not approving.")
        rec = G.record("derm12345", ACTOR, approve=True, spdx="CC-BY-4.0", claimed_license="CC BY 4.0",
                       permission_document="", notes="Licence read from the repository's own record for the original authors' deposit; every image row also carries copyright-license = CC-BY.",
                       evidence=[G.evidence_from_bytes("Harvard Dataverse record (latest version) with its licence", S.DATAVERSE + "api/datasets/:persistentId/?persistentId=" + S.DERM_DOI, dv)],
                       restrictions=["attribution (cite Yilmaz et al.)"])
        done(rec, "CC BY 4.0 stated by the original authors' repository record")

        # ---------------------------------------------------------------- refused

    step("derm12345", d_derm12345)

    def d_isic_archive():
        rec = G.record("isic-archive", ACTOR, approve=False, spdx="mixed", claimed_license="Mixed per image: CC-0, CC-BY and CC-BY-NC", permission_document="",
                       notes="Refused as a whole: it contains a large non-commercial pool. The CC-0/CC-BY subsets are registered separately (isic-dermoscopy, isic-clinical).",
                       evidence=[G.evidence_from_url("ISIC Terms & Conditions", "https://www.isic-archive.com/terms-conditions"),
                                 G.evidence_from_bytes("ISIC API: licence facet over the whole archive", S.ISIC_API + "images/facets/?fields=copyright_license",
                                                       S.get_bytes(S.ISIC_API + "images/facets/?" + urllib.parse.urlencode({"fields": "copyright_license"})))])
        done(rec, "mixed licences including a CC-BY-NC pool: only the licence-filtered subsets are used")

    step("isic_archive", d_isic_archive)

    def d_ham10000():
        ham = S.get_bytes(S.DATAVERSE + "api/datasets/:persistentId/?" + urllib.parse.urlencode({"persistentId": "doi:10.7910/DVN/DBW86T"}), limit=5_000_000)
        rec = G.record("ham10000", ACTOR, approve=False, spdx="CC-BY-NC-4.0", claimed_license="CC BY-NC 4.0 (stated by the original publisher, Harvard Dataverse)", permission_document="",
                       notes="Non-commercial licence from the original publisher. A third-party Zenodo copy under CC BY 4.0 (record 17702692, uploader is not the rights holder and says use remains subject to the original terms) cannot re-license it and was not used.",
                       evidence=[G.evidence_from_bytes("Harvard Dataverse record of HAM10000 (terms of use)", S.DATAVERSE + "api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/DBW86T", ham)])
        done(rec, "Creative Commons Attribution-NonCommercial 4.0 (publisher's terms)")

    step("ham10000", d_ham10000)

    def d_fitzpatrick17k():
        rec = G.record("fitzpatrick17k", ACTOR, approve=False, spdx="CC-BY-NC-SA-3.0", claimed_license="CC BY-NC-SA 3.0; image rights remain with the source websites", permission_document="",
                       notes="Non-commercial, share-alike; the images themselves are scraped from websites whose terms are not those of the annotations.",
                       evidence=[G.evidence_from_url("Fitzpatrick17k README", RAW + "mattgroh/fitzpatrick17k/main/README.md")])
        done(rec, "README: Creative Commons BY-NC-SA 3.0; image rights unclear")

    step("fitzpatrick17k", d_fitzpatrick17k)

    def d_ddi():
        rec = G.record("ddi", ACTOR, approve=False, spdx="Stanford-AIMI-noncommercial", claimed_license="Non-commercial research use under Stanford AIMI data use terms; commercial use needs a separate licence",
                       permission_document="", notes="Registration-gated download; commercial use requires a commercial licence from Stanford.",
                       evidence=[G.evidence_from_url("Stanford AIMI shared datasets (terms)", "https://aimi.stanford.edu/shared-datasets"),
                                 G.evidence_from_url("Stanford AIMI DDI page", "https://aimi.stanford.edu/datasets/ddi-diverse-dermatology-images")])
        done(rec, "AIMI: no-charge non-commercial research use; commercial purposes need a commercial licence")

    step("ddi", d_ddi)

    def d_acne04():
        gh = S.get_bytes("https://api.github.com/repos/xpwu95/LDL", limit=500_000)
        rec = G.record("acne04", ACTOR, approve=False, spdx="none", claimed_license="No licence stated in the authors' repository", permission_document="",
                       notes="No licence file or statement; the face images' consent and reuse terms are undocumented; author-provided download links. Written permission from the authors would be needed.",
                       evidence=[G.evidence_from_bytes("GitHub repository record (licence field is null)", "https://api.github.com/repos/xpwu95/LDL", gh),
                                 G.evidence_from_url("ACNE04 repository README", RAW + "xpwu95/LDL/master/README.md")])
        done(rec, "no licence in the authors' repository; face-image consent undocumented")

    step("acne04", d_acne04)

    # the file records the registry's CURRENT state for these datasets (a decision made in an earlier run and not repeated now is still a decision)
    decisions = []
    for ds in ("isic-dermoscopy", "isic-clinical", "scin", "derm12345", "isic-archive", "ham10000", "fitzpatrick17k", "ddi", "acne04"):
        rec = registry.get(ds)
        decisions.append({"dataset": ds, "name": rec["name"], "decision": rec["approval_status"], "verified_spdx": rec.get("verified_spdx"), "licence": rec.get("claimed_license"),
                          "notes": rec.get("verify_notes"), "restrictions": rec.get("restrictions"), "verified_by": rec.get("verified_by"),
                          "evidence": [{k: e[k] for k in ("label", "url", "sha256", "bytes")} for e in rec.get("evidence", [])]})
    out = {"schema": "dataset-decisions-1", "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "actor": ACTOR,
           "note": "Decisions taken by the automated Phase 5 run on the owner's instruction to use only datasets with verified licences. Each is reversible; legal review is recommended before commercial deployment.",
           "decisions": decisions, "failures": failures}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", a.out, "| failures:", len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

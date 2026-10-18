"""Dataset cards and model cards: plain, honest summaries generated from what is actually recorded (registry record, version manifest, model card, release gates).

A card never states an approval, a clinical validation or a licence that is not in the record. Where something is missing it says so ("not recorded"). Paths are shown relative to
the data directory so cards can be shared without exposing the host's file layout."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from ..config import DATA_DIR

NOT_RECORDED = "not recorded"


def _rel(path: str | None) -> str:
    if not path:
        return NOT_RECORDED
    try:
        return "data/" + Path(path).resolve().relative_to(DATA_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        return "outside the data directory"


def manifest_sha256(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()


def class_distribution(entries: list[dict]) -> dict:
    c = Counter(e.get("label") or "unlabelled" for e in entries)
    n = sum(c.values()) or 1
    return {k: {"n": v, "share": round(v / n, 4)} for k, v in sorted(c.items(), key=lambda kv: -kv[1])}


def fitzpatrick_distribution(entries: list[dict]) -> dict:
    def b(e):
        v = str(e.get("fitzpatrick") or "")
        return "not recorded" if v in ("", "None", "nan") else "I-II" if v in ("1", "1.0", "2", "2.0") else "III" if v in ("3", "3.0") else "IV-VI"
    c = Counter(b(e) for e in entries)
    n = sum(c.values()) or 1
    return {k: {"n": v, "share": round(v / n, 4)} for k, v in sorted(c.items())}


def representation_warnings(entries: list[dict]) -> list[str]:
    out = []
    fz = fitzpatrick_distribution(entries)
    known = sum(v["n"] for k, v in fz.items() if k != "not recorded")
    dark = fz.get("IV-VI", {"n": 0})["n"]
    if known == 0:
        out.append("Skin-tone (Fitzpatrick) labels are not recorded: performance across skin tones cannot be assessed from this dataset.")
    elif dark / known < 0.10:
        out.append(f"Fitzpatrick IV-VI make up {100 * dark / known:.1f} % of the images that have a skin-type label: results cannot be assumed to hold for darker skin.")
    cd = class_distribution(entries)
    if cd:
        smallest = min(v["n"] for v in cd.values())
        if smallest < 100:
            out.append(f"The smallest class has {smallest} images: per-class figures will have wide uncertainty.")
    return out


def dataset_card(record: dict, manifest: dict | None) -> dict:
    """JSON card for one dataset (its registry record) and, if imported, one version."""
    lic = {"claimed": record.get("claimed_license") or NOT_RECORDED, "verified_spdx": record.get("verified_spdx") or "not verified", "terms_verified": bool(record.get("terms_verified")),
           "evidence_snapshot_sha256": record.get("terms_snapshot_hash") or NOT_RECORDED, "evidence_checked_at": record.get("terms_checked_at"),
           "written_permission_reference": record.get("permission_document") or None, "attribution": record.get("attribution") or NOT_RECORDED,
           "commercial_eligibility": record.get("commercial_eligibility", "unknown")}
    card = {"id": record["id"], "name": record.get("name"), "publisher": record.get("publisher") or record.get("maintainer") or NOT_RECORDED,
            "provenance": record.get("provenance") or {"source_url": record.get("url"), "download_method": record.get("download_method"), "citation": record.get("attribution")},
            "description": record.get("description"), "modality": record.get("modality"), "annotations": record.get("annotations", []), "intended_task": record.get("intended_task"),
            "license": lic, "permitted_uses": record.get("approved_uses", []), "restrictions": record.get("restrictions", []) or _restrictions_from(record),
            "approval_status": record.get("approval_status", "unreviewed"), "import_status": record.get("import_status", "imported" if manifest else "not_imported"),
            "storage_location": _rel(record.get("local_path") or (manifest or {}).get("root")), "known_limitations": record.get("limitations") or NOT_RECORDED,
            "populations": record.get("populations") or NOT_RECORDED, "version": None}
    if manifest:
        entries = [e for e in manifest.get("entries", []) if e.get("label")]
        groups = {e.get("group_id") for e in entries}
        card["version"] = {"number": manifest.get("version"), "images": manifest.get("count"), "labelled_images": len(entries), "subjects": len(groups),
                           "manifest_sha256": manifest_sha256(manifest), "complete": manifest.get("complete", True), "imported_at": manifest.get("imported_at"),
                           "class_distribution": manifest.get("class_distribution") or class_distribution(entries), "fitzpatrick_distribution": manifest.get("fitzpatrick_distribution") or fitzpatrick_distribution(entries),
                           "label_provenance": dict(Counter(e.get("label_provenance", "unspecified") for e in entries)), "class_mapping": manifest.get("class_mapping") or "identity (dataset labels are the task labels)",
                           "split_metadata": manifest.get("split_metadata") or "computed at training time: patient-level, stratified, seeded (see each model card)",
                           "exact_duplicates_skipped": len(manifest.get("duplicates_skipped", [])), "label_conflicts_excluded": len(manifest.get("label_conflicts_excluded", [])),
                           "near_duplicates_flagged": len(manifest.get("near_duplicates_flagged", [])), "read_errors": len(manifest.get("errors", []))}
        card["representation_warnings"] = representation_warnings(entries)
    else:
        card["representation_warnings"] = ["Not imported: no counts, class balance or skin-tone distribution are known."]
    return card


def _restrictions_from(record: dict) -> list[str]:
    lic = (record.get("claimed_license") or "").lower()
    out = []
    if "nc" in lic or "non-commercial" in lic:
        out.append("Non-commercial use only (as claimed; verify the official terms).")
    if "unclear" in lic or "agreement" in lic or "verify" in lic:
        out.append("Terms unclear or require an agreement: do not use until verified.")
    if "sa" in lic.split():
        out.append("Share-alike conditions may apply.")
    return out


def dataset_card_markdown(card: dict) -> str:
    v = card.get("version")
    L = [f"# Dataset card: {card['name']} ({card['id']})", "",
         "> This card summarises what is RECORDED. It is not an approval, a clinical validation or legal advice.", "",
         f"- Publisher: {card['publisher']}", f"- Source: {json.dumps(card['provenance'], ensure_ascii=False)}", f"- Modality: {card['modality']}; annotations: {', '.join(card['annotations']) or NOT_RECORDED}",
         f"- Intended task: {card['intended_task']}", f"- Approval status: **{card['approval_status']}**; import status: {card['import_status']}; stored at `{card['storage_location']}`", "",
         "## Licence and use", f"- Claimed: {card['license']['claimed']}", f"- Verified SPDX: {card['license']['verified_spdx']} (terms verified: {card['license']['terms_verified']}); evidence hash: {card['license']['evidence_snapshot_sha256']}",
         f"- Permitted uses recorded: {', '.join(card['permitted_uses']) or 'none'}", f"- Restrictions: {'; '.join(card['restrictions']) or NOT_RECORDED}", f"- Attribution: {card['license']['attribution']}", ""]
    if v:
        L += ["## Version " + str(v["number"]), f"- Images: {v['images']} ({v['labelled_images']} labelled) from {v['subjects']} subjects; manifest SHA-256 `{v['manifest_sha256'][:16]}…`; complete: {v['complete']}",
              "- Classes: " + ", ".join(f"{k} {c['n']} ({100 * c['share']:.1f} %)" for k, c in v["class_distribution"].items()),
              "- Skin type (Fitzpatrick): " + ", ".join(f"{k} {c['n']} ({100 * c['share']:.1f} %)" for k, c in v["fitzpatrick_distribution"].items()),
              f"- Duplicates skipped: {v['exact_duplicates_skipped']}; label conflicts excluded: {v['label_conflicts_excluded']}; near-duplicates flagged: {v['near_duplicates_flagged']}; unreadable: {v['read_errors']}",
              f"- Class mapping: {v['class_mapping'] if isinstance(v['class_mapping'], str) else json.dumps(v['class_mapping'])}", ""]
    L += ["## Limitations and representation", f"- Known limitations: {card['known_limitations']}", f"- Populations: {card['populations']}"] + [f"- WARNING: {w}" for w in card["representation_warnings"]]
    return "\n".join(L) + "\n"


def model_card_markdown(card: dict, gates: list[dict], lifecycle: str = "", evidence_class: str = "public_research_dataset") -> str:
    t = card.get("test_metrics", {})
    L = [f"# Model card: {card['model_id']}", "", "> RESEARCH MODEL. Not validated for clinical use and not offered to patients unless an approval record and an active lifecycle state say otherwise.", "",
         f"- Task: `{card['task']}`; architecture: {card['architecture']}; lifecycle: **{lifecycle or 'see registry'}**; evidence class: {evidence_class}",
         f"- Dataset: {card['dataset_id']} version {card['dataset_version']} (`{card['dataset_version_id']}`); split hash `{card['split_hash'][:16]}…`; seed {card['seed']}",
         f"- Splits: {card['split_sizes']} images; patients: {card.get('patients_per_split')}",
         f"- Artifact SHA-256 `{card['artifact_sha256']}`; code revision `{card.get('code_revision')}`; device `{card.get('device')}`; trained in {card.get('training_seconds')} s",
         f"- Intended use: {card.get('intended_use')}", "", "## Held-out test results",
         f"- n={t.get('n')}; accuracy {t.get('accuracy')}; macro-F1 {t.get('macro_f1')} (95 % CI {t.get('macro_f1_ci95')}); balanced accuracy {t.get('balanced_accuracy')}; ECE {t.get('ece')}"]
    if t.get("per_class"):
        L += ["", "| class | n | sensitivity | specificity | precision |", "|---|---|---|---|---|"] + [f"| {c} | {m['n']} | {m.get('sensitivity')} | {m.get('specificity')} | {m.get('precision')} |" for c, m in t["per_class"].items()]
    L += ["", "## Baselines"] + [f"- {k}: macro-F1 {v.get('macro_f1')}" for k, v in card.get("baselines", {}).items()]
    L += ["", "## Release gates (unchanged thresholds)"] + [f"- [{'x' if g['passed'] else ' '}] {g['gate']}: {g['detail']}" for g in gates]
    L += ["", "## Limitations"] + [f"- {x}" for x in card.get("limitations", [])] + ["- Previously reported results were inadequate for clinical release; see the gate table above."]
    return "\n".join(L) + "\n"

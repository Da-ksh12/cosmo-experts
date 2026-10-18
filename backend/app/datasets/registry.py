"""Dataset source registry, license gate and task-compatibility assessment.

IMPORTANT: seeded entries carry *claimed* licence information recorded from memory of public documentation.
Every entry starts with terms_verified=False. The licence gate blocks all use until an administrator
retrieves/records the official terms (snapshot + hash) and verifies them. Nothing is assumed free for
commercial use."""
from __future__ import annotations

import hashlib
import json
import urllib.request

from .. import db, netguard

COMMERCIAL_OK = {"CC0-1.0", "CC-BY-4.0", "CC-BY-3.0", "CC-BY-2.0", "MIT", "Apache-2.0", "BSD-3-Clause"}
NONCOMMERCIAL = ("NC", "non-commercial", "noncommercial", "research use only", "research only")

SEED = [
    dict(id="isic-archive", name="ISIC Archive", url="https://www.isic-archive.com", maintainer="International Skin Imaging Collaboration",
         description="Large archive of dermoscopic and some clinical skin-lesion images with diagnosis labels.",
         intended_task="skin lesion classification", modality="dermoscopy+clinical", annotations=["lesion diagnosis", "benign/malignant", "anatomic site"],
         populations="Skewed towards lighter skin tones in many collections; check per-collection metadata.",
         claimed_license="Per-image: CC0, CC-BY or CC-BY-NC (varies by collection)", attribution="Per-image attribution required where CC-BY.",
         download_method="Official ISIC API / website download after accepting terms", version="per collection",
         limitations="Not ordinary selfies; lesion labels are not acne, pigmentation or hair labels."),
    dict(id="ham10000", name="HAM10000", url="https://doi.org/10.7910/DVN/DBW86T", maintainer="Tschandl et al. (Harvard Dataverse)",
         description="10,015 dermoscopic images of pigmented lesions in 7 diagnostic categories.",
         intended_task="pigmented lesion classification", modality="dermoscopy", annotations=["lesion diagnosis (7 classes)", "age", "sex", "site"],
         populations="Predominantly lighter skin; limited sources (Austria, Australia).",
         claimed_license="CC BY-NC 4.0 (claimed; verify)", attribution="Cite Tschandl et al., Sci Data 2018.",
         download_method="Harvard Dataverse download", version="v1",
         limitations="Non-commercial; dermoscopic pigmented lesions only."),
    dict(id="fitzpatrick17k", name="Fitzpatrick 17k", url="https://github.com/mattgroh/fitzpatrick17k", maintainer="Groh et al.",
         description="~16.5k clinical images from online atlases with Fitzpatrick skin-type labels and 114 conditions.",
         intended_task="skin condition classification / fairness evaluation", modality="clinical photographs",
         annotations=["condition label", "Fitzpatrick skin type"], populations="Includes darker skin types but with label noise.",
         claimed_license="Annotations CC BY-NC-SA 3.0; image rights remain with source websites (unclear; verify)",
         attribution="Cite Groh et al. 2021.", download_method="Image URLs list; images fetched from source sites",
         version="2021", limitations="Image redistribution/training rights unclear; label noise."),
    dict(id="ddi", name="Diverse Dermatology Images (DDI)", url="https://ddi-dataset.github.io/", maintainer="Stanford University",
         description="Biopsy-proven clinical images with diverse skin tones for evaluation.",
         intended_task="fairness evaluation of skin disease classifiers", modality="clinical photographs",
         annotations=["biopsy-proven diagnosis", "Fitzpatrick skin type"], populations="Designed for skin-tone diversity; small.",
         claimed_license="Research-use agreement required (verify terms)", attribution="Cite Daneshjou et al. 2022.",
         download_method="Request/agreement via Stanford AIMI", version="2022", limitations="Small; evaluation-oriented; access terms apply."),
    dict(id="pad-ufes-20", name="PAD-UFES-20", url="https://data.mendeley.com/datasets/zr7vgbcyr2", maintainer="Federal University of Espirito Santo",
         description="Smartphone clinical images of skin lesions with patient clinical metadata.",
         intended_task="skin lesion classification (smartphone images)", modality="smartphone clinical photographs",
         annotations=["lesion diagnosis", "patient metadata"], populations="Brazilian population; mostly lighter Fitzpatrick types.",
         claimed_license="CC BY 4.0 (claimed; verify)", attribution="Cite Pacheco et al. 2020.",
         download_method="Mendeley Data download", version="v1", limitations="Lesion diagnosis; not hair, acne or pigmentation labels."),
    dict(id="acne04", name="ACNE04 (acne severity)", url="https://github.com/xpwu95/LDL", maintainer="Wu et al.",
         description="Facial images labelled with acne severity grades and lesion counts.",
         intended_task="acne severity grading", modality="facial photographs", annotations=["acne severity grade", "lesion counts"],
         populations="Undocumented; likely limited diversity.", claimed_license="Unclear; availability and terms must be confirmed with authors",
         attribution="Cite Wu et al. ICCV 2019.", download_method="Author-provided", version="2019",
         limitations="Terms unclear; consent for face images unclear."),
    dict(id="funsd", name="FUNSD (form understanding)", url="https://guillaumejaume.github.io/FUNSD/", maintainer="Jaume et al.",
         description="199 scanned noisy forms with key-value annotations; NOT medical records.",
         intended_task="document layout / OCR benchmark", modality="scanned documents", annotations=["entities", "relations"],
         populations="n/a", claimed_license="Non-commercial research use (verify)", attribution="Cite Jaume et al. 2019.",
         download_method="Direct download", version="1.0", limitations="General forms, not clinical documents; does not authorise use of real patient records."),
]


def seed():
    for s in SEED:
        if not db.q1("SELECT id FROM datasets WHERE id=?", (s["id"],)):
            rec = dict(s, terms_verified=False, terms_snapshot_hash="", terms_checked_at=None, permission_document="",
                       commercial_eligibility="unknown", approved_uses=[], local_path="", file_hashes={})
            db.x("INSERT INTO datasets VALUES(?,?,?,?)", (s["id"], json.dumps(rec), "unreviewed", db.now()))


def get(dataset_id: str) -> dict | None:
    r = db.q1("SELECT * FROM datasets WHERE id=?", (dataset_id,))
    if not r:
        return None
    d = json.loads(r["data"])
    d["approval_status"] = r["approval_status"]
    return d


def save(d: dict):
    status = d.pop("approval_status", "unreviewed")
    db.upsert("datasets", "id", {"id": d["id"], "data": json.dumps(d), "approval_status": status, "updated_at": db.now()})
    d["approval_status"] = status


def list_all() -> list[dict]:
    return [dict(get(r["id"]), license_gate=license_gate(get(r["id"]), "model_training", commercial=True))
            for r in db.q("SELECT id FROM datasets ORDER BY id")]


SPDX_ALIASES = {"cc by 4.0": "CC-BY-4.0", "cc0 1.0": "CC0-1.0", "cc by-nc 4.0": "CC-BY-NC-4.0", "cc by-nc-sa 4.0": "CC-BY-NC-SA-4.0",
                "cc by 3.0": "CC-BY-3.0", "mit": "MIT", "apache-2.0": "Apache-2.0"}


def official_manifest(d: dict, timeout=20) -> dict | None:
    """Machine-readable official metadata (licence name + per-file URLs and SHA-256) for repositories that publish it.
    Currently: Mendeley Data. Returns None for sources without an adapter (terms must then be reviewed manually)."""
    import re
    m = re.search(r"data\.mendeley\.com/datasets/([a-z0-9]+)", d.get("url", ""))
    if not m:
        return None
    api = f"https://data.mendeley.com/public-api/datasets/{m.group(1)}"
    netguard.assert_public(api)
    req = urllib.request.Request(api, headers={"User-Agent": "CosmoExperts-DatasetRegistry/1.0 (terms check)"})
    with netguard.opener().open(req, timeout=timeout) as resp:  # fixed official API host; redirects are re-checked
        body = resp.read(5_000_000)
    j = json.loads(body)
    lic = j.get("data_licence") or {}
    return {"api_url": api, "raw_sha256": hashlib.sha256(body).hexdigest(), "license_name": lic.get("short_name", ""), "license_url": lic.get("url", ""),
            "license_text": lic.get("description", ""), "version": j.get("version"), "doi": (j.get("doi") or {}).get("id", ""),
            "files": [{"name": f["filename"], "url": f["content_details"]["download_url"], "sha256": f["content_details"]["sha256_hash"],
                       "size": f["content_details"]["size"]} for f in j.get("files", [])]}


def fetch_terms(dataset_id: str, actor: str, timeout=15) -> dict:
    """Retrieve the official page (no auth bypass, plain GET) and store a hash + excerpt as an evidence snapshot."""
    d = get(dataset_id)
    if not d:
        raise KeyError(dataset_id)
    manifest = official_manifest(d, timeout)
    if manifest:
        d["official_manifest"] = manifest
        d["official_license_name"] = manifest["license_name"]
        d["terms_snapshot_hash"] = manifest["raw_sha256"]
        d["terms_snapshot_excerpt"] = f'{manifest["license_name"]} ({manifest["license_url"]}): {manifest["license_text"][:800]}'
        status = 200
    else:
        netguard.assert_public(d["url"])
        req = urllib.request.Request(d["url"], headers={"User-Agent": "CosmoExperts-DatasetRegistry/1.0 (terms check)"})
        with netguard.opener().open(req, timeout=timeout) as resp:  # admin-triggered, registry URL only; redirects are re-checked
            body = resp.read(2_000_000)
            status = resp.status
        d["terms_snapshot_hash"] = hashlib.sha256(body).hexdigest()
        d["terms_snapshot_excerpt"] = body[:2000].decode("utf-8", "replace")
    d["terms_checked_at"] = db.now()
    save(d)
    db.audit(actor, "dataset.fetch_terms", dataset_id, {"http_status": status, "sha256": d["terms_snapshot_hash"]})
    return d


def license_gate(d: dict, use: str, commercial: bool) -> dict:
    """use: 'model_training' | 'evaluation' | 'redistribution'. Returns allowed + reasons. Conservative by design."""
    reasons = []
    lic = (d.get("claimed_license") or "").lower()
    if not d.get("terms_verified"):
        reasons.append("Official terms have not been verified by an administrator.")
    if not d.get("terms_snapshot_hash") and not d.get("permission_document"):
        reasons.append("No snapshot of the official terms has been recorded.")
    noncomm = any(k.lower() in lic for k in NONCOMMERCIAL) or "unclear" in lic or "agreement" in lic or "verify" in lic
    spdx = d.get("verified_spdx", "")
    if commercial:
        if spdx not in COMMERCIAL_OK and not d.get("permission_document"):
            reasons.append("Commercial use is not confirmed: no verified permissive licence or written permission on file."
                           + (" Licence text suggests non-commercial/restricted terms." if noncomm else ""))
    if use == "redistribution" and spdx not in COMMERCIAL_OK and not d.get("permission_document"):
        reasons.append("Redistribution rights are not confirmed.")
    if d.get("approval_status") != "approved":
        reasons.append("An administrator has not approved this dataset for the project.")
    return {"allowed": not reasons, "reasons": reasons, "attribution": d.get("attribution", "")}


# ---- task compatibility ----

def compatibility(d: dict, task: dict) -> dict:
    reasons_no, reasons_yes = [], []
    mod, ann = d.get("modality", "").lower(), " ".join(d.get("annotations", [])).lower()
    need_img = task["required_image_type"].lower()
    if "face" in need_img and "facial" not in mod and "face" not in mod:
        reasons_no.append(f'Dataset modality is "{d["modality"]}", but the task needs {task["required_image_type"]}. '
                          "Dermoscopic or lesion-site images are not interchangeable with ordinary face photos.")
    elif "face" in need_img:
        reasons_yes.append("Facial photographs match the required image type.")
    if "scalp" in need_img and "scalp" not in mod and "hair" not in mod:
        reasons_no.append("No scalp/hair imagery: a lesion or face dataset cannot support a scalp task.")
    for lab in task["required_labels"]:
        key = lab.lower().split()[0]
        if key not in ann:
            reasons_no.append(f'Required label "{lab}" is not among the dataset annotations ({d.get("annotations")}).')
        else:
            reasons_yes.append(f'Annotation covering "{lab}" present.')
    if "document" in need_img and "document" not in mod:
        reasons_no.append("Task requires documents.")
    if "segment" in task["architecture"].lower() and "mask" not in ann and "segmentation" not in ann:
        reasons_no.append("The task needs segmentation masks; classification labels cannot support segmentation.")
    return {"dataset": d["id"], "task": task["id"], "compatible": not reasons_no, "reasons_incompatible": reasons_no,
            "reasons_compatible": reasons_yes}

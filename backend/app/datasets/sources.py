"""Acquisition of eligible public dermatology datasets from their OFFICIAL sources (Phase 5).

What is eligible is decided by the registry's licence gate; this module only knows how to *get* data once a source has been judged eligible, and how to turn what it got into the folder
layout `importer.ingest_folder` reads (images/, labels.csv) plus the per-image provenance a licence needs (attribution.csv):

* ISIC Archive (official API, https://api.isic-archive.com): only images whose contributor licence is CC-0 or CC-BY are ever listed; the NC pool is never queried. 256 px thumbnails are used for
  dermoscopy (the official derivative of the same image, same licence), full-size files for the few clinical photographs. Images attributed to the Federal University of Espirito Santo are
  PAD-UFES-20 itself (a separate dataset already held) and are left out of the ISIC clinical set so the same photographs can never sit in both a training and a test split.
* SCIN (Google, public bucket dx-scin-public-data): every object is verified against the bucket's own MD5.
* DERM12345 (Harvard Dataverse, CC BY 4.0, original authors): archives are verified against the repository's MD5 and extracted with the importer's hardened unzip.

Every download is https-only, goes through the SSRF guard, is atomic (`.part` then rename), is verified (size and, where the source publishes one, MD5) and is skipped when a verified copy
already exists, so an interrupted run resumes. Nothing here labels an image by guesswork: labels come from the source's own metadata through the explicit maps below, and anything the maps do
not cover is excluded and counted."""
from __future__ import annotations

import base64
import concurrent.futures as cf
import csv
import hashlib
import json
import os
import random
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from .. import netguard
from ..config import DATASET_DIR
from . import importer

UA = "CosmoExperts-Importer/1.0 (public-dataset acquisition)"
ISIC_API = "https://api.isic-archive.com/api/v2/"
SCIN_BUCKET = "https://storage.googleapis.com/dx-scin-public-data/"
DATAVERSE = "https://dataverse.harvard.edu/"
DERM_DOI = "doi:10.7910/DVN/DAXZ7P"


class SourceError(Exception):
    pass


# ---------------------------------------------------------------- http
_OPENER = urllib.request.build_opener(importer._HttpsOnlyRedirects)


def _request(url: str, headers: dict | None = None) -> urllib.request.Request:
    if not url.lower().startswith("https://"):
        raise SourceError("Only https URLs are allowed.")
    try:
        netguard.assert_public(url)
    except netguard.Blocked as e:
        raise SourceError(str(e)) from None
    return urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})


def _retry(fn, retries: int = 4, base: float = 1.5):
    last = None
    for i in range(retries):
        try:
            return fn()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                raise SourceError(f"HTTP {e.code} from the source.") from None
            last = e
            limited = isinstance(e, urllib.error.HTTPError) and e.code == 429          # rate limited: wait as long as the server asks, else a generous default
            wait = float(e.headers.get("Retry-After", 0) or 0) if limited else 0.0
            time.sleep(min(max(wait, 25.0 * (i + 1)), 120.0) if limited else base * (2 ** i))
    raise SourceError(f"The source could not be reached: {getattr(last, 'reason', last)}")


def get_json(url: str, timeout: int = 90):
    def go():
        with _OPENER.open(_request(url, {"Accept": "application/json"}), timeout=timeout) as r:
            return json.loads(r.read())
    return _retry(go)


def get_bytes(url: str, timeout: int = 120, limit: int = 50_000_000) -> bytes:
    def go():
        with _OPENER.open(_request(url), timeout=timeout) as r:
            return r.read(limit)
    return _retry(go)


def md5_file(p: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def b64_to_hex(b64: str) -> str:
    return base64.b64decode(b64).hex()


def verified(dest: Path, size: int | None, md5_hex: str | None) -> bool:
    """A file is only ever put at `dest` by an atomic rename after it passed its checks, so with no expectation given an existing non-empty file is a completed download."""
    if not dest.is_file() or dest.stat().st_size == 0:
        return False
    if size is not None and dest.stat().st_size != size:
        return False
    return not md5_hex or md5_file(dest) == md5_hex.lower()


_MD5_ETAG = re.compile(r'^"?([0-9a-fA-F]{32})"?$')


def fetch_file(url: str, dest: Path, *, size: int | None = None, md5_hex: str | None = None, etag_check: bool = False, timeout: int = 120) -> str:
    """Download one small/medium file atomically and verify it. Returns "cached" when a completed copy already exists.
    `etag_check`: the server's ETag is the MD5 of the served object (true for single-part S3 objects); the download must match it. (ISIC publishes a size that is a constant 3,173 bytes
    smaller than the file it serves, so its size field is not used.)"""
    if verified(dest, size, md5_hex):
        return "cached"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    seen = {}

    def go():
        with _OPENER.open(_request(url), timeout=timeout) as r, open(part, "wb") as f:
            seen["etag"] = r.headers.get("ETag", "")
            shutil.copyfileobj(r, f, 1 << 20)
    _retry(go)
    m = _MD5_ETAG.match(seen.get("etag", "")) if etag_check else None
    if m and md5_file(part) != m.group(1).lower():
        part.unlink(missing_ok=True)
        raise SourceError(f"The download of {dest.name} does not match the server's checksum (ETag); it was discarded.")
    if size is not None and part.stat().st_size != size:
        part.unlink(missing_ok=True)
        raise SourceError(f"Size mismatch for {dest.name}: expected {size} bytes.")
    if md5_hex and md5_file(part) != md5_hex.lower():
        part.unlink(missing_ok=True)
        raise SourceError(f"Checksum mismatch for {dest.name}: the download was discarded.")
    part.replace(dest)
    return "downloaded"


def fetch_many(jobs: list[dict], workers: int = 8, should_stop=None, progress=None) -> dict:
    """jobs: [{url, dest, size?, md5_hex?}]. Failures are collected, never hidden; the caller decides whether a partial set is acceptable."""
    done = cached = 0
    failed: list[dict] = []
    nbytes = 0
    t0 = time.time()
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_file, j["url"], Path(j["dest"]), size=j.get("size"), md5_hex=j.get("md5_hex"), etag_check=j.get("etag_check", False)): j for j in jobs}
        for n, fu in enumerate(cf.as_completed(futs), 1):
            j = futs[fu]
            try:
                st = fu.result()
                cached += st == "cached"
                done += st == "downloaded"
                nbytes += Path(j["dest"]).stat().st_size
            except Exception as e:  # noqa: BLE001 - a failed file is a result
                failed.append({"url": j["url"], "error": str(e)[:160]})
            if progress and (n % 200 == 0 or n == len(jobs)):
                progress(f"{n}/{len(jobs)} files ({time.time() - t0:.0f}s, {nbytes / 1e6:.0f} MB, {len(failed)} failed)")
            if should_stop and should_stop():
                for f2 in futs:
                    f2.cancel()
                break
    return {"requested": len(jobs), "downloaded": done, "cached": cached, "failed": failed, "bytes": nbytes}


# ---------------------------------------------------------------- ISIC archive
ISIC_LIC = '(copyright_license:"CC-0" OR copyright_license:"CC-BY")'
ISIC_QUERIES = {
    # high-confidence labels only: histopathology, expert consensus on a single image, or serial imaging showing no change (single-contributor clinical assessment is excluded)
    "dermoscopy": (f'{ISIC_LIC} AND image_type:"dermoscopic" AND -image_manipulation:"altered" AND -image_manipulation:"synthetic" AND '
                   '(diagnosis_1:"Benign" OR diagnosis_1:"Malignant" OR diagnosis_3:"Solar or actinic keratosis") AND '
                   '(diagnosis_confirm_type:"histopathology" OR diagnosis_confirm_type:"single image expert consensus" OR diagnosis_confirm_type:"serial imaging showing no change")'),
    "clinical": (f'{ISIC_LIC} AND (image_type:"clinical: close-up" OR image_type:"clinical: overview") AND -image_manipulation:"altered" AND -image_manipulation:"synthetic" AND '
                 '(diagnosis_1:"Benign" OR diagnosis_1:"Malignant" OR diagnosis_3:"Solar or actinic keratosis")'),
}
# NOTE: a filter requiring image_manipulation:"instrument only" silently drops every record where the field is absent (including all of UFES's), so altered/synthetic are excluded instead.
UFES = "Federal University of Esp"        # the attribution of PAD-UFES-20's own photographs inside ISIC ("Espirito Santo", accent-insensitive prefix)

# diagnosis_3 -> task class. Anything not listed is excluded (and counted), never guessed.
DERMOSCOPY_CLASSES = {
    "Nevus": "NV",
    "Melanoma, NOS": "MEL", "Melanoma in situ": "MEL", "Melanoma Invasive": "MEL",
    "Basal cell carcinoma": "BCC",
    "Squamous cell carcinoma, NOS": "SCC", "Squamous cell carcinoma in situ": "SCC", "Squamous cell carcinoma, Invasive": "SCC",
    "Solar or actinic keratosis": "AK",
    "Seborrheic keratosis": "BKL", "Solar lentigo": "BKL", "Lichen planus like keratosis": "BKL",
    "Dermatofibroma": "DF",
    "Hemangioma": "VASC", "Angiokeratoma": "VASC", "Lymphangioma": "VASC", "Vascular spider": "VASC", "Pyogenic granuloma": "VASC",
}
# the same six classes PAD-UFES-20 uses, so clinical photographs from both sources can be pooled
CLINICAL_CLASSES = {
    "Nevus": "NEV", "Melanoma, NOS": "MEL", "Melanoma in situ": "MEL", "Melanoma Invasive": "MEL", "Basal cell carcinoma": "BCC",
    "Squamous cell carcinoma, NOS": "SCC", "Squamous cell carcinoma in situ": "SCC", "Solar or actinic keratosis": "ACK", "Seborrheic keratosis": "SEK",
}
FST_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}


def isic_compact(r: dict) -> dict:
    md = r.get("metadata", {})
    clin, acq = md.get("clinical", {}), md.get("acquisition", {})
    flat = {}
    for part in md.values():
        if isinstance(part, dict):
            flat.update(part)
    return {"id": r["isic_id"], "license": r["copyright_license"], "attribution": r.get("attribution") or "", "dx1": clin.get("diagnosis_1"), "dx3": clin.get("diagnosis_3"),
            "confirm": clin.get("diagnosis_confirm_type"), "patient": flat.get("patient_id"), "lesion": flat.get("lesion_id"), "fst": clin.get("fitzpatrick_skin_type"),
            "sex": clin.get("sex"), "age": clin.get("age_approx"), "site": clin.get("anatom_site_general") or clin.get("anatom_site_1"), "itype": acq.get("image_type"),
            "full": r["files"]["full"], "thumb": r["files"]["thumbnail_256"], "px": [acq.get("pixels_x"), acq.get("pixels_y")]}


def isic_list(kind: str, cache: Path, page_size: int = 100, progress=None) -> list[dict]:
    """The compact record of every image matching the query. Cached to a JSON-lines file with the cursor, so an interrupted listing resumes where it stopped."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    cur_file = cache.with_suffix(".cursor")
    url = ISIC_API + "images/search/?" + urllib.parse.urlencode({"query": ISIC_QUERIES[kind], "limit": page_size})
    n = 0
    if cache.exists() and cur_file.exists():
        c = cur_file.read_text(encoding="utf-8").strip()
        if c == "DONE":
            return [json.loads(x) for x in cache.read_text(encoding="utf-8").splitlines() if x]
        url = c
    elif cache.exists():
        cache.unlink()
    with open(cache, "a", encoding="utf-8") as f:
        while url:
            if not url.startswith(ISIC_API):
                raise SourceError("The listing pointed outside the ISIC API.")
            j = get_json(url)
            for r in j["results"]:
                if r.get("copyright_license") not in ("CC-0", "CC-BY"):
                    raise SourceError(f"A record outside the eligible licences was returned ({r.get('copyright_license')}); refusing to continue.")
                f.write(json.dumps(isic_compact(r), ensure_ascii=False) + "\n")
                n += 1
            f.flush()
            url = j.get("next")
            cur_file.write_text(url or "DONE", encoding="utf-8")
            if progress and (n % 2000 < page_size):
                progress(f"listed {n} of {j.get('count')}")
    return [json.loads(x) for x in cache.read_text(encoding="utf-8").splitlines() if x]


def isic_select(records: list[dict], kind: str, caps: dict[str, int] | None = None, seed: int = 17) -> tuple[list[dict], dict]:
    """Label, filter and (for over-represented classes) sub-sample. Returns (selected, report). Deterministic for a seed."""
    cmap = DERMOSCOPY_CLASSES if kind == "dermoscopy" else CLINICAL_CLASSES
    rep = {"listed": len(records), "excluded_ufes_pad_ufes_20": 0, "excluded_unmapped_diagnosis": Counter(), "excluded_duplicate_id": 0, "capped": {}}
    seen, kept = set(), []
    for r in records:
        if r["id"] in seen:
            rep["excluded_duplicate_id"] += 1
            continue
        seen.add(r["id"])
        if kind == "clinical" and r["attribution"].startswith(UFES):
            rep["excluded_ufes_pad_ufes_20"] += 1
            continue
        lab = cmap.get(r["dx3"])
        if lab is None:
            rep["excluded_unmapped_diagnosis"][str(r["dx3"])] += 1
            continue
        group = r["patient"] or r["lesion"]
        fst = FST_ROMAN.get(r["fst"] or "")
        kept.append(dict(r, label=lab, group_id=group or r["id"], group_known=bool(group), fst_num=fst))
    rng = random.Random(seed)
    out = []
    by = {}
    for r in kept:
        by.setdefault(r["label"], []).append(r)
    for lab, rs in sorted(by.items()):
        rs.sort(key=lambda r: r["id"])
        cap = (caps or {}).get(lab)
        if cap and len(rs) > cap:
            # prefer images whose patient/lesion is known: they can be split without leakage and report on a leakage-safe test; fill the rest at random
            known = [r for r in rs if r["group_known"]]
            unknown = [r for r in rs if not r["group_known"]]
            rng.shuffle(known)
            rng.shuffle(unknown)
            rs2 = (known + unknown)[:cap]
            rep["capped"][lab] = {"available": len(rs), "kept": cap}
            rs = sorted(rs2, key=lambda r: r["id"])
        out += rs
    rep["excluded_unmapped_diagnosis"] = dict(rep["excluded_unmapped_diagnosis"].most_common(20))
    rep["selected"] = len(out)
    rep["class_counts"] = dict(Counter(r["label"] for r in out))
    rep["attribution"] = dict(Counter(f'{r["license"]} | {r["attribution"]}' for r in out).most_common(15))
    rep["group_known"] = sum(r["group_known"] for r in out)
    rep["fitzpatrick"] = dict(sorted(Counter(r["fst_num"] or 0 for r in out).items()))
    return out, rep


def build_isic(kind: str, name: str, caps: dict[str, int] | None = None, seed: int = 17, workers: int = 12, progress=None, should_stop=None) -> dict:
    """List, select, download and lay out an ISIC subset under DATASET_DIR/<name>/ (images/, labels.csv, attribution.csv, selection.json)."""
    root = DATASET_DIR / name
    recs = isic_list(kind, root / "listing.jsonl", progress=progress)
    sel, rep = isic_select(recs, kind, caps, seed)
    use_full = kind == "clinical"
    jobs, rows = [], []
    for r in sel:
        f = r["full"] if use_full else r["thumb"]
        fn = f"{r['id']}.jpg"
        jobs.append({"url": f["url"], "dest": root / "images" / fn, "etag_check": True})
        rows.append(r | {"filename": fn})
    res = fetch_many(jobs, workers=workers, progress=progress, should_stop=should_stop)
    bad = {Path(j["url"]).stem.replace("_thumbnail", "") for j in res["failed"]}
    rows = [r for r in rows if r["id"] not in bad]
    with open(root / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["filename", "label", "group_id", "fitzpatrick", "source"])
        for r in rows:
            w.writerow([r["filename"], r["label"], r["group_id"], r["fst_num"] or "", f"isic:{r['license']}:{r['confirm']}:{'group' if r['group_known'] else 'nogroup'}"])
    with open(root / "attribution.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["filename", "isic_id", "license", "attribution", "image_type", "diagnosis_3", "confirm_type", "image_url"])
        for r in rows:
            w.writerow([r["filename"], r["id"], r["license"], r["attribution"], r["itype"], r["dx3"], r["confirm"], (r["full"] if use_full else r["thumb"])["url"]])
    rep.update(kind=kind, image_files="full-size" if use_full else "official 256 px thumbnails", download=res | {"failed": res["failed"][:10], "failed_count": len(res["failed"])},
               query=ISIC_QUERIES[kind], seed=seed, caps=caps or {})
    (root / "selection.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return rep


# ---------------------------------------------------------------- SCIN
SCIN_MIN_WEIGHT = 0.5          # the leading condition must carry at least half of the dermatologists' weighted opinion
SCIN_MIN_CASES = 40            # a condition needs this many qualifying cases to become a class (smaller ones are excluded and listed, not merged into "other")


def scin_listing() -> dict[str, dict]:
    out = {}
    url = "https://storage.googleapis.com/storage/v1/b/dx-scin-public-data/o?" + urllib.parse.urlencode({"prefix": "dataset/", "delimiter": "/", "fields": "items(name,size,md5Hash)"})
    for it in get_json(url).get("items", []):
        out[it["name"]] = {"size": int(it["size"]), "md5_hex": b64_to_hex(it["md5Hash"])}
    return out


def scin_object(name: str) -> dict:
    url = "https://storage.googleapis.com/storage/v1/b/dx-scin-public-data/o/" + urllib.parse.quote(name, safe="") + "?fields=name,size,md5Hash"
    j = get_json(url)
    return {"size": int(j["size"]), "md5_hex": b64_to_hex(j["md5Hash"])}


def scin_tables(root: Path) -> tuple[list[dict], list[dict], dict]:
    meta = {}
    for fn in ("scin_cases.csv", "scin_labels.csv"):
        info = scin_listing()[f"dataset/{fn}"]
        fetch_file(SCIN_BUCKET + f"dataset/{fn}", root / "raw" / fn, size=info["size"], md5_hex=info["md5_hex"])
        meta[fn] = {"sha256": importer.sha256_file(root / "raw" / fn), "md5": info["md5_hex"], "bytes": info["size"]}
    cases = list(csv.DictReader(open(root / "raw" / "scin_cases.csv", encoding="utf-8")))
    labels = {r["case_id"]: r for r in csv.DictReader(open(root / "raw" / "scin_labels.csv", encoding="utf-8"))}
    return cases, labels, meta


def scin_select(cases: list[dict], labels: dict[str, dict], min_weight: float = SCIN_MIN_WEIGHT, min_cases: int = SCIN_MIN_CASES) -> tuple[list[dict], dict]:
    """One row per image. A case qualifies when a dermatologist-assigned weighted label exists and its leading condition has weight >= min_weight. Conditions with fewer than
    `min_cases` qualifying cases are not classes. Skin tone = the modal dermatologist Fitzpatrick estimate (up to three raters), else none."""
    import ast
    qual, report = [], {"cases": len(cases), "no_weighted_label": 0, "leading_weight_below_threshold": 0}
    for c in cases:
        l = labels.get(c["case_id"])
        if not l or not l.get("weighted_skin_condition_label"):
            report["no_weighted_label"] += 1
            continue
        try:
            w = ast.literal_eval(l["weighted_skin_condition_label"])
        except (ValueError, SyntaxError):
            report["no_weighted_label"] += 1
            continue
        if not w:
            report["no_weighted_label"] += 1
            continue
        top = max(w, key=w.get)
        if w[top] < min_weight:
            report["leading_weight_below_threshold"] += 1
            continue
        fs = [l[f"dermatologist_fitzpatrick_skin_type_label_{i}"] for i in (1, 2, 3) if l.get(f"dermatologist_fitzpatrick_skin_type_label_{i}", "").startswith("FST")]
        fst = int(Counter(fs).most_common(1)[0][0][3]) if fs else None
        mst = l.get("monk_skin_tone_label_us") or ""
        qual.append({"case": c, "label": top, "weight": w[top], "fst": fst, "monk": int(mst) if mst.isdigit() else None})
    counts = Counter(q["label"] for q in qual)
    keep = {k for k, v in counts.items() if v >= min_cases}
    report["conditions_with_enough_cases"] = {k: counts[k] for k in sorted(keep, key=lambda k: -counts[k])}
    report["conditions_excluded_too_few_cases"] = {k: v for k, v in counts.most_common() if k not in keep}
    rows = []
    for q in qual:
        if q["label"] not in keep:
            continue
        for i in (1, 2, 3):
            p = q["case"].get(f"image_{i}_path")
            if p:
                rows.append({"case_id": q["case"]["case_id"], "path": p, "label": q["label"], "weight": q["weight"], "fst": q["fst"], "monk": q["monk"], "slot": i,
                             "shot": q["case"].get(f"image_{i}_shot_type", "")})
    report["images"] = len(rows)
    report["qualifying_cases"] = sum(1 for q in qual if q["label"] in keep)
    return rows, report


def build_scin(name: str = "scin", workers: int = 8, progress=None, should_stop=None) -> dict:
    root = DATASET_DIR / name
    cases, labels, meta = scin_tables(root)
    rows, rep = scin_select(cases, labels)
    listing = scin_listing_for(rows)
    jobs, missing_in_bucket = [], []
    for r in rows:
        info = listing.get(r["path"])
        if info is None:
            missing_in_bucket.append(r["path"])
            continue
        r["filename"] = Path(r["path"]).name
        jobs.append({"url": SCIN_BUCKET + r["path"], "dest": root / "images" / r["filename"], "size": info["size"], "md5_hex": info["md5_hex"]})
    res = fetch_many(jobs, workers=workers, progress=progress, should_stop=should_stop)
    bad = {Path(j["url"]).name for j in res["failed"]}
    rows = [r for r in rows if r.get("filename") and r["filename"] not in bad]
    with open(root / "labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["filename", "label", "group_id", "fitzpatrick", "source"])
        for r in rows:
            w.writerow([r["filename"], r["label"], r["case_id"], r["fst"] or "", f"scin:weight={r['weight']}"])
    with open(root / "image_metadata.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["filename", "case_id", "label", "leading_weight", "dermatologist_fitzpatrick", "monk_skin_tone_us", "shot_type"])
        for r in rows:
            w.writerow([r["filename"], r["case_id"], r["label"], r["weight"], r["fst"] or "", r["monk"] or "", r["shot"]])
    rep.update(tables=meta, missing_in_bucket=len(missing_in_bucket), download=res | {"failed": res["failed"][:10], "failed_count": len(res["failed"])}, min_weight=SCIN_MIN_WEIGHT, min_cases=SCIN_MIN_CASES,
               monk_distribution=dict(sorted(Counter(r["monk"] or 0 for r in rows).items())), fitzpatrick_distribution=dict(sorted(Counter(r["fst"] or 0 for r in rows).items())))
    (root / "selection.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return rep


def scin_listing_for(rows: list[dict]) -> dict[str, dict]:
    """Size and MD5 of exactly the image objects needed, from the bucket's own listing (paged)."""
    need = {r["path"] for r in rows}
    out, token = {}, None
    while True:
        q = {"prefix": "dataset/images/", "maxResults": 1000, "fields": "items(name,size,md5Hash),nextPageToken"}
        if token:
            q["pageToken"] = token
        j = get_json("https://storage.googleapis.com/storage/v1/b/dx-scin-public-data/o?" + urllib.parse.urlencode(q))
        for it in j.get("items", []):
            if it["name"] in need:
                out[it["name"]] = {"size": int(it["size"]), "md5_hex": b64_to_hex(it["md5Hash"])}
        token = j.get("nextPageToken")
        if not token:
            return out


# ---------------------------------------------------------------- DERM12345
DERM_CLASS_BY_MAIN = {"banal": "NV", "dysplastic": "NV", "melanoma": "MEL"}
DERM_CLASS_BY_LABEL = {"bcc": "BCC", "scc": "SCC", "bd": "SCC", "ak": "AK", "sk": "BKL", "sl": "BKL", "lk": "BKL", "df": "DF",
                       "ha": "VASC", "angk": "VASC", "sa": "VASC", "pg": "VASC", "la": "VASC"}


def derm_class(row: dict) -> str | None:
    """DERM12345 code -> the dermoscopy task's classes (same definitions as DERMOSCOPY_CLASSES). Codes outside the map (cutaneous horn, Paget, DFSP, Kaposi, lentigo simplex, ink-spot lentigo) are excluded."""
    return DERM_CLASS_BY_LABEL.get(row["label"]) or DERM_CLASS_BY_MAIN.get(row["main_class_1"])


def derm_files() -> dict[str, dict]:
    j = get_json(DATAVERSE + "api/datasets/:persistentId/?" + urllib.parse.urlencode({"persistentId": DERM_DOI}))["data"]["latestVersion"]
    if (j.get("license") or {}).get("name") != "CC BY 4.0":
        raise SourceError("The repository no longer states CC BY 4.0 for this dataset; refusing to continue until the terms are re-checked.")
    return {f["dataFile"]["filename"]: f["dataFile"] for f in j["files"]}


def build_derm12345(name: str = "derm12345", nv_cap: int = 2500, seed: int = 17, progress=None, extract: bool = True) -> dict:
    root = DATASET_DIR / name
    files = derm_files()
    raw = root / "raw"
    for fn in ("derm12345_metadata_train.tab", "derm12345_metadata_test.tab"):
        d = files[fn]
        dest = raw / fn
        if not dest.is_file() or md5_file(dest) != d["md5"]:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(get_bytes(DATAVERSE + f"api/access/datafile/{d['id']}?format=original"))
        if md5_file(dest) != d["md5"]:
            raise SourceError(f"Checksum mismatch for {fn}.")
    rows = []
    for fn in ("derm12345_metadata_train.tab", "derm12345_metadata_test.tab"):
        rows += list(csv.DictReader(open(raw / fn, encoding="utf-8-sig")))
    bad_lic = [r["image_id"] for r in rows if r["copyright-license"] != "CC-BY"]
    if bad_lic:
        raise SourceError(f"{len(bad_lic)} images carry a licence other than CC-BY; refusing to continue.")
    train_p = {r["patient_id"] for r in rows if r["split"] == "train"}
    cross = {r["patient_id"] for r in rows if r["split"] == "test"} & train_p
    rep = {"rows": len(rows), "patients": len({r["patient_id"] for r in rows}), "patients_in_both_official_splits": sorted(cross)}
    sel = []
    ex = Counter()
    for r in rows:
        c = derm_class(r)
        if c is None:
            ex[r["label"]] += 1
            continue
        sel.append(dict(r, label_task=c))
    by = {}
    for r in sel:
        by.setdefault(r["label_task"], []).append(r)
    rng = random.Random(seed)
    out = []
    for c, rs in sorted(by.items()):
        rs.sort(key=lambda r: r["image_id"])
        if c == "NV" and len(rs) > nv_cap:
            rng.shuffle(rs)
            rs = sorted(rs[:nv_cap], key=lambda r: r["image_id"])
        out += rs
    rep.update(excluded_codes=dict(ex), selected=len(out), class_counts=dict(Counter(r["label_task"] for r in out)), nv_cap=nv_cap, seed=seed)
    if extract:
        for fn in ("derm12345_train_part_1.zip", "derm12345_train_part_2.zip", "derm12345_test.zip"):
            d = files[fn]
            z = raw / fn
            if not z.is_file() or md5_file(z) != d["md5"]:
                importer.download(DATAVERSE + f"api/access/datafile/{d['id']}", z, None, name, max_bytes=4 * 1024 ** 3, on_progress=None)
                if md5_file(z) != d["md5"]:
                    z.unlink(missing_ok=True)
                    raise SourceError(f"Checksum mismatch for {fn}: discarded.")
            if progress:
                progress(f"{fn} verified (md5 {d['md5']})")
        ext = root / "extracted"
        if not ext.exists() or not any(ext.rglob("*.jpg")) and not any(ext.rglob("*.png")):
            for fn in ("derm12345_train_part_1.zip", "derm12345_train_part_2.zip", "derm12345_test.zip"):
                importer.safe_unzip(raw / fn, ext)
        index = {p.stem: p for p in ext.rglob("*") if p.suffix.lower() in importer.IMG_EXT}
        imgs = root / "images"
        imgs.mkdir(parents=True, exist_ok=True)
        rows_out, missing = [], []
        for r in out:
            p = index.get(r["image_id"])
            if p is None:
                missing.append(r["image_id"])
                continue
            dst = imgs / p.name
            if not dst.exists():
                try:
                    os.link(p, dst)
                except OSError:
                    shutil.copy2(p, dst)
            rows_out.append(r | {"filename": p.name})
        with open(root / "labels.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["filename", "label", "group_id", "fitzpatrick", "source"])
            for r in rows_out:
                w.writerow([r["filename"], r["label_task"], r["patient_id"], "", f"derm12345:{r['split']}:{r['label']}"])
        with open(root / "attribution.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["filename", "image_id", "license", "official_split", "code", "super_class", "malignancy"])
            for r in rows_out:
                w.writerow([r["filename"], r["image_id"], r["copyright-license"], r["split"], r["label"], r["super_class"], r["malignancy"]])
        rep.update(images_found=len(rows_out), images_missing_from_archives=len(missing))
    (root / "selection.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return rep

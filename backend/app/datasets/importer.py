"""Controlled ingestion. Only runs for datasets that pass the licence gate (or explicit evaluation-only
override by an admin, recorded in the audit log). Features: disk-space check, resumable HTTP download with
Range, sha256 checksums, image validation, exact + perceptual duplicate detection, manifests, cancellation."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .. import db, netguard
from ..config import DATASET_DIR

MIN_FREE_BYTES = 2 * 1024 ** 3
_cancel: dict[str, threading.Event] = {}
IMG_EXT = {".jpg", ".jpeg", ".png"}


class ImportError_(Exception):
    pass


def cancel(dataset_id: str):
    _cancel.setdefault(dataset_id, threading.Event()).set()


def check_disk(needed: int = 0):
    free = shutil.disk_usage(DATASET_DIR).free
    if free - needed < MIN_FREE_BYTES:
        raise ImportError_(f"Not enough free disk space ({free / 1e9:.1f} GB free; need {(needed + MIN_FREE_BYTES) / 1e9:.1f} GB).")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dhash(img: Image.Image) -> str:
    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _local_http_allowed(url: str) -> bool:
    """Test hook only: plain http to the loopback interface, and only when COSMO_ALLOW_LOCAL_HTTP=1 is set explicitly."""
    from urllib.parse import urlparse
    u = urlparse(url)
    return os.environ.get("COSMO_ALLOW_LOCAL_HTTP") == "1" and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect to a non-https location (a download must not be downgraded to plaintext)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://") and not _local_http_allowed(newurl):
            raise ImportError_("Refusing a redirect to a non-https URL.")
        try:
            netguard.assert_public(newurl)                      # a redirect must not lead to an internal address either
        except netguard.Blocked as e:
            raise ImportError_(str(e)) from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, dest: Path, expected_sha256: str | None, dataset_id: str, max_bytes: int = 5 * 1024 ** 3, on_progress=None) -> Path:
    """Resumable download of an admin-supplied permitted URL. Verifies the checksum if provided; a mismatch discards the file."""
    if not url.lower().startswith("https://") and not _local_http_allowed(url):
        raise ImportError_("Only https URLs are allowed.")
    try:
        netguard.assert_public(url)
    except netguard.Blocked as e:
        raise ImportError_(str(e)) from None
    ev = _cancel.setdefault(dataset_id, threading.Event())
    ev.clear()
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "CosmoExperts-Importer/1.0"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    opener = urllib.request.build_opener(_HttpsOnlyRedirects)
    try:
        r = opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:      # the partial file already holds the whole resource: just verify it
            r = None
        else:
            raise ImportError_(f"The server refused the download (HTTP {e.code}).")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ImportError_(f"Could not reach the server: {getattr(e, 'reason', e)}")
    if r is not None:
        with r:
            total = int(r.headers.get("Content-Length", 0)) + (have if r.status == 206 else 0)
            if total and total > max_bytes:
                raise ImportError_("Dataset is larger than the configured maximum; use the external compute adapter.")
            check_disk(max(total - have, 0))
            mode = "ab" if r.status == 206 else "wb"      # a server that ignores Range sends 200 + the whole file: start over
            with open(part, mode) as f:
                while True:
                    if ev.is_set():
                        raise ImportError_("Download cancelled. Progress is kept and can be resumed.")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    if f.tell() > max_bytes:         # enforced while streaming: Content-Length may be absent or wrong
                        f.close()
                        part.unlink(missing_ok=True)
                        raise ImportError_("Download exceeded the configured maximum size and was discarded.")
                    if on_progress:
                        on_progress(f.tell(), total)
    digest = sha256_file(part)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        part.unlink(missing_ok=True)
        raise ImportError_("Checksum mismatch: the downloaded file was discarded.")
    part.replace(dest)
    return dest


def ingest_folder(dataset_id: str, folder: Path, actor: str, labels_csv: Path | None = None, limit: int | None = None) -> dict:
    """Validate a local folder of images (already obtained under the dataset's terms) into a versioned manifest.
    labels_csv columns: filename,label[,group_id][,fitzpatrick][,source]"""
    check_disk()
    meta: dict[str, dict] = {}
    if labels_csv:
        with open(labels_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                meta[row["filename"]] = row
    ev = _cancel.setdefault(dataset_id, threading.Event())
    ev.clear()
    entries, errors, seen_sha, seen_hash, dups, conflicts, near = [], [], {}, [], [], [], []
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMG_EXT)
    if limit:
        files = files[:limit]
    by_file: dict[str, dict] = {}
    for p in files:
        if ev.is_set():
            errors.append({"file": str(p), "error": "cancelled"})
            break
        try:
            with Image.open(p) as im:
                im.load()
                ph = dhash(im)
                w, h = im.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
            errors.append({"file": p.name, "error": f"cannot decode: {e}"})
            continue
        sha = sha256_file(p)
        m = meta.get(p.name, {})
        rel = str(p.relative_to(folder)).replace("\\", "/")
        label = m.get("label")
        if sha in seen_sha:
            first = seen_sha[sha]
            fe = by_file.get(first)
            if fe is not None and fe["label"] and label and fe["label"] != label:
                # byte-identical image filed under two different diagnoses: ground truth is ambiguous, so exclude BOTH
                conflicts.append({"files": [first, rel], "labels": [fe["label"], label], "groups": [fe["group_id"], m.get("group_id") or m.get("patient_id")]})
                fe["_conflict"] = True
            else:
                dups.append({"file": p.name, "duplicate_of": first, "type": "exact"})
            continue
        nearest = next((e["file"] for e in seen_hash if hamming(e["phash"], ph) <= 2), None)
        if nearest:
            near.append({"file": rel, "near": nearest})   # flagged only: a perceptual hash over-matches low-texture images, so it never deletes data
        e = {"file": rel, "sha256": sha, "phash": ph, "width": w, "height": h, "label": label,
             "group_id": m.get("group_id") or m.get("patient_id") or sha[:12],
             "fitzpatrick": m.get("fitzpatrick"), "label_provenance": m.get("source", "unspecified")}
        seen_sha[sha] = rel
        by_file[rel] = e
        seen_hash.append({"file": rel, "phash": ph})
        entries.append(e)
    entries = [e for e in entries if not e.get("_conflict")]
    version = (db.q1("SELECT COUNT(*) c FROM dataset_versions WHERE dataset_id=?", (dataset_id,)) or {"c": 0})["c"] + 1
    manifest = {"dataset_id": dataset_id, "version": version, "root": str(folder), "count": len(entries),
                "duplicates_skipped": dups, "label_conflicts_excluded": conflicts, "near_duplicates_flagged": near, "errors": errors, "entries": entries}
    vid = db.uid()
    db.x("INSERT INTO dataset_versions VALUES(?,?,?,?)", (vid, dataset_id, json.dumps(manifest), db.now()))
    db.audit(actor, "dataset.import", dataset_id, {"version": version, "count": len(entries), "dups": len(dups), "label_conflicts": len(conflicts), "near_flagged": len(near), "errors": len(errors)})
    return {"version_id": vid, "version": version, "count": len(entries), "duplicates_skipped": len(dups), "label_conflicts_excluded": len(conflicts),
            "near_duplicates_flagged": len(near), "errors": errors}


def make_splits(entries: list[dict], seed: int = 17, fractions=(0.7, 0.15, 0.15)) -> dict:
    """Group-aware, deterministic split: all images sharing a group_id (patient/lesion) land in one split."""
    def bucket(g: str) -> str:
        x = int(hashlib.sha256(f"{seed}:{g}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return "train" if x < fractions[0] else "val" if x < fractions[0] + fractions[1] else "test"
    out = {"train": [], "val": [], "test": []}
    for e in entries:
        out[bucket(e["group_id"])].append(e)
    groups = {k: {e["group_id"] for e in v} for k, v in out.items()}
    assert not (groups["train"] & groups["val"] or groups["train"] & groups["test"] or groups["val"] & groups["test"]), "split leakage"
    return out


def safe_unzip(zip_path: Path, dest: Path) -> int:
    """Extract only regular image/csv members, rejecting path traversal and absolute paths."""
    import zipfile
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            target = (dest / info.filename).resolve()
            if root not in target.parents and target != root:
                raise ImportError_(f"Unsafe path in archive: {info.filename}")
            if target.suffix.lower() not in IMG_EXT | {".csv", ".txt"}:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    return n

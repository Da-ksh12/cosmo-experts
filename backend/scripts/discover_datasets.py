"""Read-only discovery of openly published dermatology image datasets with a machine-readable licence.

    python -m scripts.discover_datasets [--out data/dataset_discovery_raw.json]

It queries the public search APIs of Zenodo, Figshare, Harvard Dataverse and Hugging Face for a fixed list of skin, hair and scalp keywords and records, for every hit, the title, the licence the
publisher states, the file sizes and whether any file looks like an image archive. Nothing is downloaded. The licence is only the *publisher's* statement: a candidate that survives this filter still
has to pass the registry's licence gate (official terms snapshot, provenance, labels) before anything is imported, and a hit that is not openly licensed is recorded as excluded with the reason.

Fixed hosts only; each request has a timeout; a failing source is reported, never hidden."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "CosmoExperts-DatasetDiscovery/1.0 (read-only licence survey)", "Accept": "application/json"}
KEYWORDS = ["acne", "alopecia", "hair loss", "scalp", "trichoscopy", "dermatology images", "skin lesion", "melasma", "vitiligo", "psoriasis", "eczema", "rosacea", "hyperpigmentation",
            "skin disease images", "dermoscopy", "scar keloid", "facial skin", "skin tone"]
PERMISSIVE = {"cc0-1.0", "cc-0", "cc0", "cc-by-4.0", "cc-by-3.0", "cc-by-2.0", "mit", "apache-2.0", "bsd-3-clause", "odc-by", "pddl", "cc by 4.0", "cc by 3.0", "cc0 1.0", "creative commons attribution 4.0 international"}
IMAGE_EXT = re.compile(r"\.(zip|tar|tgz|gz|7z|rar|jpe?g|png|tiff?|bmp|webp)$", re.I)


def http_json(url: str, data: dict | None = None, timeout: int = 40):
    body = json.dumps(data).encode() if data is not None else None
    headers = dict(UA, **({"Content-Type": "application/json"} if data is not None else {}))
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def norm_lic(s: str) -> str:
    return (s or "").strip().lower()


def permissive(lic: str) -> bool:
    l = norm_lic(lic)
    return l in PERMISSIVE or l.startswith("cc-by-4") or l.startswith("cc0")


def zenodo(q: str):
    j = http_json("https://zenodo.org/api/records?" + urllib.parse.urlencode({"q": q, "type": "dataset", "size": 25, "access_right": "open", "sort": "bestmatch"}))
    for h in j.get("hits", {}).get("hits", []):
        m = h.get("metadata", {})
        files = h.get("files", []) or []
        yield dict(source="zenodo", id=str(h.get("id")), title=m.get("title", ""), url=h.get("links", {}).get("self_html") or f"https://zenodo.org/records/{h.get('id')}",
                   license=(m.get("license") or {}).get("id", ""), files=len(files), bytes=sum(f.get("size", 0) for f in files), has_images=any(IMAGE_EXT.search(f.get("key", "")) for f in files),
                   doi=m.get("doi", ""), published=m.get("publication_date", ""))


def figshare(q: str):
    hits = http_json("https://api.figshare.com/v2/articles/search", {"search_for": q, "item_type": 3, "page_size": 25})
    for a in hits:
        try:
            d = http_json(f"https://api.figshare.com/v2/articles/{a['id']}")
        except Exception:
            continue
        files = d.get("files", []) or []
        yield dict(source="figshare", id=str(a["id"]), title=d.get("title", ""), url=d.get("figshare_url", ""), license=(d.get("license") or {}).get("name", ""), files=len(files),
                   bytes=sum(f.get("size", 0) for f in files), has_images=any(IMAGE_EXT.search(f.get("name", "")) for f in files), doi=d.get("doi", ""), published=(d.get("published_date") or "")[:10])


def dataverse(q: str):
    j = http_json("https://dataverse.harvard.edu/api/search?" + urllib.parse.urlencode({"q": q, "type": "dataset", "per_page": 20}))
    for it in j.get("data", {}).get("items", []):
        gid = it.get("global_id", "")
        lic = ""
        try:
            d = http_json("https://dataverse.harvard.edu/api/datasets/:persistentId/?" + urllib.parse.urlencode({"persistentId": gid}))
            v = d.get("data", {}).get("latestVersion", {})
            lic = (v.get("license") or {}).get("name", "") if isinstance(v.get("license"), dict) else str(v.get("license") or "")
            files = v.get("files", []) or []
        except Exception:
            files = []
        yield dict(source="dataverse", id=gid, title=it.get("name", ""), url=it.get("url", ""), license=lic, files=len(files),
                   bytes=sum((f.get("dataFile") or {}).get("filesize", 0) for f in files), has_images=any(IMAGE_EXT.search((f.get("dataFile") or {}).get("filename", "")) for f in files),
                   doi=gid, published=(it.get("published_at") or "")[:10])


def huggingface(q: str):
    j = http_json("https://huggingface.co/api/datasets?" + urllib.parse.urlencode({"search": q, "limit": 25, "full": "true"}))
    for d in j:
        card = d.get("cardData") or {}
        lic = card.get("license", "")
        if isinstance(lic, list):
            lic = ",".join(lic)
        yield dict(source="huggingface", id=d.get("id", ""), title=d.get("id", ""), url=f"https://huggingface.co/datasets/{d.get('id', '')}", license=lic or "", files=len(d.get("siblings") or []),
                   bytes=0, has_images=any("image" in str(t) for t in (d.get("tags") or [])), doi="", published=(d.get("createdAt") or "")[:10], downloads=d.get("downloads"))


SOURCES = {"zenodo": zenodo, "figshare": figshare, "dataverse": dataverse, "huggingface": huggingface}
RELEVANT = re.compile(r"acne|alopecia|hair|scalp|trichosc|dermat|skin|melasma|vitiligo|psoria|eczema|rosacea|pigment|lesion|scar|keloid|mole|nevus|melanom|derm", re.I)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "dataset_discovery_raw.json")      # raw output stays out of Git; docs/eval/dataset_discovery.json is the curated record of what was read and decided
    ap.add_argument("--keywords", default="")
    a = ap.parse_args(argv)
    kws = [k.strip() for k in a.keywords.split(",") if k.strip()] or KEYWORDS
    found: dict[tuple, dict] = {}
    errors: list[dict] = []
    for name, fn in SOURCES.items():
        for kw in kws:
            try:
                for c in fn(kw):
                    key = (c["source"], c["id"])
                    if key in found:
                        found[key]["matched"].append(kw)
                    else:
                        found[key] = dict(c, matched=[kw])
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
                errors.append({"source": name, "keyword": kw, "error": f"{type(e).__name__}: {str(e)[:120]}"})
            time.sleep(1.1)                                           # stay well inside the public rate limits
    cands = []
    for c in found.values():
        c["relevant_title"] = bool(RELEVANT.search(c["title"]))
        c["licence_permissive"] = permissive(c["license"])
        cands.append(c)
    cands.sort(key=lambda c: (not c["licence_permissive"], not c["relevant_title"], c["source"], c["title"]))
    rep = {"schema": "dataset-discovery-1", "searched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "keywords": kws, "sources": list(SOURCES),
           "note": "The licence is the publisher's statement only. Nothing here is downloaded or approved; a candidate must still pass the registry's licence gate (official terms, provenance, labels).",
           "counts": {"hits": len(cands), "permissive_licence": sum(c["licence_permissive"] for c in cands), "permissive_and_relevant_title": sum(c["licence_permissive"] and c["relevant_title"] for c in cands)},
           "errors": errors, "candidates": cands}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rep["counts"]), "errors:", len(errors), "->", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

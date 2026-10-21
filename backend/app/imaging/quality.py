"""Task-specific image quality assessment, layered ON TOP of the existing suitability gate (which is unchanged and still decides rejection for focus, exposure, size,
subject and region).

Adds measurable checks, each with its value, threshold, basis and a `validated` flag:
  resolution   recommended resolution (advisory)
  compression  estimated JPEG quality from the quantisation tables, plus blockiness (advisory)
  obstruction  a large dark / bright / featureless region that touches the frame edge: a finger, lens cover, strap or glare patch
  framing      faces must not be cut off by the frame (advisory). A skin-in-the-centre check for close-ups was evaluated and NOT shipped: it detected a shifted subject in only
               2 % of simulated cases (criterion 80 %), and skin-colour masks are less reliable on darker skin (docs/eval/quality_v2.json, results.center_skin)
  document     the photo looks like a page of text (routes to the document pipeline instead of any image model)

RULE (also asserted by the tests): only a check marked validated=True may turn a usable photo into `not_usable`. A check is validated only if eval/quality_v2_eval.py measured
BOTH its false-flag rate on real, usable photographs and its detection rate on simulated faults, and both met the stated criteria; the recorded numbers are in
docs/eval/quality_v2.json. Everything else is a labelled advisory warning. Quality rules fitted on lesion close-ups are never applied as rejections to faces or hair/scalp."""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import suitability

VERSION = "quality-2.0.0"
CATEGORIES = ("face", "hair_scalp", "body", "other", "skin_lesion", "medical_report_image")
REGION_FOR = {"face": "face", "hair_scalp": "hair_scalp", "body": "body", "other": "other", "skin_lesion": "body"}
PROFILE_FOR = {"face": "default", "hair_scalp": "hair_closeup", "body": "skin_closeup", "other": "skin_closeup", "skin_lesion": "skin_closeup"}

# Thresholds. `basis` records where each came from; see docs/eval/quality_v2.json for the measurements behind the validated ones.
RECOMMENDED_MIN_SIDE = 400            # advisory default (not validated): more pixels help the clinician see detail; fires on about 13 % of PAD-UFES-20 photographs (min side 10th percentile 371 px, 25th 526 px)
JPEG_QUALITY_WARN = 60.0              # advisory default
OBSTRUCTION_WARN = 0.12               # replaced by the measured value below when the evaluation file exists
CENTER_SKIN_WARN = 0.15
DOCUMENT_MIN_WORDS = 15

_EVAL_FILE = Path(__file__).resolve().parents[3] / "docs" / "eval" / "quality_v2.json"


def _calibration() -> dict:
    try:
        return json.loads(_EVAL_FILE.read_text(encoding="utf-8")).get("calibration", {})
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- measurements
_STD_LUM_SUM = 16 + 11 + 10 + 16 + 24 + 40 + 51 + 61 + 12 + 12 + 14 + 19 + 26 + 58 + 60 + 55 + 14 + 13 + 16 + 24 + 40 + 57 + 69 + 56 + 14 + 17 + 22 + 29 + 51 + 87 + 80 + 62 + \
    18 + 22 + 37 + 56 + 68 + 109 + 103 + 77 + 24 + 35 + 55 + 64 + 81 + 104 + 113 + 92 + 49 + 64 + 78 + 87 + 103 + 121 + 120 + 101 + 72 + 92 + 95 + 98 + 112 + 100 + 103 + 99


def estimate_jpeg_quality(data: bytes) -> float | None:
    """Estimated IJG quality (1-100) from the luminance quantisation table, or None when the file is not a JPEG. It is an estimate: cameras may use their own tables, and the
    ratio of table sums is order-independent but saturates near 100 (tables of all ones)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "JPEG":
                return None
            tbl = im.quantization.get(0)
    except Exception:
        return None
    if not tbl or len(tbl) != 64:
        return None
    s = 100.0 * sum(tbl) / _STD_LUM_SUM
    q = (200.0 - s) / 2.0 if s <= 100 else 5000.0 / s
    return round(max(1.0, min(100.0, q)), 1)


def _small(img: np.ndarray, long_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = long_side / max(h, w)
    return cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else img


def _block_mean(a: np.ndarray, b: int) -> np.ndarray:
    h, w = (a.shape[0] // b) * b, (a.shape[1] // b) * b
    return a[:h, :w].reshape(h // b, b, w // b, b).mean(axis=(1, 3))


def obstruction_region(img: np.ndarray) -> tuple[float, list | None]:
    """Largest frame-edge-touching region made of blocks that are very dark, very bright and colourless, or far less textured than the rest of the photo, as (fraction of the frame,
    bounding box [x, y, w, h] normalised to the photo, or None when there is no such region). A finger over the lens, a lens cap or a flash-glare patch produces such a region;
    ordinary skin, hair and backgrounds usually do not. The box is the measured extent of that region on an 8-pixel block grid, not an estimate."""
    g = cv2.cvtColor(_small(img, 192), cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(_small(img, 192), cv2.COLOR_BGR2HSV)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    tex = _block_mean(np.sqrt(gx * gx + gy * gy), 8)
    mean = _block_mean(g.astype(np.float32), 8)
    sat = _block_mean(hsv[..., 1].astype(np.float32), 8)
    med = float(np.median(tex)) or 1.0
    mask = (mean < 35) | ((mean > 240) & (sat < 30)) | (tex < 0.12 * med)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=4)
    H, W = mask.shape
    best, box = 0, None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if (x == 0 or y == 0 or x + w == W or y + h == H) and area > best:
            best, box = int(area), [round(float(x) / W, 4), round(float(y) / H, 4), round(float(w) / W, 4), round(float(h) / H, 4)]
    return round(best / float(H * W), 4), box


def obstruction_fraction(img: np.ndarray) -> float:
    """The share of the frame covered by the largest frame-edge-touching region (see obstruction_region)."""
    return obstruction_region(img)[0]


def center_skin_fraction(img: np.ndarray) -> float:
    """Share of skin-coloured pixels in the central half of the frame (width and height)."""
    sm = suitability.skin_mask(img)
    h, w = sm.shape
    c = sm[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    return round(float(c.mean()) if c.size else 0.0, 4)


def face_framing(img: np.ndarray, faces: list[tuple]) -> dict:
    """Whether the single detected face is inside the frame and roughly centred."""
    if len(faces) != 1:
        return {"cut_off": False, "offset": None}
    x, y, w, h = faces[0]
    H, W = img.shape[:2]
    m = 0.02
    cut = x < m * W or y < m * H or x + w > (1 - m) * W or y + h > (1 - m) * H
    off = float(np.hypot((x + w / 2) / W - 0.5, (y + h / 2) / H - 0.5))
    return {"cut_off": bool(cut), "offset": round(off, 3)}


def document_words(img: np.ndarray) -> int:
    """Number of confidently read words, but only for photos that pass a cheap white-page pre-filter (OCR costs seconds; ordinary skin photos never pay it)."""
    g = cv2.cvtColor(_small(img, 1200), cv2.COLOR_BGR2GRAY)
    if float((g > 170).mean()) < 0.45:
        return 0
    try:
        import pytesseract
        from ..documents import pipeline
        if not pipeline.ocr_available():
            return 0
        pytesseract.pytesseract.tesseract_cmd = pipeline.tesseract_cmd()
        d = pytesseract.image_to_data(g, config="--psm 6", output_type=pytesseract.Output.DICT, timeout=15)
    except Exception:
        return 0
    return sum(1 for c, w in zip(d["conf"], d["text"]) if len(w.strip()) >= 2 and float(c) >= 60 and any(ch.isalnum() for ch in w))


# ---------------------------------------------------------------- assessment
def check(cid: str, label: str, status: str, value, threshold, basis: str, validated: bool, message: str = "", region: list | None = None) -> dict:
    out = {"id": cid, "label": label, "status": status, "value": value, "threshold": threshold, "basis": basis, "validated": validated, "message": message}
    if region is not None:
        out["region"] = {"box": region}                                 # normalised [x, y, w, h] of the photo as displayed; present only where the check located something
    return out


@dataclass
class QualityAssessment:
    verdict: str                          # usable | usable_with_warnings | not_usable | uncertain
    reason: str                           # key in suitability.MESSAGES, or "ok"
    message: str
    category: str
    profile: str
    checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    document_like: bool = False
    version: str = VERSION
    legacy: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"version": self.version, "category": self.category, "profile": self.profile, "verdict": self.verdict, "reason": self.reason, "message": self.message,
                "checks": self.checks, "warnings": self.warnings, "metrics": self.metrics, "document_like": self.document_like,
                "legacy_report_version": self.legacy.get("version"), "validated_checks": [c["id"] for c in self.checks if c.get("validated")]}


def assess(data: bytes, category: str, legacy: "suitability.Report | None" = None) -> QualityAssessment:
    """`legacy`: an already computed suitability report for the same bytes and region (the upload flow has one), so the legacy gate is not run twice."""
    if category not in CATEGORIES:
        raise ValueError("unknown category")
    if category == "medical_report_image":
        return _assess_document_photo(data)
    region = REGION_FOR[category]
    legacy = legacy if legacy is not None else suitability.assess(data, region)
    L = legacy.to_dict()
    profile = PROFILE_FOR[category]
    checks = [dict(c, source=suitability.VERSION, validated=_legacy_validated(c["id"], profile)) for c in legacy.checks]
    if legacy.reason == "invalid_image":
        return QualityAssessment("not_usable", "invalid_image", legacy.message, category, profile, checks, [], {}, False, legacy=L)
    img = suitability.decode(data)
    m = dict(legacy.metrics)
    cal = _calibration()
    extra: list[dict] = []

    side = min(m["width"], m["height"])
    extra.append(check("resolution", "Resolution", "warn" if side < RECOMMENDED_MIN_SIDE else "pass", side, RECOMMENDED_MIN_SIDE, "advisory default (not derived from data)", False,
                       "More pixels help the clinic see detail; a larger photo is better." if side < RECOMMENDED_MIN_SIDE else "Resolution is good."))
    q = estimate_jpeg_quality(data)
    block = m.get("blockiness", 1.0)
    # a JPEG's own quantisation table is a far better witness (estimator error <= 0.4 on libjpeg re-encodes) than blockiness, which also fires on clean JPEGs; blockiness is used
    # only when there is no table to read (a PNG that may have been compressed earlier)
    comp_warn = (q < JPEG_QUALITY_WARN) if q is not None else block >= suitability.BLOCKINESS_WARN
    extra.append(check("compression", "Compression", "warn" if comp_warn else "pass", {"estimated_jpeg_quality": q, "blockiness": block},
                       {"min_quality": JPEG_QUALITY_WARN, "max_blockiness": suitability.BLOCKINESS_WARN}, "advisory defaults; estimator error measured in docs/eval/quality_v2.json", False,
                       "This photo looks heavily compressed; the clinic may ask for the original." if comp_warn else "Compression looks acceptable."))
    obs, obs_box = obstruction_region(img)
    obs_thr = cal.get("obstruction", {}).get("threshold", OBSTRUCTION_WARN)
    # validated on lesion close-ups only: for any other category (wider body shots, faces, hair) the same measurement is an advisory warning, never a rejection
    obs_val = bool(cal.get("obstruction", {}).get("validated")) and category == "skin_lesion"
    extra.append(check("obstruction", "Something covering part of the frame", "fail" if (obs >= obs_thr and obs_val) else "warn" if obs >= obs_thr else "pass", obs, obs_thr,
                       cal.get("obstruction", {}).get("basis", "advisory default"), obs_val,
                       "Part of the photo looks covered (for example by a finger, a lens cover or glare). Please retake it." if obs >= obs_thr else "Nothing appears to cover the frame.",
                       region=obs_box if obs >= obs_thr else None))
    if category == "face":
        ff = face_framing(img, suitability.detect_faces(img))
        extra.append(check("framing", "Face inside the frame", "warn" if ff["cut_off"] or (ff["offset"] or 0) > 0.28 else "pass", ff, {"max_offset": 0.28},
                           "advisory default; not validated on real face photographs", False,
                           "The face is cut off or far from the centre. Please retake it with the whole face in view." if ff["cut_off"] or (ff["offset"] or 0) > 0.28 else "The face is inside the frame."))
    words = document_words(img)
    doc_like = words >= DOCUMENT_MIN_WORDS
    extra.append(check("document", "Looks like a page of text", "warn" if doc_like else "pass", words, DOCUMENT_MIN_WORDS, "advisory; false-positive rate on skin photographs in docs/eval/quality_v2.json",
                       False, "This looks like a document. Upload it under Reports so it can be read." if doc_like else "Does not look like a document."))
    m.update({"estimated_jpeg_quality": q, "obstruction_fraction": obs, "document_words": words})
    allc = checks + extra
    warnings = list(legacy.warnings) + [c["message"] for c in extra if c["status"] == "warn" and c["message"]]
    failed_v2 = [c for c in extra if c["status"] == "fail" and c["validated"]]
    if legacy.verdict == "rejected":
        return QualityAssessment("not_usable", legacy.reason, legacy.message, category, profile, allc, warnings, m, doc_like, legacy=L)
    if failed_v2:
        c = failed_v2[0]
        return QualityAssessment("not_usable", c["id"], c["message"], category, profile, allc, warnings, m, doc_like, legacy=L)
    if legacy.verdict == "uncertain":
        return QualityAssessment("uncertain", legacy.reason, legacy.message, category, profile, allc, warnings, m, doc_like, legacy=L)
    verdict = "usable_with_warnings" if warnings else "usable"
    return QualityAssessment(verdict, "ok", suitability.MESSAGES["ok"], category, profile, allc, warnings, m, doc_like, legacy=L)


def _legacy_validated(check_id: str, profile: str) -> bool:
    """The legacy sharpness/exposure thresholds were fitted and validated on PAD-UFES-20 skin close-ups only; other profiles use untuned defaults."""
    return profile == "skin_closeup" and check_id in ("quality", "lighting")


def _assess_document_photo(data: bytes) -> QualityAssessment:
    """A photo of a paper report: legibility matters, not skin. Uses the same decode, blur and exposure measurements, with the document-appropriate question: can text be read?"""
    try:
        img = suitability.decode(data)
    except ValueError:
        return QualityAssessment("not_usable", "invalid_image", suitability.MESSAGES["invalid_image"], "medical_report_image", "document", [], [], {})
    m = suitability.quality_metrics(img)
    words = document_words(img)
    checks = [check("legibility", "Text can be read", "pass" if words >= DOCUMENT_MIN_WORDS else "warn", words, DOCUMENT_MIN_WORDS, "advisory: OCR word count", False,
                    "Text was readable." if words >= DOCUMENT_MIN_WORDS else "Not much text could be read. Flatten the page, add light and retake it."),
              check("resolution", "Resolution", "pass" if min(m["width"], m["height"]) >= 800 else "warn", min(m["width"], m["height"]), 800, "advisory default", False,
                    "Higher resolution makes small print easier to read." if min(m["width"], m["height"]) < 800 else "Resolution is good."),
              check("focus", "Focus", "pass" if m["sharpness"] >= 40 else "warn", m["sharpness"], 40, "advisory default", False, "The page looks out of focus." if m["sharpness"] < 40 else "In focus.")]
    warnings = [c["message"] for c in checks if c["status"] == "warn"]
    return QualityAssessment("usable_with_warnings" if warnings else "usable", "ok", "Upload this page under Reports; the clinic reviews what is read from it.", "medical_report_image", "document",
                             checks, warnings, dict(m, document_words=words), words >= DOCUMENT_MIN_WORDS)

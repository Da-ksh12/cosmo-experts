"""Task A: image suitability (versioned). Checks image decodability, quality and region plausibility.

This is NOT a diagnostic model. Acceptance means only "usable for the selected workflow".
Hair/scalp and body checks are conservative colour/texture heuristics; when they cannot decide the
result is 'uncertain' rather than a guess."""
from __future__ import annotations

import io
from dataclasses import dataclass, asdict, field

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

VERSION = "suitability-1.0.0"
REGIONS = ("face", "hair_scalp", "body", "other")
MAX_PIXELS = 60_000_000

# Quality thresholds per photo type. "default" = the original untuned values (faces; NOT validated on real faces).
# "skin_closeup" = fitted on the PAD-UFES-20 training split and reported on its test split (docs/eval/quality_calibration.json): the old
# values falsely rejected 49 % of usable skin close-ups, these reject 8.4 % while still catching blur/dark/over-exposed/washed-out simulations (98-100 %).
QUALITY_PROFILES = {
    "default": {"sharpness": 40.0, "contrast": 18.0, "dark": 55.0, "bright": 215.0},
    "skin_closeup": {"sharpness": 10.5, "contrast": 11.9, "dark": 85.0, "bright": 200.0},
}
PROFILE_BY_REGION = {"face": "default", "hair_scalp": "skin_closeup", "body": "skin_closeup", "other": "skin_closeup", "lesion": "skin_closeup"}


def quality_issues(m: dict, profile: str = "default") -> list[str]:
    """Reasons (in priority order) why a photo is unusable under the given profile. Keys match MESSAGES."""
    t = QUALITY_PROFILES[profile]
    # Exposure is reported before focus: darkening or over-exposure also lowers measured sharpness, and "too dark" is the actionable cause.
    out = []
    if min(m["width"], m["height"]) < 300:
        out.append("too_small")
    if m["brightness"] < t["dark"] or m["underexposed_fraction"] > 0.5:
        out.append("too_dark")
    if m["brightness"] > t["bright"] or m["overexposed_fraction"] > 0.35:
        out.append("too_bright")
    if m["sharpness"] < t["sharpness"]:
        out.append("blurry")
    if m["contrast"] < t["contrast"]:
        out.append("low_contrast")
    return out


MESSAGES = {
    "invalid_image": "We couldn't open this file as a photo. Please upload a JPG or PNG image.",
    "too_small": "This photo is too small to check. Please upload a higher-resolution photo.",
    "no_face": "We couldn't find a suitable face in this photo. Please upload a clear face photo.",
    "face_too_small": "The face is too small in this photo. Please move closer and retake it.",
    "multiple_faces": "We found more than one face. Please upload a photo with only the patient's face.",
    "blurry": "The photo is blurry. Please retake it while holding the camera steady.",
    "too_dark": "The photo is too dark. Please try again in brighter, even light.",
    "too_bright": "The photo is too bright or washed out. Please avoid direct flash or harsh sunlight.",
    "low_contrast": "The photo looks flat or washed out. Please try again in even, natural light.",
    "wrong_region": "This photo may not show the area selected. Please choose the correct area or take another photo.",
    "uncertain": "We couldn't confidently check this photo. Please try another image or ask the clinic team for help.",
    "ok": "This photo can be used for the selected area. A successful photo check is not a diagnosis.",
}


@dataclass
class Report:
    verdict: str                      # accepted | rejected | uncertain
    reason: str                       # key in MESSAGES
    message: str
    region: str
    metrics: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    version: str = VERSION
    checks: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


_face_cascade = None


def _cascade():
    global _face_cascade
    if _face_cascade is None:
        _face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _face_cascade


def decode(data: bytes) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > MAX_PIXELS:
                raise ValueError("image too large")
            im.load()
            im = ImageOps.exif_transpose(im).convert("RGB")
            return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        raise ValueError(str(e))


def _resize(img, long_side=800):
    h, w = img.shape[:2]
    s = long_side / max(h, w)
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img


def quality_metrics(img) -> dict:
    gray = cv2.cvtColor(_resize(img), cv2.COLOR_BGR2GRAY)
    return {
        "width": int(img.shape[1]), "height": int(img.shape[0]),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        "brightness": round(float(gray.mean()), 1),
        "contrast": round(float(gray.std()), 1),
        "overexposed_fraction": round(float((gray > 245).mean()), 3),
        "underexposed_fraction": round(float((gray < 15).mean()), 3),
    }


def skin_mask(img) -> np.ndarray:
    ycrcb = cv2.cvtColor(_resize(img, 400), cv2.COLOR_BGR2YCrCb)
    return cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127)) > 0


def detect_faces(img):
    small = _resize(img, 800)
    scale = img.shape[1] / small.shape[1]
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    faces = _cascade().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(60, 60))
    sm = skin_mask(small)
    fy, fx = sm.shape[0] / small.shape[0], sm.shape[1] / small.shape[1]
    out = []
    for (x, y, w, h) in faces:
        patch = sm[int(y * fy):int((y + h) * fy), int(x * fx):int((x + w) * fx)]
        # reject Haar false positives that contain almost no skin-coloured pixels
        if patch.size and patch.mean() >= 0.25:
            out.append((int(x * scale), int(y * scale), int(w * scale), int(h * scale)))
    return out


def _hair_like_fraction(img) -> float:
    hsv = cv2.cvtColor(_resize(img, 400), cv2.COLOR_BGR2HSV)
    return float(((hsv[..., 2] < 110) & (hsv[..., 1] < 140)).mean())


def _result(verdict, reason, region, metrics, warnings=None):
    return Report(verdict, reason, MESSAGES[reason], region, metrics, warnings or [])


def _check(cid, label, status, detail):
    return {"id": cid, "label": label, "status": status, "detail": detail}


def _checklist(m, region, faces, big_face, verdict, reason, decoded=True):
    """Per-check results for the UI. Every entry is derived from measured values; nothing is assumed."""
    if not decoded:
        skip = lambda cid, label: _check(cid, label, "skipped", "Not checked because the file could not be read.")
        return [_check("format", "File format", "failed", "This file could not be opened as a JPG or PNG photo."),
                skip("quality", "Image quality"), skip("lighting", "Lighting"), skip("subject", "Subject visibility"),
                skip("framing", "Framing"), _check("overall", "Overall suitability", "failed", MESSAGES[reason])]
    iss = quality_issues(m, PROFILE_BY_REGION[region])
    small, blurry = "too_small" in iss, "blurry" in iss
    q = ("failed", "Too small to check reliably." if small else "Not in focus.") if (small or blurry) else ("passed", "Resolution and focus are acceptable.")
    dark, bright, flat = "too_dark" in iss, "too_bright" in iss, "low_contrast" in iss
    lt = ("failed", "Too dark." if dark else "Too bright or washed out." if bright else "Flat lighting, low contrast.") if (dark or bright or flat)         else ("passed", "Lighting is even enough.")
    if region == "face":
        subj = ("passed", "A face was found.") if faces else ("failed", "No suitable face found.")
        if len(faces) > 1:
            subj = ("failed", "More than one face found.")
        frm = ("skipped", "Needs a detected face.") if not faces else             (("passed", "Face fills enough of the frame.") if big_face >= 0.08 else ("failed", "Face is too small in the frame."))
    elif region == "hair_scalp":
        subj = {"wrong_region": ("failed", "This may not show the hair or scalp."), "uncertain": ("uncertain", "Could not confidently confirm hair or scalp.")}.get(
            reason, ("passed", "Hair or scalp colours and texture look plausible (heuristic)."))
        frm = ("failed", "Looks like a face portrait.") if m.get("largest_face_fraction", 0) > 0.25 else ("passed", "Framing looks acceptable.")
    else:
        subj = {"wrong_region": ("failed", "This may not show a skin area."), "uncertain": ("uncertain", "Could not confidently confirm the skin area.")}.get(
            reason, ("passed", "A skin area is visible (heuristic)."))
        frm = ("failed", "A face fills much of the frame.") if m.get("largest_face_fraction", 0) > 0.15 else ("passed", "Framing looks acceptable.")
    overall = {"accepted": ("passed", MESSAGES["ok"]), "uncertain": ("uncertain", MESSAGES["uncertain"])}.get(verdict, ("failed", MESSAGES[reason]))
    return [_check("format", "File format", "passed", "Readable JPG or PNG."),
            _check("quality", "Image quality", *q), _check("lighting", "Lighting", *lt),
            _check("subject", "Face detection" if region == "face" else "Subject visibility", *subj),
            _check("framing", "Framing", *frm), _check("overall", "Overall suitability", *overall)]


def assess(data: bytes, region: str) -> Report:
    if region not in REGIONS:
        raise ValueError("unknown region")
    try:
        img = decode(data)
    except ValueError:
        r = _result("rejected", "invalid_image", region, {})
        r.checks = _checklist({}, region, [], 0, "rejected", "invalid_image", decoded=False)
        return r
    m = quality_metrics(img)
    faces = detect_faces(img)
    area = img.shape[0] * img.shape[1]
    big_face = max((w * h for _, _, w, h in faces), default=0) / area
    skin_frac = float(skin_mask(img).mean())
    hair = _hair_like_fraction(img)
    m.update({"faces_found": len(faces), "largest_face_fraction": round(big_face, 3), "skin_fraction": round(skin_frac, 3),
              "hair_like_fraction": round(hair, 3)})
    rep = _decide(m, region, faces, big_face, skin_frac, hair)
    rep.checks = _checklist(m, region, faces, big_face, rep.verdict, rep.reason)
    return rep


def _decide(m, region, faces, big_face, skin_frac, hair) -> Report:
    # Quality gates by photo type (see QUALITY_PROFILES for how the thresholds were obtained).
    for reason in quality_issues(m, PROFILE_BY_REGION[region]):
        return _result("rejected", reason, region, m)

    if region == "face":
        if not faces:
            return _result("rejected", "no_face", region, m)
        if len(faces) > 1:
            return _result("rejected", "multiple_faces", region, m)
        if big_face < 0.08:
            return _result("rejected", "face_too_small", region, m)
        return _result("accepted", "ok", region, m,
                       ["A detected face does not mean the photo suits every skin concern."])

    if region == "other":
        region = "body"   # "Other skin area" uses the same skin-visibility rules as body
    if region == "hair_scalp":
        if big_face > 0.25:
            return _result("rejected", "wrong_region", region, m)
        if hair >= 0.2 and hair + skin_frac >= 0.6:
            return _result("accepted", "ok", region, m,
                           ["Hair/scalp checking is a simple colour heuristic; the clinic confirms suitability."])
        if hair + skin_frac < 0.3:
            return _result("rejected", "wrong_region", region, m)
        return _result("uncertain", "uncertain", region, m)

    # body / other skin area
    if big_face > 0.15:
        return _result("rejected", "wrong_region", region, m)
    if skin_frac >= 0.25:
        return _result("accepted", "ok", region, m, ["Body-area checking is a skin-visibility heuristic only."])
    if skin_frac < 0.08:
        return _result("rejected", "wrong_region", region, m)
    return _result("uncertain", "uncertain", region, m)

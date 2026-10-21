"""Builds the PhotoAnalysis (schema.py) for one photo from what the backend measured and resolved. Pure assembly: it runs no model, writes nothing and invents nothing.

Where each part comes from:
* observations about the photo       <- the suitability checklist and the quality-v2 checks of the SAME upload (measured values, the thresholds they were compared with, whether the
                                         threshold was validated against real photographs);
* geometry                           <- the face rectangle(s) the detector found and the covered/glare region the quality check found; converted to fractions of the photo as displayed;
                                         a box that does not lie inside the photo is dropped, never repaired;
* skin-feature observations          <- ONLY outcomes of a model that the capability registry reports as approved for patients (vision.analyze never runs anything else for a patient).
                                         None exists today: every skin-feature task is listed under `capabilities` as not available and nothing is produced;
* what the photo cannot establish    <- the limitations the capability registry itself declares, the report's own warnings, and the fixed statement that a photo check looks at the photo;
* questions                          <- built from the observations that exist (a question that could only be answered with "I can't" is not offered), plus a general-care question only
                                         when clinic-approved material can actually answer it."""
from __future__ import annotations

import io
import logging
from typing import Any

from PIL import Image
from pydantic import TypeAdapter, ValidationError

from .. import db
from ..ai import get_engine, safety, vision
from ..imaging import quality, suitability
from . import schema as S

log = logging.getLogger("cosmo.photo_analysis")
_GEOMETRY = TypeAdapter(S.Geometry)

REGION_LABEL = {"face": "Face / skin", "hair_scalp": "Hair / scalp", "body": "Body area", "other": "Other area"}
HEADLINE = {"accepted": "Your photo is suitable for review", "uncertain": "We couldn't be sure about this photo", "rejected": "This photo needs to be retaken"}

# Practical, non-medical advice for each way a photo can fail. The wording of the failure itself comes from suitability.MESSAGES; this only says how to fix it.
TIPS = {
    "too_dark": "Move to bright, even light, for example facing a window, and avoid strong light from behind.",
    "too_bright": "Avoid flash and direct sun. Soft, even daylight works best.",
    "blurry": "Hold the phone with both hands, tap the screen to focus, and keep still for a moment. The rear camera usually gives a sharper photo.",
    "motion_blur": "Rest your elbows on a table or lean on a wall, then take the photo. The rear camera usually gives a sharper photo.",
    "low_contrast": "Use soft, even daylight and wipe the camera lens.",
    "too_small": "Use the phone's own camera at full resolution instead of a small or forwarded copy.",
    "no_face": "Face the camera, take off glasses or a hat, and keep your whole face in the frame.",
    "face_too_small": "Move closer so the face fills more of the frame.",
    "multiple_faces": "Take the photo with only the patient's face in view.",
    "wrong_region": "Check that you chose the right area, then take the photo again.",
    "uncertain": "Try another photo in even light, or ask the clinic team for help.",
    "invalid_image": "Take a new photo with the phone's camera, or choose a JPG or PNG from your gallery.",
    "obstruction": "Keep fingers, straps and the lens cover out of view, and avoid the flash.",
}
MEANING = {
    "too_dark": "A dark photo hides colour and texture, which are what a clinician looks at.",
    "too_bright": "A washed-out photo hides colour and texture, which are what a clinician looks at.",
    "blurry": "A blurry photo hides detail, so your clinician may ask for another one.",
    "motion_blur": "A smeared photo hides detail, so your clinician may ask for another one.",
    "low_contrast": "Flat, low-contrast light makes it hard to tell features apart.",
    "too_small": "A small photo has too little detail to review.",
    "no_face": "If the face cannot be found, the area cannot be reviewed from this photo.",
    "face_too_small": "If the face is small in the frame, there is too little detail to review.",
    "multiple_faces": "With more than one face it is unclear whose skin the photo shows.",
    "wrong_region": "The photo may not show the area you selected, so it could be compared with the wrong place.",
    "obstruction": "Something covering part of the photo (a finger, glare or a lens cover) hides that part of the image.",
}


def _pct(v: float | None) -> str:
    return f"{round(float(v) * 100):d}%" if v is not None else "n/a"


def _tone(status: str) -> str:
    return {"passed": "ok", "pass": "ok", "failed": "attention", "fail": "attention", "warn": "attention", "uncertain": "attention"}.get(status, "info")


# ---------------------------------------------------------------- provenance
def _provenance() -> list[S.Provenance]:
    return [
        S.Provenance(capability="image.quality", version=suitability.VERSION, kind="rule_based", approval="not_required", validated=False,
                     note="Rule-based checks of focus, light, size, subject and framing. Some thresholds were validated on skin close-ups; the rules for faces were not validated on real face photographs."),
        S.Provenance(capability="image.quality", version=quality.VERSION, kind="rule_based", approval="not_required", validated=False,
                     note="Additional photo checks (resolution, compression, something covering the frame, a page of text). Advisory unless marked validated."),
        S.Provenance(capability="face.detect", version="opencv-haarcascade-frontalface-default", kind="rule_based", approval="not_required", validated=False,
                     note="A classical face detector, used only to locate the face area for the checks. It can miss or mislabel faces and was not validated on real face photographs."),
    ]


# ---------------------------------------------------------------- observations about the photo
def _legacy(rep: dict, region: str) -> list[S.Observation]:
    m = rep.get("metrics") or {}
    profile = suitability.PROFILE_BY_REGION.get(region, "default")
    t = suitability.QUALITY_PROFILES[profile]
    out: list[S.Observation] = []
    for c in rep.get("checks", []):
        cid = c["id"]
        ev: list[S.Evidence] = []
        if cid == "quality" and m:
            ev = [S.Evidence(text=f"Focus measure {m.get('sharpness')} (needs at least {t['sharpness']})", metric="sharpness", value=m.get("sharpness"), threshold=f"at least {t['sharpness']}"),
                  S.Evidence(text=f"Photo size {m.get('width')} x {m.get('height')} pixels (shorter side needs at least 300)", metric="shorter_side_px", value=min(m.get("width", 0), m.get("height", 0)), threshold="at least 300")]
            if t.get("min_direction_ratio") is not None and m.get("direction_ratio") is not None:
                ev.append(S.Evidence(text=f"Detail in all directions {m['direction_ratio']} (a smeared photo scores close to 0)", metric="direction_ratio", value=m["direction_ratio"], threshold=f"above {t['min_direction_ratio']}"))
        elif cid == "lighting" and m:
            ev = [S.Evidence(text=f"Average brightness {m.get('brightness')} of 255 (accepted between {t['dark']:.0f} and {t['bright']:.0f})", metric="brightness", value=m.get("brightness"), threshold=f"{t['dark']:.0f} to {t['bright']:.0f}"),
                  S.Evidence(text=f"Contrast {m.get('contrast')} (needs at least {t['contrast']})", metric="contrast", value=m.get("contrast"), threshold=f"at least {t['contrast']}"),
                  S.Evidence(text=f"Very dark pixels {_pct(m.get('underexposed_fraction'))}, very bright pixels {_pct(m.get('overexposed_fraction'))}", metric="exposed_fraction",
                             value=f"{_pct(m.get('underexposed_fraction'))} / {_pct(m.get('overexposed_fraction'))}", threshold="under 50% dark, under 35% bright")]
        elif cid == "subject" and m:
            if region == "face":
                ev = [S.Evidence(text=f"Faces found: {m.get('faces_found', 0)}", metric="faces_found", value=m.get("faces_found", 0), threshold="exactly 1")]
            else:
                ev = [S.Evidence(text=f"Skin-coloured pixels {_pct(m.get('skin_fraction'))}, hair-like pixels {_pct(m.get('hair_like_fraction'))} (a simple colour heuristic)", metric="skin_fraction", value=m.get("skin_fraction"))]
        elif cid == "framing" and m:
            ev = [S.Evidence(text=f"Largest face fills {_pct(m.get('largest_face_fraction'))} of the photo" + (" (needs at least 8%)" if region == "face" else ""), metric="largest_face_fraction",
                             value=m.get("largest_face_fraction"), threshold="at least 8%" if region == "face" else None)]
        out.append(S.Observation(id=cid, kind="framing" if cid in ("subject", "framing") else "photo_quality", group="observe", label=c["label"], tone=_tone(c["status"]),
                                 summary=c["detail"], evidence=ev, provenance=2 if cid == "subject" and region == "face" else 0,
                                 validated=quality._legacy_validated(cid, profile) if cid in ("quality", "lighting") else False,
                                 limitations=["The rules for faces were not validated on real face photographs."] if region == "face" and cid in ("quality", "lighting", "subject", "framing") else []))
    return out


def _v2(rep: dict, width: int, height: int) -> list[S.Observation]:
    out: list[S.Observation] = []
    for c in (rep.get("quality_v2") or {}).get("checks", []):
        cid, val, thr = c["id"], c.get("value"), c.get("threshold")
        ev: list[S.Evidence] = []
        if cid == "resolution":
            ev = [S.Evidence(text=f"Shorter side {val} pixels (recommended at least {thr})", metric="shorter_side_px", value=val, threshold=f"at least {thr}")]
        elif cid == "compression" and isinstance(val, dict):
            q = val.get("estimated_jpeg_quality")
            ev = [S.Evidence(text=f"Estimated JPEG quality {q}" if q is not None else "Not a JPEG: judged from blockiness", metric="estimated_jpeg_quality", value=q, threshold=f"at least {thr.get('min_quality')}" if isinstance(thr, dict) else None)]
        elif cid == "obstruction":
            ev = [S.Evidence(text=f"Largest area that looks covered: {_pct(val)} of the photo (flagged from {_pct(thr)})", metric="obstruction_fraction", value=val, threshold=f"below {_pct(thr)}")]
        elif cid == "framing" and isinstance(val, dict):
            ev = [S.Evidence(text=("The face touches or crosses the edge of the photo" if val.get("cut_off") else "The whole face is inside the photo") + (f"; its centre is {val['offset']} of the frame away from the middle" if val.get("offset") is not None else ""),
                             metric="face_offset", value=val.get("offset"), threshold="at most 0.28")]
        elif cid == "document":
            ev = [S.Evidence(text=f"Readable words found: {val}", metric="document_words", value=val, threshold=f"under {thr}")]
        geom = None
        reg = (c.get("region") or {}).get("box")
        if reg is not None:
            box = S.box_from_unit(reg)
            if box is None:
                log.warning("photo analysis: dropped an invalid region for check %s", cid)
            else:
                geom = box
        out.append(S.Observation(id=f"v2_{cid}", kind="photo_quality", group="area" if geom else "observe", label=c["label"], tone=_tone(c["status"]), summary=c.get("message") or "", evidence=ev,
                                 provenance=1, validated=bool(c.get("validated")), geometry=geom,
                                 limitations=[] if c.get("validated") else ["Advisory: this threshold was not validated as a reason to reject a photo."]))
    return out


def _faces(rep: dict, width: int, height: int) -> list[S.Observation]:
    m = rep.get("metrics") or {}
    out: list[S.Observation] = []
    regs = [r for r in rep.get("regions", []) if r.get("kind") == "face"]
    for i, r in enumerate(regs, 1):
        box = S.box_from_pixels(r.get("box_px") or [], width, height)
        if box is None:
            log.warning("photo analysis: dropped an invalid face box")
            continue
        n = len(regs)
        out.append(S.Observation(id=f"face-{i}", kind="face_region", group="area", label="Face area" if n == 1 else f"Face {i}", tone="info",
                                 summary="The area the photo checks treated as the face. It shows where the checks looked; it is not a finding about your skin.",
                                 evidence=[S.Evidence(text=f"This face fills {_pct((box.w * box.h))} of the photo", metric="face_fraction", value=round(box.w * box.h, 3))] + (
                                     [S.Evidence(text=f"{n} faces were found", metric="faces_found", value=n)] if n > 1 else []),
                                 geometry=box, provenance=2, limitations=["Face detection can miss or mislabel a face and was not validated on real face photographs."]))
    return out


# ---------------------------------------------------------------- observations about the skin: approved models only
def _model_observations(ia: vision.ImageAnalysis | None, provenance: list[S.Provenance], width: int, height: int) -> list[S.Observation]:
    """Findings of an APPROVED model for this patient, if one ran. A result must be {"findings": [{id, label, summary, geometry?, score?, calibration?, evidence?, limitations?}]};
    anything that does not validate is dropped (and logged), never repaired. Nothing here can run a model: vision.analyze already restricted what ran to approved tasks."""
    out: list[S.Observation] = []
    if ia is None:
        return out
    eng = get_engine()
    for o in ia.outcomes:
        if o.status != "ok" or not isinstance(o.result, dict):
            continue
        st = eng.describe(o.capability)
        if not (st.approval == "approved_for_defined_use" and "patient" in st.audiences and st.patient_facing):
            continue                                                  # research and unapproved output is never shown to a patient
        provenance.append(S.Provenance(capability=st.id, version=st.version, kind="model", approval=st.approval, validated=True,
                                       note=(st.model.model_id if st.model else st.name) + ": " + "; ".join(st.limitations[:2])))
        pidx = len(provenance) - 1
        for f in o.result.get("findings", [])[:20]:
            try:
                geom = None
                if f.get("geometry") is not None:
                    geom = _GEOMETRY.validate_python(f["geometry"])
                conf = None
                cal = f.get("calibration") or {}
                if f.get("score") is not None and cal.get("calibrated") is True:      # a score is shown only when it is calibrated
                    conf = S.Confidence(value=float(f["score"]), calibrated=True, basis=str(cal.get("basis", "")))
                out.append(S.Observation(id=f"skin-{len(out) + 1}", kind="skin_feature", group="area" if geom else "observe", label=str(f["label"])[:80], tone="info",
                                         summary=str(f.get("summary", ""))[:500], evidence=[S.Evidence(text=str(e)[:300]) for e in f.get("evidence", [])][:12],
                                         limitations=[str(x)[:200] for x in (f.get("limitations") or st.limitations)][:6], confidence=conf, geometry=geom, provenance=pidx, validated=True))
            except (ValidationError, KeyError, TypeError, ValueError) as e:
                log.warning("photo analysis: dropped an invalid model finding (%s)", type(e).__name__)
    return out


# ---------------------------------------------------------------- the whole analysis
def exif_orientation(data: bytes | None) -> int | None:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            v = im.getexif().get(0x0112)
            return int(v) if v in range(1, 9) else None
    except Exception:                                               # an unreadable file simply has no orientation to report
        return None


def capability_notes(category: str, ia: vision.ImageAnalysis | None) -> list[S.CapabilityNote]:
    """The honest availability of every skin analysis that applies to this photo category, as a patient may see it."""
    spec = vision.CATEGORY_SPECS.get(category)
    if spec is None or spec.research_only:
        return []
    eng = get_engine()
    by_task = {c.task: c for c in (ia.candidates if ia else [])}
    out = []
    for t in spec.tasks:
        st = eng.describe(f"image.{t}")
        c = by_task.get(t)
        ok = bool(c.runnable_by_caller) if c else bool(st.patient_facing)
        out.append(S.CapabilityNote(task=t, name=st.name, available=ok, status=st.status,
                                    note="Available." if ok else "No approved analysis exists for this yet, so it is not attempted."))
    return out


def build(*, analysis_id: str, owner: str, out: dict, data: bytes | None, params: dict, created_at: float, finished_at: float | None, ia: vision.ImageAnalysis | None,
          previous_photos: int = 0, kb_care: bool = False) -> dict:
    """`out` is the result of services.core.check_and_store_image (the stored photo's id and the full report of THIS upload). Returns the validated PhotoAnalysis as a plain dict."""
    rep = out["report"]
    region = params.get("region", "face")
    m = rep.get("metrics") or {}
    width, height = int(m.get("width") or 0), int(m.get("height") or 0)
    verdict = rep.get("verdict")
    reason = rep.get("reason", "")
    prov = _provenance()

    obs: list[S.Observation] = []
    if width and height:
        obs += _legacy(rep, region) + _v2(rep, width, height) + _faces(rep, width, height)
        obs += _model_observations(ia, prov, width, height)
    else:
        obs += _legacy(rep, region)
    attention = [o for o in obs if o.tone == "attention"]
    doc_like = bool((rep.get("quality_v2") or {}).get("document_like"))

    row = db.q1("SELECT id, stored_path, deleted, created_at, clinician_review FROM images WHERE id=?", (out["id"],)) or {}
    kept = bool(row.get("stored_path")) and not row.get("deleted")
    image = S.ImageRef(id=out["id"] if kept else None, kept=kept, url=f"/api/images/{out['id']}/file" if kept else None, thumb_url=f"/api/images/{out['id']}/file?thumb=1" if kept else None,
                       width=width or None, height=height or None, region=region, region_label=REGION_LABEL.get(region, region), taken_at=row.get("created_at"))
    transform = S.Transform(width=width, height=height, exif_orientation=exif_orientation(data)) if width and height else None

    # ---- outcome
    if doc_like and verdict != "accepted":
        route, headline, message = "document_pipeline", "This looks like a document", (rep.get("routing") or {}).get("message", "Please upload it under Reports so it can be read and checked.")
    elif verdict == "accepted":
        route, headline, message = "clinician_review", HEADLINE["accepted"], out.get("next") or suitability.MESSAGES["ok"]
    else:
        route, headline, message = "rejected", HEADLINE.get(verdict or "rejected", HEADLINE["rejected"]), rep.get("corrective_action") or rep.get("message", "")
    if ia is not None and ia.route == "image_models" and verdict == "accepted":
        route = "image_models"
    outcome = S.Outcome(route=route, verdict=verdict if verdict in ("accepted", "uncertain", "rejected") else None, reason=reason or None, headline=headline, message=message, clinician_review=True)

    # ---- what it may mean, what it cannot establish
    meaning: list[str] = []
    if verdict == "accepted":
        meaning += ["The photo is clear enough for your clinician to look at.", "This check only looks at the photo. It does not look at your skin, so it says nothing about any skin condition."]
    else:
        if MEANING.get(reason):
            meaning.append(MEANING[reason])
    if any(o.id == "v2_obstruction" and o.tone == "attention" for o in obs):
        meaning.append(MEANING["obstruction"])
    caps = capability_notes(region, ia)
    eng = get_engine()
    cannot = ["This check measures the photo, not your skin. It cannot find, rule out or diagnose any skin condition."]
    if caps and not any(c.available for c in caps):
        cannot.append("Visible skin features (for example spots, redness, dryness, pigmentation or texture) are not analysed: no approved analysis exists for them yet.")
    cannot += [x for x in eng.describe("image.quality").limitations if x not in cannot]
    cannot += [w for w in rep.get("warnings", []) if w not in cannot]
    cannot = cannot[:8]

    # ---- next steps
    steps: list[S.NextStep] = []
    if route == "document_pipeline":
        steps.append(S.NextStep(id="reports", kind="reports", label="Upload it under Reports", detail="Reports are read and explained by the document tools, not the photo check.", route="/reports"))
    elif verdict == "accepted":
        steps += [S.NextStep(id="clinician", kind="clinician", label="Your clinician will review it", detail="Accepted photos are saved privately and reviewed together with your history."),
                  S.NextStep(id="add_photo", kind="add_photo", label="Add another angle", detail="Optional: a second photo from a different angle can help.", route=f"/photo?region={region}")]
    else:
        tip = TIPS.get(reason) or TIPS["uncertain"]
        if any(o.id == "v2_obstruction" and o.tone == "attention" for o in obs) and reason not in TIPS:
            tip = TIPS["obstruction"]
        steps.append(S.NextStep(id="retake", kind="retake", label="Retake the photo", detail=tip, route=f"/photo?region={region}"))
    steps.append(S.NextStep(id="summary", kind="summary", label="Prepare a summary for my dermatologist", detail="A short, factual summary of this photo check that you can share."))

    # ---- questions: only ones this analysis can actually answer
    qs: list[S.Question] = [S.Question(id="q-measure", text="Explain what was measured.", intent="measurements")]
    if verdict == "accepted" and not attention:
        qs.append(S.Question(id="q-next", text="What happens next with my photo?", intent="what_next"))
    if attention or verdict != "accepted":
        qs.append(S.Question(id="q-better", text="How can I take a better photo?", intent="better_photo"))
    for o in attention[:2]:
        qs.append(S.Question(id=f"q-obs-{o.id}", text=f"Explain: {o.label}.", intent="observation", observation_id=o.id))
    for o in [o for o in obs if o.group == "area" and o.tone != "attention"][:1]:
        qs.append(S.Question(id=f"q-obs-{o.id}", text="What is the outlined area?", intent="observation", observation_id=o.id))
    qs.append(S.Question(id="q-summary", text="Prepare a summary for my dermatologist.", intent="summary"))
    if kept and verdict == "accepted" and previous_photos > 0:
        qs.append(S.Question(id="q-compare", text="Compare this with my previous photo.", intent="compare"))
    if kb_care and verdict == "accepted":
        qs.append(S.Question(id="q-care", text="What can I do to care for my skin?", intent="care"))

    review = S.ClinicianReview(status="reviewed" if (row.get("clinician_review") == "reviewed") else "pending", message="A clinician reviews accepted photos together with your history.") if kept else \
        S.ClinicianReview(status="not_applicable", message="This photo was not kept, so there is nothing for a clinician to review.")
    status = {"accepted": "completed", "uncertain": "uncertain", "rejected": "rejected"}.get(verdict or "", "completed")
    analysis = S.PhotoAnalysis(
        id=analysis_id, conversation_id=params.get("conversation_id"), message_id=params.get("message_id"), index=int(params.get("index", 0)), status=status, created_at=created_at, finished_at=finished_at,
        image=image, transform=transform, outcome=outcome, provenance=prov, observations=obs,
        sections=S.Sections(observe=[o.id for o in obs if o.group == "observe"], areas=[o.id for o in obs if o.group == "area"], meaning=meaning, cannot=cannot, next_steps=steps, questions=qs),
        capabilities=caps, clinician_review=review,
        disclaimers=[safety.NOT_DIAGNOSIS, safety.REVIEW_NOTE] + [d for d in (ia.disclaimers if ia else []) if d not in (safety.NOT_DIAGNOSIS, safety.REVIEW_NOTE)])
    return analysis.model_dump(mode="json")


def minimal(job: dict, params: dict, *, stage: str | None = None, error: str | None = None) -> dict:
    """The analysis of a photo that has no result yet (queued or processing) or never will (failed, cancelled): the real status and nothing else."""
    status = job["status"]
    headline = {"failed": "The photo check could not finish", "cancelled": "The check was cancelled"}.get(status)
    outcome = S.Outcome(route="clinician_review", verdict=None, headline=headline, message=error or ("Nothing was saved." if status in ("failed", "cancelled") else ""), clinician_review=False) if headline else None
    region = params.get("region", "face")
    return S.PhotoAnalysis(id=job["id"], conversation_id=params.get("conversation_id"), message_id=params.get("message_id"), index=int(params.get("index", 0)), status=status, stage=stage, error=error,
                           created_at=job["created_at"], finished_at=job["finished_at"], image=S.ImageRef(region=region, region_label=REGION_LABEL.get(region, region)),
                           outcome=outcome).model_dump(mode="json")

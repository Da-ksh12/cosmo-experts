"""Extensible, versioned task registry. A task is a *potential* capability; only tasks whose status says so
may produce outputs. Unimplemented tasks must never return predictions."""
from __future__ import annotations

from .. import status as st

TASKS = [
    dict(id="image_suitability", name="Image suitability check", version="1.0.0",
         intended_use="Tell the patient whether a photo is usable for the selected area (quality, subject, region). Not a diagnosis.",
         required_image_type="face, scalp/hair or body photograph", required_labels=[], required_dataset="Held-out set of accepted/rejected photos (to extend validation)",
         architecture="OpenCV quality metrics + Haar face detector + skin/hair colour heuristics",
         evaluation_requirements=["Per-reason precision/recall on a labelled photo set", "Skin-tone-stratified false-reject rate"],
         supported_populations="Not formally evaluated across skin tones; Haar detector known to be weaker on some faces/poses",
         limitations=["Scalp and body checks are heuristics and may return 'uncertain'", "Does not assess any condition"],
         declared_status="available"),
    dict(id="acne_features", name="Acne-like visible features", version="0.0.0",
         intended_use="Possible future: highlight visible acne-like features for clinician review.",
         required_image_type="face photograph", required_labels=["acne severity grade", "lesion counts"],
         required_dataset="Consented, licensed facial acne images with clinician labels (e.g. ACNE04 if terms permit)",
         architecture="Transfer-learned CNN classifier (ResNet/MobileNet)",
         evaluation_requirements=["Held-out patient-level test set", "Fitzpatrick-stratified metrics", "Calibration", "Clinician agreement study"],
         supported_populations="Unknown", limitations=["No model exists", "Photo alone may not establish cause or suitability for treatment"],
         declared_status="unavailable"),
    dict(id="pigmentation", name="Pigmentation and uneven tone", version="0.0.0",
         intended_use="Possible future: describe visible uneven tone for clinician review.",
         required_image_type="face photograph", required_labels=["pigmentation label"],
         required_dataset="Standardised-lighting facial images with pigmentation labels (none identified)",
         architecture="CNN classifier or segmentation", evaluation_requirements=["Patient-level splits", "Skin-tone-stratified metrics"],
         supported_populations="Unknown", limitations=["No suitable public dataset identified", "Lighting strongly confounds tone"],
         declared_status="unavailable"),
    dict(id="redness_irritation", name="Redness and visible irritation", version="0.0.0",
         intended_use="Possible future.", required_image_type="face photograph", required_labels=["erythema label"],
         required_dataset="None identified", architecture="CNN", evaluation_requirements=["As above"],
         supported_populations="Unknown", limitations=["Redness is hard to see on darker skin; high bias risk"], declared_status="unavailable"),
    dict(id="dryness_scaling", name="Dryness and scaling", version="0.0.0",
         intended_use="Possible future.", required_image_type="face or body photograph", required_labels=["scaling label"],
         required_dataset="None identified", architecture="CNN", evaluation_requirements=["As above"],
         supported_populations="Unknown", limitations=["No model exists"], declared_status="unavailable"),
    dict(id="texture_scarring", name="Texture and scarring", version="0.0.0",
         intended_use="Possible future.", required_image_type="face photograph", required_labels=["scar label"],
         required_dataset="None identified", architecture="CNN or segmentation (needs masks)", evaluation_requirements=["As above"],
         supported_populations="Unknown", limitations=["No model exists"], declared_status="unavailable"),
    dict(id="hair_thinning", name="Hair thinning appearance", version="0.0.0",
         intended_use="Possible future: record visible thinning pattern for clinician review.",
         required_image_type="scalp and hair photograph (top/crown/hairline)", required_labels=["hair density grade"],
         required_dataset="Scalp images with density/Norwood-Ludwig labels (none identified as openly licensed)",
         architecture="CNN classifier", evaluation_requirements=["Patient-level splits", "Hair-type-stratified metrics"],
         supported_populations="Unknown", limitations=["No model exists", "Cause of hair loss cannot be determined from a photo"],
         declared_status="unavailable"),
    dict(id="scalp_findings", name="Visible scalp findings", version="0.0.0",
         intended_use="Possible future.", required_image_type="scalp photograph (trichoscopy preferred)", required_labels=["scalp finding label"],
         required_dataset="None identified", architecture="CNN", evaluation_requirements=["As above"],
         supported_populations="Unknown", limitations=["Many scalp conditions need trichoscopy/examination"], declared_status="unavailable"),
    dict(id="lesion_classification_research", name="Skin lesion classification (research)", version="0.1.0",
         intended_use="Research benchmarking only: demonstrates the training pipeline on dermoscopic lesion datasets. Not offered to patients.",
         required_image_type="dermoscopic or clinical lesion photograph", required_labels=["lesion diagnosis"],
         required_dataset="PAD-UFES-20 and ISIC clinical photographs subject to licence gate", architecture="Transfer-learned CNN",
         evaluation_requirements=["Patient/lesion-level split", "Per-class recall", "Skin-tone stratified metrics", "Calibration"],
         supported_populations="Datasets are skewed towards lighter skin tones", limitations=["Not clinically validated", "Not for ordinary face photos"],
         declared_status="research_only"),
    dict(id="dermoscopy_lesion_research", name="Dermoscopic lesion classification (research)", version="0.1.0",
         intended_use="Research benchmarking only: eight diagnostic classes on dermoscopic images. A dermoscope is not a patient's phone camera; never offered to patients.",
         required_image_type="dermoscopic lesion image", required_labels=["lesion diagnosis"],
         required_dataset="ISIC contributor-licensed subsets and DERM12345, subject to the licence gate", architecture="Frozen self-supervised backbone with a linear head",
         evaluation_requirements=["Patient-level split", "Per-class sensitivity for melanoma, BCC and SCC", "External validation on a different centre", "Skin-tone stratified metrics where recorded", "Calibration"],
         supported_populations="Mostly lighter skin tones; Fitzpatrick type recorded for a subset only", limitations=["Not clinically validated", "Dermoscopic images only"],
         declared_status="research_only"),
    dict(id="skin_condition_research", name="Common skin condition classification (research)", version="0.1.0",
         intended_use="Research benchmarking only: common inflammatory and infectious skin conditions on smartphone photographs, evaluated across skin tones. Never offered to patients.",
         required_image_type="clinical photograph of skin", required_labels=["condition label"],
         required_dataset="SCIN subject to the licence gate", architecture="Frozen self-supervised backbone with a linear head",
         evaluation_requirements=["Case-level split", "Skin-tone stratified metrics (dermatologist-estimated Fitzpatrick and Monk)", "Calibration", "Behaviour on non-skin images"],
         supported_populations="Volunteers in the United States; labels are dermatologist opinions from photographs", limitations=["Closed set: has no 'other' class", "Not clinically validated"],
         declared_status="research_only"),
    dict(id="other_uncertain", name="Other or uncertain concern", version="0.0.0",
         intended_use="Always routes to clinician review.", required_image_type="any", required_labels=[], required_dataset="n/a",
         architecture="none", evaluation_requirements=[], supported_populations="n/a",
         limitations=["No automated assessment"], declared_status="unavailable"),
]


def list_tasks(approvals: dict | None = None) -> list[dict]:
    out = []
    for t in TASKS:
        eff = st.effective_status(t["declared_status"], (approvals or {}).get(t["id"]))
        out.append(dict(t, status=st.describe(eff)))
    return out


def get_task(task_id: str) -> dict | None:
    return next((t for t in list_tasks() if t["id"] == task_id), None)


def predict(task_id: str, *_a, **_k):
    """Hard stop: no fake predictions for tasks that have no evaluated model."""
    t = get_task(task_id)
    if not t:
        raise KeyError(task_id)
    if t["status"]["status"] in ("unavailable",):
        raise NotImplementedError("No validated model exists for this task. Please ask the clinic team for review.")
    raise NotImplementedError("This task is for research use only and is not offered to patients.")

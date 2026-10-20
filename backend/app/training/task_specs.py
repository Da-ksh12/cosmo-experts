"""Structured specifications of the image tasks, for the reusable training pipeline.

`tasks.py` says what each task is for in prose; this module says what a legitimate training run for it REQUIRES, so a run can be checked mechanically: the actual task definition,
the label scheme, how data must be split, the minimum data, and the candidate datasets in the catalogue. A task whose requirements cannot be met today is reported as BLOCKED with the
reasons: the pipeline is finished, the data is what is missing. No dataset is invented, and nothing here makes a task available (availability comes from the model lifecycle).

Segmentation is deliberately not implemented: it needs pixel-level masks and a real use case. No catalogued dataset provides masks for these tasks (`segmentation_supported=False`)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    intended_task: str
    output_type: str                       # classification | ordinal_classification
    label_schema: tuple[str, ...] | None   # None: the dataset's documented scale defines the labels and a class mapping must be recorded
    unit_of_analysis: str                  # what one example is
    min_images_per_class: int
    min_subjects: int                      # distinct patients across all splits
    needs_skin_type: bool                  # Fitzpatrick (or equivalent) needed to evaluate performance across skin tones
    metrics: tuple[str, ...]
    candidate_datasets: tuple[str, ...]    # ids in datasets/registry.py SEED
    critical_classes: tuple[str, ...] = ()
    segmentation_supported: bool = False
    notes: str = ""


_COMMON = ("macro_f1", "balanced_accuracy", "per_class_sensitivity_specificity", "calibration_ece", "subgroup_performance", "ood_behaviour")

SPECS: dict[str, TaskSpec] = {s.task_id: s for s in (
    TaskSpec("acne_features", "Grade the visible severity of acne on a frontal face photograph, on the scale used by the dataset (clinician-labelled).", "ordinal_classification", None,
             "face photograph", 100, 100, True, _COMMON + ("quadratic_weighted_kappa",), ("acne04",),
             notes="ACNE04 terms and face-image consent are unclear (catalogue: unreviewed): training stays blocked until an administrator verifies the terms."),
    TaskSpec("pigmentation", "Classify visible uneven pigmentation on a standardised-lighting face photograph.", "classification", None, "face photograph", 100, 100, True, _COMMON, (),
             notes="No suitable public dataset identified; lighting strongly confounds tone."),
    TaskSpec("redness_irritation", "Classify visible redness/irritation on a face photograph.", "classification", None, "face photograph", 100, 100, True, _COMMON, (),
             notes="Redness is harder to see on darker skin: a skin-tone-stratified evaluation is a precondition, not an extra."),
    TaskSpec("dryness_scaling", "Classify visible dryness or scaling on a face or body photograph.", "classification", None, "face or body photograph", 100, 100, True, _COMMON, ()),
    TaskSpec("texture_scarring", "Classify visible texture irregularity or scarring on a face photograph.", "classification", None, "face photograph", 100, 100, True, _COMMON, (),
             notes="Needs clinician-labelled scars; segmentation would need masks."),
    TaskSpec("hair_thinning", "Grade the visible pattern of hair thinning on a scalp photograph (crown, top, hairline) on a published scale.", "ordinal_classification", None,
             "scalp photograph", 100, 100, False, _COMMON, (), notes="Cause of hair loss cannot be determined from a photograph; hair-type-stratified evaluation required."),
    TaskSpec("scalp_findings", "Classify visible scalp findings on a scalp (ideally trichoscopy) photograph.", "classification", None, "scalp photograph", 100, 100, False, _COMMON, ()),
    TaskSpec("lesion_classification_research", "Research benchmark: classify a skin lesion photograph into the dataset's diagnostic categories. Never offered to patients.", "classification", None,
             "lesion photograph", 20, 200, True, _COMMON + ("critical_class_sensitivity",), ("pad-ufes-20", "isic-clinical"), critical_classes=("MEL", "BCC", "SCC"),
             notes="Previously trained models failed their release gates; this task stays research-only."),
    TaskSpec("dermoscopy_lesion_research", "Research benchmark: classify a dermoscopic image into eight diagnostic classes. Never offered to patients.", "classification",
             ("NV", "MEL", "BCC", "SCC", "AK", "BKL", "DF", "VASC"), "dermoscopic image", 100, 200, True, _COMMON + ("critical_class_sensitivity", "external_validation"), ("isic-dermoscopy", "derm12345"),
             critical_classes=("MEL", "BCC", "SCC"), notes="Dermoscopy only; Fitzpatrick type is recorded for a minority of the images, so skin-tone results rest on that subset."),
    TaskSpec("skin_condition_research", "Research benchmark: classify a smartphone photograph into common skin conditions, reported by skin tone. Never offered to patients.", "classification", None,
             "photograph (cases are split as a whole)", 100, 100, True, _COMMON, ("scin",), notes="Closed set with no 'other' class; labels are dermatologist opinions, not biopsy results."),
)}


def get(task_id: str) -> TaskSpec | None:
    return SPECS.get(task_id)

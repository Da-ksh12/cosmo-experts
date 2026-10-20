# Phase 5 training report: datasets, models and what they can and cannot do (2026-10-20)

Written for the project owner and the engineers who continue with the product and the app. Plain language; every figure is copied from the model cards in [docs/eval/phase5/](eval/phase5/) (exported by `backend/scripts/phase5_export.py`),
the job ledger is [docs/eval/phase5_jobs.json](eval/phase5_jobs.json), and the licence decisions with their evidence hashes are [docs/eval/dataset_decisions.json](eval/dataset_decisions.json).
**None of the models below is approved, active, clinically validated or shown to patients.** They are experimental research output for administrators.

## 1. Result in brief

* **Data.** Four more licence-verified sources were downloaded from their official hosts and imported: ISIC dermoscopy (11,974 images), ISIC clinical photographs (474), SCIN (2,842) and DERM12345 (4,821). That is 20,111 new images, 22,382 labelled images in all. Nine licence
  decisions are recorded with hashed evidence: 4 approvals and 5 refusals (HAM10000, Fitzpatrick17k, DDI, ACNE04, and the ISIC Archive as a whole because it contains a non-commercial pool). A survey of 970 further candidates found nothing usable for acne, pigmentation, redness, scarring, hair or scalp.
* **Models.** Five research models were trained and evaluated on the CPU (about 2.2 hours of training and evaluation, plus about an hour of downloads) and registered as experimental. None is approved, active or shown to patients.
* **What they do.** Phone photographs of six lesion types: macro-F1 **0.559** (interval 0.496-0.614); the 474 extra independent photographs changed nothing (0.558 before). Eleven common skin conditions (SCIN): **0.347** (0.287-0.395), accuracy 34%. Eight dermoscopic lesion types:
  **0.404** (0.368-0.437) on held-out ISIC images and **0.405** on a second centre (DERM12345) that the model never saw; adding DERM12345 to the training data gave 0.472 on a different, easier test mix, with no gain on the ISIC images (balanced accuracy 0.495 against 0.501).
* **What they cannot do.** Not one passes the release gates, and none is close. Sensitivity for melanoma and the two common skin cancers is 0.41-0.57 where the gate asks 0.80; darker skin is untested for every lesion model (4-8 test images; only SCIN has 85); and every model is more confident on non-skin pictures than on real test pictures (out-of-distribution AUROC 0.26-0.37 where 0.5 is chance), so an input check must come first.
* **The lesson.** Public data of the kinds that exist did not make these models usable: the frozen general-purpose backbone sees too little of dermoscopic detail (a ResNet18 head on ImageNet features is worse, 0.34, but a fine-tuned network would very likely do much better and was not run), and the data the product needs most (acne, pigmentation, redness, scarring, hair, scalp, dark skin) does not exist with traceable rights.
  Seven of the ten tasks therefore stay blocked (section 8), and the next real gains need either GPU time for fine-tuning or new, consented, clinician-graded data. Legal review of the ISIC and SCIN licences is advised before any commercial use.

## 2. Hardware, checked once, and how the pipeline adapted

| Item | Value |
|---|---|
| CPU | Intel Core i5-5350U, 2 physical / 4 logical cores |
| Memory | 8.5 GB installed; between 0.2 and 2.2 GB free while the jobs ran (other applications) |
| Accelerator | none (no CUDA, no MPS), PyTorch 2.14.1 CPU build. No job tried a GPU; the GPU path has never been run here and stays reported as unverified |
| Disk | 154 GB free on F: (datasets, image caches, feature stores; all outside Git); about 6 GB free on C: |
| Measured speed | DINOv2 ViT-S/14: 3.1-7.3 images/s depending on threads, batch and background load; ResNet18: 16-18 images/s; decoding and resizing 20-40 images/s. Downloads: SCIN 3.5 GB in 13 min; ISIC (listing 52,207 records and downloading 11,974 thumbnails) 34 min; DERM12345 (6.5 GB of archives) 15 min |

What the pipeline decided by itself (recorded on every card under `resources`): the thread count and batch size from a 24-image measurement on the real machine, bounded by the memory that was free at that moment (3-4 threads, batch 8-16);
fp32 (mixed precision is used only on CUDA); a memory-mapped, resumable image cache and feature store so an interrupted run continues; and a safe pause instead of a failure when free memory stays below 120 MB for 90 s.
Each dataset's features were extracted once and shared by every later experiment that used it (PAD-UFES-20 for two models, the ISIC dermoscopy and DERM12345 features for two).

**Not attempted, and why.** Fine-tuning a vision transformer, a larger DINOv2, or training above 224 px: days of CPU time per run on this machine. A ResNet18 layer-4 fine-tune on the new dermoscopy data (estimated 1.5 hours, code in
`backend/app/training/finetune.py`, unit-tested on synthetic images): skipped to finish the phase; the like-for-like comparison on PAD-UFES-20 from Phase 3 stands (section 6).

## 3. Data: what was found, decided and imported

| Source | Licence (verified from the publisher) | Decision | Images used | Subjects | Notes |
|---|---|---|---|---|---|
| PAD-UFES-20 | CC BY 4.0 | approved earlier | 2,271 | 1,363 patients | 6 classes, phone photographs |
| ISIC Archive, dermoscopic (CC-0/CC-BY subset) | per image CC-0 or CC-BY | **approved** | 11,974 | 6,601 | 8 classes; 20 images of Fitzpatrick IV, none of V-VI |
| ISIC Archive, clinical photographs (CC-0/CC-BY subset) | per image CC-0 or CC-BY | **approved** | 474 | 372 | 6 classes; 2,298 PAD-UFES-20 copies excluded |
| SCIN (Google) | SCIN Data Use License | **approved** | 2,842 | 1,353 cases | 11 conditions; 595 images of Fitzpatrick IV-VI |
| DERM12345 | CC BY 4.0 | **approved** | 4,821 | 1,236 | 8 classes; no skin-tone labels |
| ISIC Archive as a whole | mixed incl. CC-BY-NC | refused | 0 | | only the licence-filtered subsets are used |
| HAM10000, Fitzpatrick17k, DDI, ACNE04 | CC BY-NC 4.0; BY-NC-SA 3.0; non-commercial; none | refused | 0 | | with the publishers' own statements |
| 13 more candidates read in detail (FitzDerm-CF, an acne set on Hugging Face, two Figshare sets, ScalpHairLabels and others) | various | not used | 0 | | rights untraceable, derived from non-commercial data, synthetic, or not labelled for any task; see [dataset_discovery.json](eval/dataset_discovery.json) |

Totals: **22,382 labelled images** are now registered (20,111 new). A survey of 970 candidates on Zenodo, Figshare, Harvard Dataverse and Hugging Face found nothing with traceable rights and labels for acne, pigmentation, redness,
scarring, hair or scalp. All downloads were verified against the host's own checksums (0 failures among about 15,300 files and archives); archives were extracted with the bounded safe extractor; nothing was committed to Git.
Class and skin-tone distributions, exclusions and defects are in [DATASET_CATALOG.md](../DATASET_CATALOG.md).

**Leakage control.** Subjects (patients, lesions or SCIN cases) never straddle train, validation and test; photographs with no identifier are used for training only (1,686 ISIC dermoscopy images, 135 ISIC clinical images); an external
source is never used for training or selection; 32x32-thumbnail near-duplicates of a training image were removed from validation, test and external sets before any metric (counts are on each card); the copies of PAD-UFES-20 inside ISIC (2,298 images) were
excluded, which would otherwise have put test images into training. No training image carries an EXIF rotation, so the cache and the serving path read the pixels the same way.

## 4. Jobs

| Job | What it did | Time | Result |
|---|---|---|---|
| data `isic-clinical` | listed 4,028 records, excluded 2,298 PAD-UFES-20 copies, downloaded 474 full-size photographs | 131 s + 12 s import | version 2 (version 1 invalidated) |
| data `scin` | read 5,033 cases, selected 2,852 images, downloaded 3.5 GB with bucket checksums | 789 s + 3.3 min import | version 1, 2,842 images |
| data `isic-dermoscopy` | listed 52,207 records, selected and downloaded 11,974 thumbnails with S3 ETag checks | 2,062 s + 5.5 min import | version 1 |
| data `derm12345` | downloaded 6.5 GB of archives (MD5 verified), safe-extracted, selected 4,821 | 910 s + 8.3 min import | version 1 |
| governance | recorded 9 licence decisions (4 approvals, 5 refusals) with hashed evidence | minutes | [dataset_decisions.json](eval/dataset_decisions.json) |

Training and evaluation runs (all on the CPU; the same runs are in the training-run history table and in [phase5_jobs.json](eval/phase5_jobs.json)):

| Run | Experiment | Task | Status | Minutes | Device |
|---|---|---|---|---|---|
| `92239063` | `clinical-pad-only` | lesion_classification_research | done | 22.0 | cpu |
| `0ff772fd` | `clinical-pad-isic` | lesion_classification_research | done | 8.6 | cpu |
| `e36adfdf` | `skin-condition-scin` | skin_condition_research | done | 17.1 | cpu |
| `ecbb813d` | `derm-isic` | dermoscopy_lesion_research | done | 81.2 | cpu |
| `a7cbadb7` | `derm-isic-derm12345` | dermoscopy_lesion_research | done | 4.9 | cpu |

Tasks that were **blocked** and therefore not run: `acne_features`, `pigmentation`, `redness_irritation`, `dryness_scaling`, `texture_scarring`, `hair_thinning`, `scalp_findings` (section 8). No job failed, was cancelled or hit the memory-pause during Phase 5.

## 5. Results

How to read the tables. **Macro-F1** averages the F1 of every class equally, so a rare class counts as much as a common one; chance for k classes is about 1/k and "always the most common class" scores 0.04-0.09 here. The interval is a 95% bootstrap interval.
Every number is on held-out subjects the model never trained on or was selected with; small test sets are noisy. A "gate" is a release check from Phase 3 whose thresholds were not changed in Phase 5.

### Table 1. Held-out test results (macro-F1 is the headline; every model is experimental)

| Experiment | Model | Task | Test n | Macro-F1 (95% CI) | Balanced acc. | Accuracy | ECE | Best baseline macro-F1 | Gates passed |
|---|---|---|---|---|---|---|---|---|---|
| `clinical-pad-only` | `c128eb96` | lesion_classification_research | 325 | **0.558** (0.471-0.630) | 0.576 | 0.615 | 0.035 | 0.508 | 7/11 |
| `clinical-pad-isic` | `0f7e066f` | lesion_classification_research | 374 | **0.559** (0.496-0.614) | 0.578 | 0.61 | 0.064 | 0.476 | 8/11 |
| `skin-condition-scin` | `e0ce44b3` | skin_condition_research | 406 | **0.347** (0.287-0.395) | 0.377 | 0.34 | 0.041 | 0.223 | 9/10 |
| `derm-isic` | `bab527c0` | dermoscopy_lesion_research | 1343 | **0.404** (0.368-0.437) | 0.501 | 0.517 | 0.026 | 0.344 | 8/11 |
| `derm-isic-derm12345` | `0e29da33` | dermoscopy_lesion_research | 2053 | **0.472** (0.444-0.497) | 0.534 | 0.586 | 0.047 | 0.405 | 8/11 |

### Table 2. Baselines on the same test images (macro-F1)

| Experiment | This model | Majority class | DINOv2 k-NN | ResNet18-ImageNet frozen + same head |
|---|---|---|---|---|
| `clinical-pad-only` | 0.558 | 0.089 | 0.423 | 0.508 |
| `clinical-pad-isic` | 0.559 | 0.089 | 0.409 | 0.476 |
| `skin-condition-scin` | 0.347 | 0.04 | 0.223 | 0.206 |
| `derm-isic` | 0.404 | 0.047 | 0.281 | 0.344 |
| `derm-isic-derm12345` | 0.472 | 0.077 | 0.351 | 0.405 |

### Table 3. Release gates that failed (thresholds unchanged)

| Experiment | Failed gates and why |
|---|---|
| `clinical-pad-only` | **improvement_supported_by_ci**: 95% CI lower bound 0.471 must exceed the best baseline 0.508; **critical_class_sensitivity**: MEL: only 7 test images; BCC: sensitivity 0.546; SCC: sensitivity 0.407; **dark_skin_evaluated**: Fitzpatrick 4-6 test images: 8 (need >= 50); **ood_detection**: confidence separates lesion from non-lesion images with AUROC 0.349 (need >= 0.9) |
| `clinical-pad-isic` | **critical_class_sensitivity**: MEL: only 17 test images; BCC: sensitivity 0.566; SCC: sensitivity 0.412; **dark_skin_evaluated**: Fitzpatrick 4-6 test images: 8 (need >= 50); **ood_detection**: confidence separates lesion from non-lesion images with AUROC 0.361 (need >= 0.9) |
| `skin-condition-scin` | **ood_detection**: confidence separates lesion from non-lesion images with AUROC 0.259 (need >= 0.9) |
| `derm-isic` | **critical_class_sensitivity**: MEL: sensitivity 0.484; BCC: sensitivity 0.478; SCC: sensitivity 0.519; **dark_skin_evaluated**: Fitzpatrick 4-6 test images: 4 (need >= 50); **ood_detection**: confidence separates lesion from non-lesion images with AUROC 0.296 (need >= 0.9) |
| `derm-isic-derm12345` | **critical_class_sensitivity**: MEL: sensitivity 0.503; BCC: sensitivity 0.495; SCC: sensitivity 0.43; **dark_skin_evaluated**: Fitzpatrick 4-6 test images: 4 (need >= 50); **ood_detection**: confidence separates lesion from non-lesion images with AUROC 0.374 (need >= 0.9) |

### Table 4. Sensitivity of the serious classes on the held-out test images

| Experiment | Class | Test n | Sensitivity | Specificity | Precision |
|---|---|---|---|---|---|
| `clinical-pad-only` | MEL | 7 | 0.429 | 0.987 | 0.429 |
| `clinical-pad-only` | BCC | 119 | 0.546 | 0.874 | 0.714 |
| `clinical-pad-only` | SCC | 27 | 0.407 | 0.862 | 0.212 |
| `clinical-pad-isic` | MEL | 17 | 0.471 | 0.972 | 0.444 |
| `clinical-pad-isic` | BCC | 136 | 0.566 | 0.895 | 0.755 |
| `clinical-pad-isic` | SCC | 34 | 0.412 | 0.874 | 0.246 |
| `derm-isic` | MEL | 310 | 0.484 | 0.905 | 0.605 |
| `derm-isic` | BCC | 245 | 0.478 | 0.913 | 0.552 |
| `derm-isic` | SCC | 133 | 0.519 | 0.928 | 0.442 |
| `derm-isic-derm12345` | MEL | 318 | 0.503 | 0.924 | 0.548 |
| `derm-isic-derm12345` | BCC | 222 | 0.495 | 0.933 | 0.472 |
| `derm-isic-derm12345` | SCC | 121 | 0.43 | 0.955 | 0.377 |

### Table 5. External validation (never trained on, selected with, or calibrated on)

| Experiment | External source | n | Macro-F1 (95% CI) | Balanced acc. | Accuracy | Near-duplicates removed |
|---|---|---|---|---|---|---|
| `derm-isic` | derm12345 | 4061 | 0.405 (0.383-0.429) | 0.482 | 0.65 | 760 |

### Table 6. Skin-tone subgroups of the held-out test images (Fitzpatrick; 'reliable' needs 50 images)

| Experiment | Group | n | Accuracy | Balanced acc. | Reliable |
|---|---|---|---|---|---|
| `clinical-pad-only` | 1-2 | 147 | 0.503 | 0.514 | yes |
| `clinical-pad-only` | 3 | 58 | 0.603 | 0.569 | yes |
| `clinical-pad-only` | 4-6 | 8 | 0.5 | None | no |
| `clinical-pad-only` | NA | 112 | 0.777 | 0.756 | yes |
| `clinical-pad-isic` | 1-2 | 193 | 0.528 | 0.512 | yes |
| `clinical-pad-isic` | 3 | 61 | 0.574 | 0.585 | yes |
| `clinical-pad-isic` | 4-6 | 8 | 0.625 | None | no |
| `clinical-pad-isic` | NA | 112 | 0.768 | 0.732 | yes |
| `skin-condition-scin` | 1-2 | 184 | 0.332 | 0.353 | yes |
| `skin-condition-scin` | 3 | 129 | 0.326 | 0.38 | yes |
| `skin-condition-scin` | 4-6 | 85 | 0.388 | 0.471 | yes |
| `skin-condition-scin` | NA | 8 | 0.25 | None | no |
| `derm-isic` | 1-2 | 102 | 0.529 | 0.523 | yes |
| `derm-isic` | 3 | 38 | 0.368 | 0.305 | no |
| `derm-isic` | 4-6 | 4 | 0.25 | None | no |
| `derm-isic` | NA | 1199 | 0.521 | 0.509 | yes |
| `derm-isic-derm12345` | 1-2 | 102 | 0.5 | 0.47 | yes |
| `derm-isic-derm12345` | 3 | 38 | 0.421 | 0.348 | no |
| `derm-isic-derm12345` | 4-6 | 4 | 0.25 | None | no |
| `derm-isic-derm12345` | NA | 1909 | 0.595 | 0.536 | yes |

### Table 7. Calibration, robustness and out-of-distribution behaviour

| Experiment | ECE before -> after temperature scaling | Clean macro-F1 (same subset) | blur | dark | jpeg20 | rotate90 | OOD AUROC (0.5 = chance) |
|---|---|---|---|---|---|---|---|
| `clinical-pad-only` | 0.105 -> 0.035 (T=1.3) | 0.54 | 0.472 | 0.539 | 0.281 | 0.542 | 0.349 |
| `clinical-pad-isic` | 0.084 -> 0.064 (T=1.15) | 0.542 | 0.435 | 0.541 | 0.335 | 0.528 | 0.361 |
| `skin-condition-scin` | 0.201 -> 0.041 (T=2.15) | 0.365 | 0.216 | 0.354 | 0.226 | 0.36 | 0.259 |
| `derm-isic` | 0.041 -> 0.026 (T=1.05) | 0.417 | 0.334 | 0.412 | 0.315 | 0.392 | 0.296 |
| `derm-isic-derm12345` | 0.038 -> 0.047 (T=0.95) | 0.418 | 0.308 | 0.429 | 0.351 | 0.465 | 0.374 |

### Table 9. Resources used by each model

| Experiment | Training seconds | Peak working set (MB) | Artifact (KB) | Threads / batch (auto-tuned) | img/s measured |
|---|---|---|---|---|---|
| `clinical-pad-only` | 1332 | 1081 | 16.6 | 4 / 8 | 5.03 |
| `clinical-pad-isic` | 530 | 1211 | 31.6 | 3 / 16 | 4.13 |
| `skin-condition-scin` | 1031 | 1200 | 47.7 | 4 / 16 | 6.52 |
| `derm-isic` | 4879 | 3137 | 37.6 | 4 / 16 | 7.32 |
| `derm-isic-derm12345` | 306 | 3347 | 37.6 | 2 / 8 | 6.73 |

### What the results say

**Phone photographs of lesions (PAD-UFES-20, with and without the ISIC clinical photographs).** The first model reproduces the Phase 3 DINOv2 probe exactly (macro-F1 0.558 on the same 325 test images), which validates the new pipeline. Adding the 474 independent ISIC photographs did not move the headline
(0.559). The test set grew from 325 to 374 images (the same 325 plus 49 ISIC photographs), which narrowed the interval and, because the best baseline is lower on the larger set (0.508 to 0.476), turned one gate (interval above the best baseline) from fail to pass: that is a statistical effect, not a better model. On the identical 325
PAD-UFES-20 images accuracy went from 0.615 to 0.622 and balanced accuracy from 0.576 to 0.591, within noise. The 49 ISIC photographs scored a balanced accuracy of 0.486, too few to rely on. Melanoma has only 17 test images (sensitivity 0.47); basal cell carcinoma 0.57; squamous cell carcinoma 0.41. Fitzpatrick IV-VI has 8 test images.

**Eleven common skin conditions (SCIN).** Macro-F1 0.347 and accuracy 0.34 against 0.09 for guessing and 0.22 for the best baseline: real signal, far from usable. Sensitivity ranges from 0.63 (psoriasis, 19 test images) and 0.57 (acne, 14) down to 0.16 (tinea, 19), 0.21 (herpes simplex, 14) and 0.22 (allergic contact dermatitis, 69). Scored per case
(a case's photographs averaged, 193 test cases) the macro-F1 is 0.410 (0.316-0.484). SCIN is the only source with enough darker-skin test images to report: 85 images of Fitzpatrick IV-VI, balanced accuracy 0.47 against 0.35 for types I-II. That gap is not evidence of better performance on darker skin: the condition mix differs between groups and
the skin-tone labels are dermatologists' estimates. The raw model was overconfident (calibration error 0.201); temperature scaling on validation brought it to 0.041.

**Dermoscopy (ISIC, and DERM12345 as a second centre).** The probe reaches macro-F1 0.404 on 1,343 held-out ISIC images and 0.405 (0.383-0.429) on 4,061 DERM12345 images it never saw, selected on or calibrated with. Agreement between the two centres argues against leakage or overfitting: the model is simply weak. It is better than the
alternatives measured (ResNet18 ImageNet head 0.344, DINOv2 nearest-neighbour 0.281, majority class 0.047), but at the melanoma operating point it finds 48% of melanomas on ISIC (60% of its melanoma calls are right) and 58% on DERM12345 where only 22% of its melanoma calls are right: most alarms on the second centre are false. Nevi dominate DERM12345, which is why its
accuracy (0.65) looks better than its macro-F1. Training on DERM12345 as well (`derm-isic-derm12345`, official patient-level split, 1 patient who appeared in both official splits moved to training) gives 0.472 on 2,053 test images, but that is a different test set (1,225 ISIC plus 828 DERM12345 images, the latter easier: accuracy 0.71): on the ISIC images alone balanced accuracy is 0.495
against 0.501 before. **The two dermoscopy headlines must not be compared with each other.** The near-duplicate filter removed 760 of 4,821 DERM12345 images, and 99-341 validation and 127-383 test images, as near-copies of an ISIC training image: the two public sets overlap. The filter is deliberately conservative (a 32x32 thumbnail correlation of 0.985) and may also
remove some merely similar images, which makes the reported numbers, if anything, pessimistic.

**Across all five models.**
* *No model clears the release gates* and every one fails the serious-class, dark-skin or out-of-distribution gate (Table 3). Thresholds were not changed.
* *Calibration works after temperature scaling* (error 0.026-0.064 everywhere); the unscaled SCIN model was the worst (0.201).
* *Confidence cannot flag unsuitable images*: non-skin pictures get higher confidence than real test pictures (AUROC 0.26-0.37 against 0.5 for chance). The phone-photo and SCIN models have an input-quality check in front of them (size, exposure, sharpness, contrast) that does not recognise "not skin"; the dermoscopy models have none.
* *Robustness*: heavy JPEG compression (quality 20) and blur cost the most (macro-F1 falls by 0.1-0.25 on every model); darkening to 45% and a 90 degree rotation cost little (Table 7).
* *More data of the same kind did not help the phone-photo model; the method limits dermoscopy; and the data the product needs most does not exist with traceable rights.* The Phase 3 conclusion stands, with sharper evidence.
* *Serving matches evaluation*: for four of the models, 60 raw PNG or JPEG test files each, sent through the research interface, gave the same top class as the evaluation path in 60 of 60 cases, with a largest probability difference of 0.00005 ([eval/phase5_serving_consistency.json](eval/phase5_serving_consistency.json)).

## 6. Which method? (transfer learning, pretrained features, resource-efficient options)

| Method | Data | Test images | Macro-F1 (95% CI) | Cost on this machine |
|---|---|---|---|---|
| ResNet18 layer-4 fine-tune (Phase 3) | PAD-UFES-20 | 325 | 0.502 (0.429-0.576) | 1,199 s for 6 epochs |
| ResNet18 frozen features + logistic regression | PAD-UFES-20 | 325 | 0.508 | seconds once features exist |
| DINOv2 k-nearest-neighbour | PAD-UFES-20 | 325 | 0.423 | seconds |
| **DINOv2 frozen features + logistic regression (chosen)** | PAD-UFES-20 | 325 | **0.558 (0.471-0.630)** | one feature pass (0.2 s/image), then seconds |
| int8 dynamic quantisation of the DINOv2 backbone | speed check | - | cosine similarity to the float model 0.966 | +10% speed: rejected |
| DINOv2 frozen features + logistic regression (`derm-isic`) | ISIC dermoscopy | 1343 | **0.404** (0.368-0.437) | one feature pass, then seconds |
| ResNet18 frozen features + same head (`derm-isic`) | same | 1343 | 0.344 | seconds once features exist |
| DINOv2 k-nearest-neighbour (`derm-isic`) | same | 1343 | 0.281 | seconds |
| DINOv2 frozen features + logistic regression (`derm-isic-derm12345`) | ISIC dermoscopy + DERM12345 | 2053 | **0.472** (0.444-0.497) | one feature pass, then seconds |
| ResNet18 frozen features + same head (`derm-isic-derm12345`) | same | 2053 | 0.405 | seconds once features exist |
| DINOv2 k-nearest-neighbour (`derm-isic-derm12345`) | same | 2053 | 0.351 | seconds |

Reading: on the small phone-photo set the fine-tune did not beat frozen features, and the DINOv2 head is ahead of the ResNet18 head by 0.05, but the ResNet18 figure lies inside the probe's interval, so the evidence for DINOv2 over ResNet18 is suggestive, not proven. The probe's cost structure
(one expensive pass per image, then everything else is cheap) is what made five experiments over 22,000 images feasible on 2 cores. DINOv2 (Apache-2.0) is the backbone of every registered model; the ResNet18-ImageNet weights are used only for these comparisons because the
licence status of the ImageNet data they were trained on is unresolved for commercial use.

## 7. Registered models and their approval state

Every model is stored in the model registry as `research_only` with lifecycle state `experimental` and no approval record; it is served only to administrators through the existing research interface (checksums of the artifact and the backbone are verified before
loading, the card's own input check and warnings are applied, and every answer is labelled research output). Nothing replaces or competes with an approved model, because none exists. A model becomes eligible for review only when every gate passes
**and** a verified clinician signs off an intended use; none is close.

| Model | Experiment | Task | Registry status | Lifecycle | Approval | Artifact sha256 (first 16) | Classes |
|---|---|---|---|---|---|---|---|
| `c128eb967c3f481bb25e8d9adbb71a43` | `clinical-pad-only` | lesion_classification_research | research_only | experimental | none | `fad05a260f1250c3` | ACK, BCC, MEL, NEV, SCC, SEK |
| `0f7e066f6b734e1092e17218fb6e1881` | `clinical-pad-isic` | lesion_classification_research | research_only | experimental | none | `81a9c43f409ca492` | ACK, BCC, MEL, NEV, SCC, SEK |
| `e0ce44b3a7894fac916e7d659f1efbe4` | `skin-condition-scin` | skin_condition_research | research_only | experimental | none | `200619304c195d23` | Acne, Allergic Contact Dermatitis, Eczema, Folliculitis, Herpes Simplex, Herpes Zoster, Insect Bite, Pigmented purpuric eruption, Psoriasis, Tinea, Urticaria |
| `bab527c0778f497abc4366b4c421bebd` | `derm-isic` | dermoscopy_lesion_research | research_only | experimental | none | `911654d065a336ee` | AK, BCC, BKL, DF, MEL, NV, SCC, VASC |
| `0e29da33555a4a7b94399a3084e1da94` | `derm-isic-derm12345` | dermoscopy_lesion_research | research_only | experimental | none | `f9e6e22ce9ee3645` | AK, BCC, BKL, DF, MEL, NV, SCC, VASC |

## 8. Tasks still blocked, and what would unblock them

| Task | Why it cannot be trained | What would unblock it |
|---|---|---|
| `acne_features` (severity grading) | The only acne-severity set, ACNE04, has no licence and undocumented consent for the face images (refused). SCIN has 98 acne images but no grade | Written permission from the ACNE04 authors, or a consented, clinician-graded face dataset (the task needs at least 100 images per grade from at least 100 people, with skin tone recorded) |
| `pigmentation`, `redness_irritation`, `dryness_scaling`, `texture_scarring` | No dataset with traceable rights and matching labels exists in the survey (rosacea: 25 SCIN cases; scar: 12) | Prospectively collected, consented photographs with clinician grading under a standard capture protocol |
| `hair_thinning`, `scalp_findings` | Only ScalpHairLabels (108 images with hair masks, no hair-loss grade) | Scalp or trichoscopy photographs with density or condition labels, consented, with skin tone recorded |
| Dark-skin evidence for lesion tasks | 8 Fitzpatrick IV-VI test images for phone lesion photos; 20 images in all of ISIC dermoscopy | A licensed set with at least 50 dark-skin test images per task and enough malignant cases (e.g. a commercial licence for DDI from Stanford, or new consented collection) |
| Any approval | No clinician has reviewed anything; every model fails at least one gate | A verified clinician sign-off on a narrow intended use, plus legal and regulatory review |
| Larger or fine-tuned vision models | CPU-only machine with 8.5 GB RAM | A GPU or 16 GB+ of RAM; the GPU code path then needs its first verified run |

## 9. Defects found and fixed during the phase

* **PAD-UFES-20 inside ISIC.** 2,298 of the 4,028 ISIC clinical photographs are PAD-UFES-20's own images; pooling them would have put test images into training. They are excluded by attribution, and the first ISIC clinical import was invalidated for a separate query defect (below).
* **ISIC query silently dropped records.** A filter on `image_manipulation:"instrument only"` removed every record that lacks the field and selected 56 images instead of 474. Fixed (altered and synthetic are excluded instead); the wrong version is marked incomplete so no run can use it.
* **ISIC file sizes.** The size ISIC publishes is 3,173 bytes smaller than the file it serves, so every size check failed; downloads are now verified with the S3 ETag (MD5 of the served object).
* **Rate limiting.** The ISIC website answers 429 under load; requests now wait for `Retry-After`, evidence pages are cached for a day and each licence decision is recorded independently.
* **Image cache.** It raised "paused" after the last chunk, and left a memory map open so Windows refused the final rename; both fixed with regression tests. The backbone file was re-hashed on every call (88 MB each time); now once.
* **Serving text.** The serving warning hard-coded "did not meet every release gate"; it now lists the gates that actually failed. Cards carry their own warning text per dataset.
* **Card layout.** The first three Phase 5 cards stored two bookkeeping keys inside the robustness table, which broke the registry exporter and showed blank rows in the model card screen. Fixed in the code and in the three stored cards (no figure changed; audit entries record it).
* **Refusal reasons.** A refused dataset's blocker said only "not approved"; it now carries the recorded reason. An empty "landing page" evidence item on DERM12345 was removed and the decision re-recorded.
* **Known and left alone.** The registry's task-compatibility check is loose (it lists unrelated datasets as compatible); the licence gate and explicit source lists are what decide use, so it blocks nothing.

## 10. Reproduce

```
cd backend
.venv\Scripts\python -m scripts.phase5_governance                     # licence decisions (network)
.venv\Scripts\python -m scripts.phase5_data isic-clinical scin isic-dermoscopy derm12345
.venv\Scripts\python -m scripts.phase5_train run clinical-pad-only    # then clinical-pad-isic, skin-condition-scin, derm-isic, derm-isic-derm12345
.venv\Scripts\python -m scripts.phase5_export                         # per-model reports and the job ledger under docs/eval
.venv\Scripts\python -m scripts.export_registry ../MODEL_REGISTRY.md
.venv\Scripts\python -m pytest tests/test_phase5_data.py tests/test_phase5_training.py -q -p no:cacheprovider
```
Seed 17 everywhere; the configuration hash of every run is on its card and in the run history. A repeated run reuses the cached images and features, so it takes seconds to minutes instead of the original hours.

## 11. Limits and caveats

* **Nothing here is clinical evidence.** The labels are dermatologist or pathology labels from public datasets collected for other purposes; the test sets are small for the rare, serious classes; one centre dominates each source.
* **Licences.** The approvals were recorded by the automated Phase 5 run on the owner's instruction and are reversible in the administrator screen. ISIC's contributor licences (CC-0/CC-BY, version of CC-BY not stated) and the custom SCIN licence were accepted on a recorded permission document;
  **a lawyer should confirm them before any commercial use.** The ResNet18-ImageNet weights are unresolved for commercial use and appear only in comparisons.
* **Model selection used validation data only; the test sets were scored once.** The PAD-UFES-20 test images are the same ones the four Phase 3 models were scored on, so across experiments they are not independent evidence.
* **Gates measure process, not usefulness.** The SCIN model passes 9 of 10 gates with an accuracy of 34% on 11 conditions, because the Phase 3 gates test improvement over baselines, calibration and reporting, not an absolute floor; the failed out-of-distribution gate and the model's modest accuracy are the real
  limits. No gate was lowered, added or removed.
* **A confident answer does not mean a suitable picture.** All five models are *more* confident on non-skin pictures than on real test pictures (out-of-distribution AUROC 0.26-0.36 where 0.5 is chance), so an input check must come first; the dermoscopy models have none and must never see a phone photograph.
* **Skin tone.** SCIN's skin-tone labels are dermatologists' estimates from photographs. Only SCIN has enough Fitzpatrick IV-VI test images (85) for a reliable subgroup number; for every lesion model, darker skin is effectively untested.

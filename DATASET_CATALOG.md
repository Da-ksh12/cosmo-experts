# Dataset Catalog

Discovery is not permission. Every dataset in the in-app registry starts **blocked**; it becomes usable only after the administrator (1) retrieves
the official terms (evidence hash stored), (2) records a verified SPDX licence (rejected if it contradicts the repository's own machine-readable
record), (3) approves it, and (4) the task-compatibility check passes. Registry licence text is a **claim** until verified.

**Phase 5 (2026-10-20)** surveyed Zenodo, Figshare, Harvard Dataverse and Hugging Face (970 candidates, [docs/eval/dataset_discovery.json](docs/eval/dataset_discovery.json)), read the records of every plausible
candidate, and registered four more sources from their official hosts. Each decision (approval or refusal) is in [docs/eval/dataset_decisions.json](docs/eval/dataset_decisions.json) with the hash of the
evidence it rests on. The approvals were recorded by the automated Phase 5 run (`phase5-agent`) on the owner's instruction to use only datasets with verified licences; they are reversible in the
administrator screen, and **legal review is recommended before any commercial deployment** (see the caveats under each dataset).

## Used

### PAD-UFES-20 — verified, approved, downloaded, imported
| Field | Value |
|---|---|
| Official source | https://data.mendeley.com/datasets/zr7vgbcyr2 (DOI 10.17632/zr7vgbcyr2.1), Federal University of Espírito Santo (UFES), Brazil |
| Licence | **CC BY 4.0** — read from the official Mendeley API record (`data_licence.short_name`), snapshot hash stored in the registry; commercial use and training permitted with attribution |
| Attribution | Pacheco et al., "PAD-UFES-20: a skin lesion dataset composed of patient data and clinical images collected from smartphones", Data in Brief 2020. Further permission may be required for any third-party content inside the dataset (per the licence note). |
| Content | 2,298 clinical smartphone images of skin lesions, 1,373 patients, 1,891 lesions, 6 diagnoses, up to 22 clinical features per sample |
| Labels | ACK 730 · BCC 845 · NEV 244 · SEK 235 · SCC 192 · MEL 52 (before cleaning). 1,342 of 2,298 images biopsy-proven; the rest clinical consensus (recorded per image as label provenance) |
| Skin tone (Fitzpatrick, partially labelled) | 1: 153 · 2: 876 · 3: 392 · 4: 62 · 5: 10 · 6: 1 · **unlabelled: 804** |
| Acquisition | Smartphones in a hospital programme; mostly lighter skin; Brazilian population |
| Download | Three zip files + `metadata.csv`, ≈3.6 GB, **sha256 verified against the repository's per-file hashes** (all four matched) |
| Cleaning | Exact duplicates with the same label skipped (3); **12 exact-duplicate pairs filed under different diagnoses excluded entirely** (ambiguous ground truth; e.g. PAT_202 lesions 307/308, PAT_38 1002/1003); 15 perceptual near-duplicates flagged, kept (the hash over-matches low-texture images; a stricter thumbnail-correlation audit found **0** train/val/test pairs ≥ 0.985). Result: **2,271 images, 1,363 patients** (dataset version 2; version 1 kept for audit, it silently kept the first label of conflicting pairs). |
| Splitting | Patient-level, class-stratified (sklearn `StratifiedGroupKFold`, seed 17): train 1,621 / val 325 / test 325 images (980 / 197 / 194 patients); no patient crosses splits (asserted) |
| Suitable for | Research benchmarking of 6-class smartphone-photo lesion classification (`lesion_classification_research`) |
| **Not** suitable for | Acne, pigmentation, hair/scalp tasks (no such labels); ordinary face selfies; any claim about darker skin (types 5–6 ≈ 11 images); any patient-facing diagnosis |
| Known limitations | Strong class imbalance (MEL 52); a single hospital programme; label noise for non-biopsied cases; duplicate-image label conflicts found and excluded; many images lack Fitzpatrick labels |

### ISIC Archive, dermoscopic images (CC-0 and CC-BY only) — verified, approved, downloaded, imported (`isic-dermoscopy`, dataset version 1)
| Field | Value |
|---|---|
| Official source | ISIC Archive API v2, https://api.isic-archive.com/api/v2/ (International Skin Imaging Collaboration); terms https://www.isic-archive.com/terms-conditions (sections 3.1 and 4.2) |
| Licence | **Per image, set by the contributor: CC-0 or CC-BY** (the version of CC-BY is not stated by ISIC). The archive also holds a CC-BY-NC pool: the whole archive is **refused**, only this licence-filtered subset is registered, and the listing code stops if a record with any other licence appears. Attribution per image is in `attribution.csv` next to the images. |
| Content | **11,974** official 256-px thumbnails of dermoscopic images from 52,207 eligible listed images (not altered or synthetic; benign, malignant or actinic keratosis; histopathology, expert consensus on the single image, or serial imaging as the confirmation). 6,601 distinct patients/lesions. |
| Labels (8 classes) | NV 3,500 (capped from 16,608) · MEL 3,203 · BKL 1,849 · BCC 1,810 · SCC 948 · AK 486 · VASC 91 · DF 87. Anything not in the mapping (26,213 images with no specific diagnosis, fibroepithelial polyps, lentigo NOS, warts, metastases ...) is excluded and counted, never guessed. |
| Skin tone | Fitzpatrick recorded for only 1,078 images (I-II 868 · III 190 · **IV 20** · V-VI 0); 10,896 not recorded. The eligible listing holds **30** Fitzpatrick IV images in total and none of type V or VI. |
| Download integrity | Every file verified against the S3 ETag (MD5 of the served object); 0 failures. (ISIC's published `size` field is 3,173 bytes smaller than the file served, so size is not used.) |
| Cleaning | No exact duplicates; 1,031 perceptual near-duplicates flagged and kept (the training code removes validation/test images that duplicate a training image before any metric); 0 decode errors. 10,288 images have a patient or lesion identifier; the other 1,686 are used for training only. |
| Suitable for | Research benchmarking of 8-class dermoscopic lesion classification (`dermoscopy_lesion_research`) |
| **Not** suitable for | Phone or clinical photographs; any claim about darker skin; any malignancy claim for Fitzpatrick IV-VI; patient-facing diagnosis |
| Caveats | The contributor licences are what ISIC states; the website terms add conditions (attribution, no re-identification). A lawyer should confirm that this is sufficient for commercial model training. Label definitions differ between contributing centres. |

### ISIC Archive, clinical photographs (CC-0 and CC-BY only, PAD-UFES-20's own copy removed) — verified, approved, downloaded, imported (`isic-clinical`, version 2)
| Field | Value |
|---|---|
| Source and licence | As above (per-image CC-0 / CC-BY). |
| Content | **474** full-size clinical close-up and overview photographs, 372 patients/lesions, from 4,028 listed. **2,298 listed images are attributed to the Federal University of Espirito Santo: they are PAD-UFES-20 itself and are excluded** so the same photograph cannot appear in both sources. |
| Labels (the six PAD-UFES-20 classes) | BCC 154 · MEL 123 · NEV 88 · SCC 71 · SEK 21 · ACK 17 |
| Skin tone | I-II 300 · III 25 · **IV-VI 4** · not recorded 145 |
| Defect found and fixed | The first version (56 images) came from a query that required an `image_manipulation` field most records lack. It was marked incomplete (so no run can use it) and rebuilt as version 2; both are kept for audit. |
| Suitable for | A modest addition (+21%) to PAD-UFES-20 for the six-class phone-photo task; the 135 images with no patient or lesion identifier are used for training only |

### SCIN, Skin Condition Image Network (Google) — verified, approved, downloaded, imported (`scin`, version 1)
| Field | Value |
|---|---|
| Official source | https://github.com/google-research-datasets/scin; data in the public bucket `dx-scin-public-data` |
| Licence | **SCIN Data Use License**: worldwide, royalty-free, irrevocable; conditions are attribution and no attempt to re-identify contributors; **no non-commercial clause** (full text read, hash in the decision record). It is a custom licence, so it is accepted through a recorded permission document and **should be confirmed by a lawyer**. |
| Content | 2,842 photographs of **1,353 cases** (up to three photographs per case) of 11 conditions that have at least 40 qualifying cases: from 5,033 cases, 1,972 have no dermatologist-weighted label and 837 have a leading condition below weight 0.5 (1,358 qualified; 8 image files shared between cases carry identical labels and were collapsed into one entry each). Download verified by the bucket's MD5 (0 failures, 3.5 GB). |
| Labels | Dermatologist opinion from the photographs (not biopsy): Eczema 802 · Allergic contact dermatitis 488 · Urticaria 368 · Insect bite 314 · Folliculitis 215 · Tinea 134 · Psoriasis 129 · Herpes zoster 107 · Acne 98 · Herpes simplex 96 · Pigmented purpuric eruption 91 images. Conditions with fewer than 40 qualifying cases (rosacea 25, seborrheic dermatitis 13, scar condition 12 ...) are not classes. |
| Skin tone | Modal dermatologist Fitzpatrick estimate from the photograph (up to three raters): I-II 1,201 · III 925 · **IV-VI 595** · not recorded 121; Monk tone is also in the metadata. These are estimates, not measurements. |
| Suitable for | Research benchmarking of 11-class common-condition recognition across skin tones (`skin_condition_research`) |
| **Not** suitable for | Acne severity (98 acne images, no grade), redness or pigmentation scoring, hair or scalp findings; patient-facing diagnosis. US volunteers who chose to submit photographs. |

### DERM12345 — verified, approved, downloaded, imported (`derm12345`, version 1)
| Field | Value |
|---|---|
| Official source | Harvard Dataverse, doi:10.7910/DVN/DAXZ7P (Yilmaz et al.), the original authors' deposit |
| Licence | **CC BY 4.0**, read from the repository record; every image row also carries `copyright-license = CC-BY`. Three archives and two metadata files verified by the repository's MD5. |
| Content | 4,821 of 12,345 dermatoscopic images (1,627 patients) selected: 8 task classes mapped from 40 sub-classes (codes outside the map, 72 images, excluded); benign nevi capped at 2,500. 1,236 patients in the selection. Official train/test split recorded; 3 patients appear in both and are moved to training. |
| Labels | NV 2,500 · BKL 675 · BCC 423 · MEL 400 · SCC 303 · VASC 282 · DF 180 · AK 58 |
| Skin tone | **Not recorded** for any image |
| Use | A second centre's dermoscopy for external validation (`derm-isic`) and, separately, as extra training data with its own patient-level split (`derm-isic-derm12345`) |

## Reviewed and not used
Every row was read from the publisher's own record; the evidence hashes of the registry decisions are in [docs/eval/dataset_decisions.json](docs/eval/dataset_decisions.json) and the survey candidates in [docs/eval/dataset_discovery.json](docs/eval/dataset_discovery.json).
| Dataset | Source | Licence found | Why not used |
|---|---|---|---|
| ISIC Archive as a whole | isic-archive.com | per image CC-0, CC-BY or **CC-BY-NC** | Mixed licences: refused as a whole; the CC-0/CC-BY subsets above are used instead |
| HAM10000 | doi.org/10.7910/DVN/DBW86T | **CC BY-NC 4.0** (publisher) | Non-commercial. A Zenodo copy labelled CC BY 4.0 (record 17702692) cannot relicense it |
| Fitzpatrick 17k | github.com/mattgroh/fitzpatrick17k | annotations CC BY-NC-SA 3.0; images belong to the source websites | Non-commercial and image rights unclear |
| DDI | aimi.stanford.edu | no-charge non-commercial research use | Registration-gated; commercial use needs a separate licence |
| ACNE04 | github.com/xpwu95/LDL | none stated | No licence, face-image consent undocumented; the only acne-severity candidate |
| FitzDerm-CF (Dataverse OZKS1L) | Dataverse | CC0 (uploader) | Built from Fitzpatrick17k and DDI images: the CC0 label cannot override their terms |
| huynhnhu213/acne (Hugging Face) | Hugging Face | apache-2.0 (uploader) | The dataset card has no source, labels or consent statement; the rights cannot be traced |
| Binary_Skin_Cancer_Dataset; Ringworm/Scabies set (Figshare) | Figshare | CC BY 4.0 (uploader) | "Curated from publicly available sources" with no named source; the second says non-commercial |
| ScalpHairLabels v1 (Zenodo 23433121) | Zenodo | CC BY 4.0 annotations over CC-0 ISIC images | 504 hair-mask tiles from 108 images: a segmentation dataset, no hair-loss grade or scalp-condition label |
| AI-generated faces of skin disease (Zenodo 14882299) | Zenodo | CC BY 4.0 | Synthetic (DALL-E 3): cannot evaluate anything about real skin |
| FUNSD | guillaumejaume.github.io/FUNSD | non-commercial research | Not medical; general forms |

## Gaps (reported, not papered over)
* **No** verified, openly licensed dataset with traceable rights was found, after the Phase 5 survey of 970 candidates, for: acne severity on face photos, pigmentation/tone, redness, dryness, texture/scarring, hair-density/hair-loss or scalp findings. Those tasks stay `unavailable` in the task registry and return no predictions. SCIN contains a few relevant conditions (acne 98 images, rosacea 25 cases, seborrheic dermatitis 13 cases) but no grading and too few cases. Body-photo common-condition classification now has a research benchmark (SCIN, 11 conditions).
* **No** open medical-document dataset with licence terms permitting evaluation of Indian lab-report layouts was identified; extraction is therefore evaluated on **synthetic** reports (`docs/eval/doc_extraction.md`) and must be re-evaluated on consented, de-identified real documents before reliance.
* **No** Hindi/Hinglish speech or clinical-text corpus was available locally; those capabilities are unverified.

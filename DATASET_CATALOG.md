# Dataset Catalog

Discovery is not permission. Every dataset in the in-app registry starts **blocked**; it becomes usable only after the administrator (1) retrieves
the official terms (evidence hash stored), (2) records a verified SPDX licence (rejected if it contradicts the repository's own machine-readable
record), (3) approves it, and (4) the task-compatibility check passes. Registry licence text is a **claim** until verified.

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

## Registry entries not used (unverified — blocked)
| Dataset | Source | Claimed licence | Why not used |
|---|---|---|---|
| ISIC Archive | isic-archive.com | per-image CC0 / CC-BY / CC-BY-NC | Mixed licences; needs per-collection review; dermoscopy ≠ patient photos |
| HAM10000 | doi.org/10.7910/DVN/DBW86T | CC BY-NC 4.0 | **Non-commercial**: blocked for a commercial product; dermoscopic pigmented lesions only |
| Fitzpatrick 17k | github.com/mattgroh/fitzpatrick17k | annotations CC BY-NC-SA 3.0; image rights unclear | Image redistribution/training rights unclear |
| DDI | ddi-dataset.github.io | research-use agreement | Access agreement and evaluation-only intent |
| ACNE04 | github.com/xpwu95/LDL | unclear | Terms and face-image consent unclear; the only candidate for an acne task |
| FUNSD | guillaumejaume.github.io/FUNSD | non-commercial research | Not medical; general forms |

## Gaps (reported, not papered over)
* **No** verified, openly licensed dataset was found for: acne severity on ordinary face photos, pigmentation/tone, redness, hair-density/hair-loss, scalp findings, or body-photo condition classification. Those tasks stay `unavailable` in the task registry and return no predictions.
* **No** open medical-document dataset with licence terms permitting evaluation of Indian lab-report layouts was identified; extraction is therefore evaluated on **synthetic** reports (`docs/eval/doc_extraction.md`) and must be re-evaluated on consented, de-identified real documents before reliance.
* **No** Hindi/Hinglish speech or clinical-text corpus was available locally; those capabilities are unverified.

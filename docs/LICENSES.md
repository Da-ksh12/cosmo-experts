# Licences (verify before distribution)

Software (as published on their package pages; confirm for the exact versions in requirements.txt / package.json):
FastAPI (MIT), Uvicorn (BSD-3), Pydantic (MIT), OpenCV (Apache-2.0), NumPy/scikit-learn (BSD-3), Pillow (HPND),
PyTorch/torchvision (BSD-3), PyMuPDF (**AGPL-3.0 / commercial** — review before any commercial deployment;
swap for pypdf + pdfium if AGPL is unacceptable), pypdf (BSD-3), ReportLab (BSD), pytesseract (Apache-2.0) with
Tesseract (Apache-2.0), pyttsx3 (MPL-2.0), Next.js/React (MIT), Tailwind CSS (MIT).

Models: no pretrained weights are downloaded or bundled. OpenCV's Haar face cascade ships with OpenCV
(BSD-style notice in the file). Any pretrained weights an admin adds must have provenance and licence recorded in the
model card (`pretrained_weights`); ImageNet-derived weights carry research-use caveats that need legal review.

Datasets: see the in-app registry and [DATASET_CATALOG.md](../DATASET_CATALOG.md). All start unverified and blocked. State after Phase 5 (decisions with evidence hashes in
`docs/eval/dataset_decisions.json`, recorded by the automated Phase 5 run on the owner's instruction, reversible, **legal review recommended before commercial use**):

| Dataset | Licence as verified | What must travel with the data or a model trained on it |
|---|---|---|
| PAD-UFES-20 | CC BY 4.0 (Mendeley record) | Cite Pacheco et al. 2020 |
| ISIC Archive subsets (dermoscopy, clinical) | per image CC-0 or CC-BY set by the contributor (CC-BY version not stated); ISIC terms 3.1 and 4.2 | Per-image attribution (`attribution.csv` beside the images); no re-identification. The CC-BY-NC pool is never listed or downloaded |
| SCIN | SCIN Data Use License (custom: worldwide, royalty-free, irrevocable; no non-commercial clause) | Attribution (Ward et al. 2024), keep the licence notice with redistribution, no re-identification of contributors |
| DERM12345 | CC BY 4.0 (original authors' Dataverse record) | Cite Yilmaz et al. |
| Refused | HAM10000 (CC BY-NC 4.0), Fitzpatrick17k (BY-NC-SA, image rights unclear), DDI (non-commercial), ACNE04 (no licence), the ISIC Archive as a whole (contains CC-BY-NC) | Not downloaded for training |

Backbone weights: **DINOv2 ViT-S/14 (Apache-2.0)** is the backbone of every Phase 5 model. **ResNet18 ImageNet-1k weights** are used only for comparison baselines: the licence of the weights is
BSD-3-Clause but the ImageNet data they were trained on carries research-use terms, so their commercial use is **unresolved**; a model fine-tuned from them must not be approved before legal review.

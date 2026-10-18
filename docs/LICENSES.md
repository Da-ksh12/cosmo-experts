# Licences (verify before distribution)

Software (as published on their package pages; confirm for the exact versions in requirements.txt / package.json):
FastAPI (MIT), Uvicorn (BSD-3), Pydantic (MIT), OpenCV (Apache-2.0), NumPy/scikit-learn (BSD-3), Pillow (HPND),
PyTorch/torchvision (BSD-3), PyMuPDF (**AGPL-3.0 / commercial** — review before any commercial deployment;
swap for pypdf + pdfium if AGPL is unacceptable), pypdf (BSD-3), ReportLab (BSD), pytesseract (Apache-2.0) with
Tesseract (Apache-2.0), pyttsx3 (MPL-2.0), Next.js/React (MIT), Tailwind CSS (MIT).

Models: no pretrained weights are downloaded or bundled. OpenCV's Haar face cascade ships with OpenCV
(BSD-style notice in the file). Any pretrained weights an admin adds must have provenance and licence recorded in the
model card (`pretrained_weights`); ImageNet-derived weights carry research-use caveats that need legal review.

Datasets: see the in-app registry. All start unverified and blocked; claimed licences include non-commercial terms
(HAM10000, ISIC subsets, FUNSD), unclear terms (ACNE04, Fitzpatrick17k images) and agreement-gated access (DDI).

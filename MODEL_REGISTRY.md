# Model Registry

Generated from the live registry by `backend/scripts/export_registry.py` on the machine where the models were trained; numbers are copied from model cards, not typed. **No model here is approved for patient use.**

## Models used by the product (not trained here)

| Model | Role | Licence | Source | sha256 (file) | Intended use | Known limitations |
|---|---|---|---|---|---|---|
| Tesseract OCR 5.4.0 | Scanned-document text recognition | Apache-2.0 | https://github.com/UB-Mannheim/tesseract (winget UB-Mannheim.TesseractOCR) | n/a (installed binary) | Read printed lab reports/prescriptions; output is reviewed by a clinician | English + OSD only installed; Hindi traineddata not installed; accuracy in docs/eval/doc_extraction.md |
| BGE-small-en-v1.5 | Passage/query embeddings for retrieval | MIT | https://huggingface.co/BAAI/bge-small-en-v1.5 | 3c9f31665447c891… | Rank the patient's own document lines and approved articles | English; abstention threshold calibrated on synthetic data (docs/eval/retrieval.md) |
| faster-whisper base (CTranslate2, int8) | Speech-to-text | MIT | https://huggingface.co/Systran/faster-whisper-base | d01c3014881c9c6f… | Let a patient dictate a question; transcript is shown for correction before sending | Evaluated only on synthetic English speech; Hindi/Hinglish unverified |
| Qwen2.5-1.5B-Instruct Q4_K_M | Optional claim drafting (OFF by default) | Apache-2.0 | https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF | 6a1a2eb6d15622bf… | Experimental draft of source-cited claims, always verified before display | Measured as NOT adequate (docs/eval/llm_*.json); 3-4 tokens/s on 2 CPU cores |
| ResNet18 (ImageNet-1k, torchvision weights via timm) | Backbone for baseline and fine-tuning experiments | BSD-3-Clause (weights trained on ImageNet-1k; upstream data terms need legal review for commercial use) | https://huggingface.co/timm/resnet18.tv_in1k | ef5596a83ccfde67… | Research backbone only | — |
| DINOv2 ViT-S/14 (timm) | Frozen backbone for the lesion linear probe | Apache-2.0 (trained on Meta's LVD-142M; data terms not independently reviewed) | https://huggingface.co/timm/vit_small_patch14_dinov2.lvd142m | 04d27f3400d059fc… | Research backbone only | — |

## Image classifiers trained in this repository

### fc7eef89489e4e7db1ce95caf2ec5173  — resnet18

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** lesion_classification_research · classes ACK, BCC, MEL, NEV, SCC, SEK
* **Dataset:** pad-ufes-20 v2 (licence CC BY 4.0); split images {'test': 325, 'val': 325, 'train': 1621}; patients {'test': 194, 'val': 197, 'train': 980}
* **Training:** seed 17, device cpu, image 224px, fine-tune `layer4`, epochs 6, 1199 s; code revision `src-cff35d303fda9d62`
* **Pretrained weights:** `data/models/pretrained/resnet18_tv_in1k.pth` sha256 `ef5596a83ccfde67…`
* **Artifact:** `fc7eef89489e4e7db1ce95caf2ec5173.pt` sha256 `a2c5e42dfd9efc56…`
* **Held-out test (n=325):** macro-F1 **0.502** (95% CI [0.429, 0.576]), balanced accuracy 0.505, accuracy 0.637, ECE 0.076 (uncalibrated 0.043, T=0.9)
* **Baselines on the same test set:** majority_class: macro-F1 0.089; frozen_features_logreg: macro-F1 0.515

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| ACK | 104 | 0.750 | 0.887 | 0.757 | 0.925 | 26 |
| BCC | 119 | 0.672 | 0.835 | 0.702 | 0.876 | 39 |
| MEL | 7 | 0.143 | 0.994 | 0.333 | 0.865 | 6 |
| NEV | 35 | 0.743 | 0.934 | 0.578 | 0.957 | 9 |
| SCC | 27 | 0.296 | 0.936 | 0.296 | 0.779 | 19 |
| SEK | 33 | 0.424 | 0.935 | 0.424 | 0.876 | 19 |

Subgroups (Fitzpatrick): 1-2: n=147, acc 0.578; 3: n=58, acc 0.569; 4-6: n=8, acc 0.5 (unreliable); NA: n=112, acc 0.759

Robustness (macro-F1): blur 0.412; dark 0.417; jpeg20 0.417; rotate90 0.563

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence CC-BY-4.0 with evidence hash |
| min_test_images | PASS | test n=325 (need >= 200) |
| no_near_duplicates_in_test | **FAIL** | near-duplicates test/train: 4 |
| beat_baselines_macro_f1 | **FAIL** | macro-F1 0.502 vs best baseline 0.515 |
| improvement_supported_by_ci | **FAIL** | 95% CI lower bound 0.429 must exceed the best baseline 0.515 |
| max_ece_calibrated | PASS | ECE 0.076 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| critical_class_sensitivity | **FAIL** | MEL: only 7 test images; BCC: sensitivity 0.672; SCC: sensitivity 0.296 |
| dark_skin_evaluated | **FAIL** | Fitzpatrick 4-6 test images: 8 (need >= 50) |
| ood_detection | **FAIL** | no OOD evaluation recorded |

**Known limitations:** Small-sample metrics are unreliable; Dataset demographics may not represent target patients

### 124f81d4a9594958896d16e61944cd4d  — dinov2_vit_s14_frozen + logistic_regression_probe

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** lesion_classification_research · classes ACK, BCC, MEL, NEV, SCC, SEK
* **Dataset:** pad-ufes-20 v2 (licence CC BY 4.0); split images {'test': 325, 'val': 325, 'train': 1621}; patients {'test': 194, 'val': 197, 'train': 980}
* **Training:** seed 17, device cpu, image 224px, fine-tune `none (frozen backbone)`, epochs 0, 755 s; code revision `src-370f9726f39e2eab`
* **Pretrained weights:** `data/models/pretrained/dinov2_small/model.safetensors` sha256 `04d27f3400d059fc…`
* **Artifact:** `124f81d4a9594958896d16e61944cd4d.npz` sha256 `f7303a50537923f2…`
* **Held-out test (n=325):** macro-F1 **0.558** (95% CI [0.471, 0.63]), balanced accuracy 0.576, accuracy 0.615, ECE 0.035 (uncalibrated 0.102, T=1.3)
* **Baselines on the same test set:** majority_class: macro-F1 0.089; resnet18_frozen_features_logreg: macro-F1 0.508

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| ACK | 104 | 0.721 | 0.891 | 0.758 | 0.888 | 29 |
| BCC | 119 | 0.546 | 0.874 | 0.714 | 0.850 | 54 |
| MEL | 7 | 0.429 | 0.987 | 0.429 | 0.910 | 4 |
| NEV | 35 | 0.657 | 0.941 | 0.575 | 0.953 | 12 |
| SCC | 27 | 0.407 | 0.862 | 0.212 | 0.787 | 16 |
| SEK | 33 | 0.697 | 0.955 | 0.639 | 0.968 | 10 |

Subgroups (Fitzpatrick): 1-2: n=147, acc 0.503; 3: n=58, acc 0.603; 4-6: n=8, acc 0.5 (unreliable); NA: n=112, acc 0.777

Robustness (macro-F1): blur 0.477; dark 0.562; jpeg20 0.32; rotate90 0.562

Out-of-distribution: 19 non-lesion photos got mean confidence 0.75 vs 0.643 for real test images; AUROC of confidence for telling them apart = **0.349** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence CC-BY-4.0 with evidence hash |
| min_test_images | PASS | test n=325 (need >= 200) |
| no_near_duplicates_in_test | PASS | near-duplicates test/train: 0 |
| beat_baselines_macro_f1 | PASS | macro-F1 0.558 vs best baseline 0.508 |
| improvement_supported_by_ci | **FAIL** | 95% CI lower bound 0.471 must exceed the best baseline 0.508 |
| max_ece_calibrated | PASS | ECE 0.035 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| critical_class_sensitivity | **FAIL** | MEL: only 7 test images; BCC: sensitivity 0.546; SCC: sensitivity 0.407 |
| dark_skin_evaluated | **FAIL** | Fitzpatrick 4-6 test images: 8 (need >= 50) |
| ood_detection | **FAIL** | confidence separates lesion from non-lesion images with AUROC 0.349 (need >= 0.9) |

**Known limitations:** Small-sample metrics are unreliable; Dataset demographics may not represent target patients

### 20b66199a48b4956962f9a3fdcb7bbac  — dinov2_vit_s14_frozen + logistic_regression_probe

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** lesion_classification_research · classes ACK, BCC, MEL, NEV, SCC, SEK
* **Dataset:** pad-ufes-20 v2 (licence CC BY 4.0); split images {'test': 325, 'val': 325, 'train': 1621}; patients {'test': 194, 'val': 197, 'train': 980}
* **Training:** seed 17, device cpu, image 224px, fine-tune `none (frozen backbone)`, epochs 0, 837 s; code revision `src-77a987f8b5e443db`
* **Pretrained weights:** `data/models/pretrained/dinov2_small/model.safetensors` sha256 `04d27f3400d059fc…`
* **Artifact:** `20b66199a48b4956962f9a3fdcb7bbac.npz` sha256 `f7303a50537923f2…`
* **Held-out test (n=325):** macro-F1 **0.558** (95% CI [0.471, 0.63]), balanced accuracy 0.576, accuracy 0.615, ECE 0.035 (uncalibrated 0.102, T=1.3)
* **Baselines on the same test set:** majority_class: macro-F1 0.089; resnet18_frozen_features_logreg: macro-F1 0.508

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| ACK | 104 | 0.721 | 0.891 | 0.758 | 0.888 | 29 |
| BCC | 119 | 0.546 | 0.874 | 0.714 | 0.850 | 54 |
| MEL | 7 | 0.429 | 0.987 | 0.429 | 0.910 | 4 |
| NEV | 35 | 0.657 | 0.941 | 0.575 | 0.953 | 12 |
| SCC | 27 | 0.407 | 0.862 | 0.212 | 0.787 | 16 |
| SEK | 33 | 0.697 | 0.955 | 0.639 | 0.968 | 10 |

Subgroups (Fitzpatrick): 1-2: n=147, acc 0.503; 3: n=58, acc 0.603; 4-6: n=8, acc 0.5 (unreliable); NA: n=112, acc 0.777

Robustness (macro-F1): blur 0.477; dark 0.562; jpeg20 0.32; rotate90 0.562

Out-of-distribution: 19 non-lesion photos got mean confidence 0.75 vs 0.643 for real test images; AUROC of confidence for telling them apart = **0.349** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence CC-BY-4.0 with evidence hash |
| min_test_images | PASS | test n=325 (need >= 200) |
| no_near_duplicates_in_test | PASS | near-duplicates test/train: 0 |
| beat_baselines_macro_f1 | PASS | macro-F1 0.558 vs best baseline 0.508 |
| improvement_supported_by_ci | **FAIL** | 95% CI lower bound 0.471 must exceed the best baseline 0.508 |
| max_ece_calibrated | PASS | ECE 0.035 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| critical_class_sensitivity | **FAIL** | MEL: only 7 test images; BCC: sensitivity 0.546; SCC: sensitivity 0.407 |
| dark_skin_evaluated | **FAIL** | Fitzpatrick 4-6 test images: 8 (need >= 50) |
| ood_detection | **FAIL** | confidence separates lesion from non-lesion images with AUROC 0.349 (need >= 0.9) |

**Known limitations:** Small-sample metrics are unreliable; Dataset demographics may not represent target patients

### f93966836e184a2c869dba28a4cd17af  — resnet18

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** lesion_classification_research · classes ACK, BCC, MEL, NEV, SCC, SEK
* **Dataset:** pad-ufes-20 v2 (licence CC BY 4.0); split images {'test': 325, 'val': 325, 'train': 1621}; patients {'test': 194, 'val': 197, 'train': 980}
* **Training:** seed 17, device cpu, image 224px, fine-tune `layer4`, epochs 6, 1220 s; code revision `src-f6e4a519abd4ff4f`
* **Pretrained weights:** `data/models/pretrained/resnet18_tv_in1k.pth` sha256 `ef5596a83ccfde67…`
* **Artifact:** `f93966836e184a2c869dba28a4cd17af.pt` sha256 `24ed3c8061f915f1…`
* **Held-out test (n=325):** macro-F1 **0.502** (95% CI [0.429, 0.576]), balanced accuracy 0.505, accuracy 0.637, ECE 0.076 (uncalibrated 0.043, T=0.9)
* **Baselines on the same test set:** majority_class: macro-F1 0.089; frozen_features_logreg: macro-F1 0.515

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| ACK | 104 | 0.750 | 0.887 | 0.757 | 0.925 | 26 |
| BCC | 119 | 0.672 | 0.835 | 0.702 | 0.876 | 39 |
| MEL | 7 | 0.143 | 0.994 | 0.333 | 0.865 | 6 |
| NEV | 35 | 0.743 | 0.934 | 0.578 | 0.957 | 9 |
| SCC | 27 | 0.296 | 0.936 | 0.296 | 0.779 | 19 |
| SEK | 33 | 0.424 | 0.935 | 0.424 | 0.876 | 19 |

Subgroups (Fitzpatrick): 1-2: n=147, acc 0.578; 3: n=58, acc 0.569; 4-6: n=8, acc 0.5 (unreliable); NA: n=112, acc 0.759

Robustness (macro-F1): blur 0.412; dark 0.417; jpeg20 0.417; rotate90 0.563

Out-of-distribution: 19 non-lesion photos got mean confidence 0.858 vs 0.708 for real test images; AUROC of confidence for telling them apart = **0.273** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence CC-BY-4.0 with evidence hash |
| min_test_images | PASS | test n=325 (need >= 200) |
| no_near_duplicates_in_test | PASS | near-duplicates test/train: 0 |
| beat_baselines_macro_f1 | **FAIL** | macro-F1 0.502 vs best baseline 0.515 |
| improvement_supported_by_ci | **FAIL** | 95% CI lower bound 0.429 must exceed the best baseline 0.515 |
| max_ece_calibrated | PASS | ECE 0.076 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| critical_class_sensitivity | **FAIL** | MEL: only 7 test images; BCC: sensitivity 0.672; SCC: sensitivity 0.296 |
| dark_skin_evaluated | **FAIL** | Fitzpatrick 4-6 test images: 8 (need >= 50) |
| ood_detection | **FAIL** | confidence separates lesion from non-lesion images with AUROC 0.273 (need >= 0.9) |

**Known limitations:** Small-sample metrics are unreliable; Dataset demographics may not represent target patients

### c128eb967c3f481bb25e8d9adbb71a43  — dinov2_vit_s14_frozen + logistic_regression_probe

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** lesion_classification_research · classes ACK, BCC, MEL, NEV, SCC, SEK
* **Dataset(s):** pad-ufes-20 (pad-ufes-20 v2, 2271 images, train/val/test, licence CC BY 4.0); split images {'train': 1621, 'val': 325, 'test': 325}; patients {'train': 980, 'val': 197, 'test': 194}
* **Training:** seed 17, device cpu, image 224px, fine-tune `none (frozen backbone)`, epochs 0, 1332 s; code revision `src-71cd7506223ae75f`; experiment `clinical-pad-only`, feature kind `cls`, C=0.03
* **Pretrained weights:** `data/models/pretrained/dinov2_small/model.safetensors` sha256 `04d27f3400d059fc…`
* **Artifact:** `c128eb967c3f481bb25e8d9adbb71a43.npz` sha256 `fad05a260f1250c3…`
* **Held-out test (n=325):** macro-F1 **0.558** (95% CI [0.471, 0.63]), balanced accuracy 0.576, accuracy 0.615, ECE 0.035 (uncalibrated 0.105, T=1.3)
* **Baselines on the same test set:** majority_class: macro-F1 0.089; dinov2_knn_cosine: macro-F1 0.423; resnet18_frozen_features_logreg: macro-F1 0.508

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| ACK | 104 | 0.721 | 0.891 | 0.758 | 0.888 | 29 |
| BCC | 119 | 0.546 | 0.874 | 0.714 | 0.850 | 54 |
| MEL | 7 | 0.429 | 0.987 | 0.429 | 0.910 | 4 |
| NEV | 35 | 0.657 | 0.941 | 0.575 | 0.953 | 12 |
| SCC | 27 | 0.407 | 0.862 | 0.212 | 0.786 | 16 |
| SEK | 33 | 0.697 | 0.955 | 0.639 | 0.967 | 10 |

Subgroups (Fitzpatrick): 1-2: n=147, acc 0.503; 3: n=58, acc 0.603; 4-6: n=8, acc 0.5 (unreliable); NA: n=112, acc 0.777

Robustness (macro-F1): blur 0.472; dark 0.539; jpeg20 0.281; rotate90 0.542

Out-of-distribution: 19 non-lesion photos got mean confidence 0.75 vs 0.643 for real test images; AUROC of confidence for telling them apart = **0.349** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence CC-BY-4.0 with evidence hash |
| min_test_images | PASS | test n=325 (need >= 200) |
| no_near_duplicates_in_test | PASS | near-duplicates test/train: 0 |
| beat_baselines_macro_f1 | PASS | macro-F1 0.558 vs best baseline 0.508 |
| improvement_supported_by_ci | **FAIL** | 95% CI lower bound 0.471 must exceed the best baseline 0.508 |
| max_ece_calibrated | PASS | ECE 0.035 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| critical_class_sensitivity | **FAIL** | MEL: only 7 test images; BCC: sensitivity 0.546; SCC: sensitivity 0.407 |
| dark_skin_evaluated | **FAIL** | Fitzpatrick 4-6 test images: 8 (need >= 50) |
| ood_detection | **FAIL** | confidence separates lesion from non-lesion images with AUROC 0.349 (need >= 0.9) |

**Known limitations:** Trained on one centre's smartphone photographs (PAD-UFES-20): few melanoma examples and almost no dark-skin examples.; Small-sample metrics are unreliable

### 0f7e066f6b734e1092e17218fb6e1881  — dinov2_vit_s14_frozen + logistic_regression_probe

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** lesion_classification_research · classes ACK, BCC, MEL, NEV, SCC, SEK
* **Dataset(s):** pad-ufes-20 (pad-ufes-20 v2, 2271 images, train/val/test, licence CC BY 4.0); isic-clinical (isic-clinical v2, 474 images, train/val/test, licence Per image: CC-0 or CC-BY (contributor licence), version of CC-BY not stated by ISIC); split images {'train': 1997, 'val': 374, 'test': 374}; patients {'train': 1285, 'val': 231, 'test': 227}
* **Training:** seed 17, device cpu, image 224px, fine-tune `none (frozen backbone)`, epochs 0, 530 s; code revision `src-49eb1d7ee9c4b2de`; experiment `clinical-pad-isic`, feature kind `cls+mean`, C=0.01
* **Pretrained weights:** `data/models/pretrained/dinov2_small/model.safetensors` sha256 `04d27f3400d059fc…`
* **Artifact:** `0f7e066f6b734e1092e17218fb6e1881.npz` sha256 `81a9c43f409ca492…`
* **Held-out test (n=374):** macro-F1 **0.559** (95% CI [0.496, 0.614]), balanced accuracy 0.578, accuracy 0.61, ECE 0.064 (uncalibrated 0.084, T=1.15)
* **Baselines on the same test set:** majority_class: macro-F1 0.089; dinov2_knn_cosine: macro-F1 0.409; resnet18_frozen_features_logreg: macro-F1 0.476

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| ACK | 107 | 0.720 | 0.891 | 0.726 | 0.901 | 30 |
| BCC | 136 | 0.566 | 0.895 | 0.755 | 0.848 | 59 |
| MEL | 17 | 0.471 | 0.972 | 0.444 | 0.924 | 9 |
| NEV | 44 | 0.659 | 0.930 | 0.558 | 0.940 | 15 |
| SCC | 34 | 0.412 | 0.874 | 0.246 | 0.778 | 20 |
| SEK | 36 | 0.639 | 0.953 | 0.590 | 0.947 | 13 |

Held-out test by source: isic-clinical: n=49, acc 0.531, balanced acc 0.486 (unreliable); pad-ufes-20: n=325, acc 0.622, balanced acc 0.591

Subgroups (Fitzpatrick): 1-2: n=193, acc 0.528; 3: n=61, acc 0.574; 4-6: n=8, acc 0.625 (unreliable); NA: n=112, acc 0.768

Robustness (macro-F1): blur 0.435; dark 0.541; jpeg20 0.335; rotate90 0.528

Out-of-distribution: 19 non-lesion photos got mean confidence 0.715 vs 0.628 for real test images; AUROC of confidence for telling them apart = **0.361** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence CC-BY-4.0 with evidence hash |
| min_test_images | PASS | test n=374 (need >= 200) |
| no_near_duplicates_in_test | PASS | near-duplicates test/train: 0 |
| beat_baselines_macro_f1 | PASS | macro-F1 0.559 vs best baseline 0.476 |
| improvement_supported_by_ci | PASS | 95% CI lower bound 0.496 must exceed the best baseline 0.476 |
| max_ece_calibrated | PASS | ECE 0.064 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| critical_class_sensitivity | **FAIL** | MEL: only 17 test images; BCC: sensitivity 0.566; SCC: sensitivity 0.412 |
| dark_skin_evaluated | **FAIL** | Fitzpatrick 4-6 test images: 8 (need >= 50) |
| ood_detection | **FAIL** | confidence separates lesion from non-lesion images with AUROC 0.361 (need >= 0.9) |

**Known limitations:** Two clinical sources of different style; ISIC photographs without a patient identifier are used for training only.; Almost no dark-skin examples in either source.; Small-sample metrics are unreliable

### e0ce44b3a7894fac916e7d659f1efbe4  — dinov2_vit_s14_frozen + logistic_regression_probe

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** skin_condition_research · classes Acne, Allergic Contact Dermatitis, Eczema, Folliculitis, Herpes Simplex, Herpes Zoster, Insect Bite, Pigmented purpuric eruption, Psoriasis, Tinea, Urticaria
* **Dataset(s):** scin (scin v1, 2842 images, train/val/test, licence SCIN Data Use License (worldwide, royalty-free, irrevocable; attribution; no re-identification)); split images {'train': 2029, 'val': 407, 'test': 406}; patients {'train': 966, 'val': 194, 'test': 193}
* **Training:** seed 17, device cpu, image 224px, fine-tune `none (frozen backbone)`, epochs 0, 1031 s; code revision `src-49eb1d7ee9c4b2de`; experiment `skin-condition-scin`, feature kind `cls+mean`, C=0.03
* **Pretrained weights:** `data/models/pretrained/dinov2_small/model.safetensors` sha256 `04d27f3400d059fc…`
* **Artifact:** `e0ce44b3a7894fac916e7d659f1efbe4.npz` sha256 `200619304c195d23…`
* **Held-out test (n=406):** macro-F1 **0.347** (95% CI [0.287, 0.395]), balanced accuracy 0.377, accuracy 0.34, ECE 0.041 (uncalibrated 0.201, T=2.15)
* **Baselines on the same test set:** majority_class: macro-F1 0.04; dinov2_knn_cosine: macro-F1 0.223; resnet18_frozen_features_logreg: macro-F1 0.206

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| Acne | 14 | 0.571 | 0.974 | 0.444 | 0.911 | 6 |
| Allergic Contact Dermatitis | 69 | 0.217 | 0.843 | 0.221 | 0.635 | 54 |
| Eczema | 114 | 0.333 | 0.856 | 0.475 | 0.712 | 76 |
| Folliculitis | 31 | 0.323 | 0.915 | 0.238 | 0.703 | 21 |
| Herpes Simplex | 14 | 0.214 | 0.972 | 0.214 | 0.760 | 11 |
| Herpes Zoster | 15 | 0.533 | 0.964 | 0.364 | 0.914 | 7 |
| Insect Bite | 45 | 0.311 | 0.931 | 0.359 | 0.739 | 31 |
| Pigmented purpuric eruption | 13 | 0.462 | 0.975 | 0.375 | 0.927 | 7 |
| Psoriasis | 19 | 0.632 | 0.959 | 0.429 | 0.923 | 7 |
| Tinea | 19 | 0.158 | 0.941 | 0.115 | 0.709 | 16 |
| Urticaria | 53 | 0.396 | 0.909 | 0.396 | 0.802 | 32 |

Case level (case (logits of its photographs averaged)): n=193, accuracy 0.394, macro-F1 0.41 (95% CI [0.316, 0.484]), balanced accuracy 0.437

Subgroups (Fitzpatrick): 1-2: n=184, acc 0.332; 3: n=129, acc 0.326; 4-6: n=85, acc 0.388; NA: n=8, acc 0.25 (unreliable)

Robustness (macro-F1): blur 0.216; dark 0.354; jpeg20 0.226; rotate90 0.36

Out-of-distribution: 19 non-lesion photos got mean confidence 0.426 vs 0.314 for real test images; AUROC of confidence for telling them apart = **0.259** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence LicenseRef-SCIN-Data-Use-License with evidence hash |
| min_test_images | PASS | test n=406 (need >= 200) |
| no_near_duplicates_in_test | PASS | near-duplicates test/train: 0 |
| beat_baselines_macro_f1 | PASS | macro-F1 0.347 vs best baseline 0.223 |
| improvement_supported_by_ci | PASS | 95% CI lower bound 0.287 must exceed the best baseline 0.223 |
| max_ece_calibrated | PASS | ECE 0.041 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| dark_skin_evaluated | PASS | Fitzpatrick 4-6 test images: 85 (need >= 50) |
| ood_detection | **FAIL** | confidence separates lesion from non-lesion images with AUROC 0.259 (need >= 0.9) |

**Known limitations:** Closed set of common conditions with no 'other' class.; Labels are dermatologist opinions from photographs (leading condition weight >= 0.5), not biopsy results.; Volunteers in the United States; self-selected; photographs of varying quality.

### bab527c0778f497abc4366b4c421bebd  — dinov2_vit_s14_frozen + logistic_regression_probe

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** dermoscopy_lesion_research · classes AK, BCC, BKL, DF, MEL, NV, SCC, VASC
* **Dataset(s):** isic-dermoscopy (isic-dermoscopy v1, 11974 images, train/val/test, licence Per image: CC-0 or CC-BY (contributor licence), version of CC-BY not stated by ISIC); derm12345 (derm12345 v1, 4821 images, external test only, licence CC BY 4.0); split images {'train': 9034, 'val': 1371, 'test': 1343}; patients {'train': 5192, 'val': 697, 'test': 691}
* **Training:** seed 17, device cpu, image 224px, fine-tune `none (frozen backbone)`, epochs 0, 4879 s; code revision `src-274ecbdf1224807a`; experiment `derm-isic`, feature kind `cls+mean`, C=0.003
* **Pretrained weights:** `data/models/pretrained/dinov2_small/model.safetensors` sha256 `04d27f3400d059fc…`
* **Artifact:** `bab527c0778f497abc4366b4c421bebd.npz` sha256 `911654d065a336ee…`
* **Held-out test (n=1343):** macro-F1 **0.404** (95% CI [0.368, 0.437]), balanced accuracy 0.501, accuracy 0.517, ECE 0.026 (uncalibrated 0.041, T=1.05)
* **Baselines on the same test set:** majority_class: macro-F1 0.047; dinov2_knn_cosine: macro-F1 0.281; resnet18_frozen_features_logreg: macro-F1 0.344

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| AK | 55 | 0.291 | 0.925 | 0.142 | 0.800 | 39 |
| BCC | 245 | 0.478 | 0.913 | 0.552 | 0.865 | 128 |
| BKL | 167 | 0.275 | 0.907 | 0.297 | 0.762 | 121 |
| DF | 9 | 0.444 | 0.963 | 0.074 | 0.884 | 5 |
| MEL | 310 | 0.484 | 0.905 | 0.605 | 0.819 | 160 |
| NV | 412 | 0.684 | 0.912 | 0.775 | 0.910 | 130 |
| SCC | 133 | 0.519 | 0.928 | 0.442 | 0.878 | 64 |
| VASC | 12 | 0.833 | 0.977 | 0.244 | 0.980 | 2 |

External validation (never trained on, selected with or calibrated on): derm12345: n=4061, macro-F1 0.405 (95% CI [0.383, 0.429]), balanced accuracy 0.482, accuracy 0.65, 760 near-duplicates of training images removed

Subgroups (Fitzpatrick): 1-2: n=102, acc 0.529; 3: n=38, acc 0.368 (unreliable); 4-6: n=4, acc 0.25 (unreliable); NA: n=1199, acc 0.521

Robustness (macro-F1): blur 0.334; dark 0.412; jpeg20 0.315; rotate90 0.392

Out-of-distribution: 19 non-lesion photos got mean confidence 0.691 vs 0.537 for real test images; AUROC of confidence for telling them apart = **0.296** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence LicenseRef-ISIC-Contributor-CC0-or-CC-BY with evidence hash |
| min_test_images | PASS | test n=1343 (need >= 200) |
| no_near_duplicates_in_test | PASS | near-duplicates test/train: 0 |
| beat_baselines_macro_f1 | PASS | macro-F1 0.404 vs best baseline 0.344 |
| improvement_supported_by_ci | PASS | 95% CI lower bound 0.368 must exceed the best baseline 0.344 |
| max_ece_calibrated | PASS | ECE 0.026 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| critical_class_sensitivity | **FAIL** | MEL: sensitivity 0.484; BCC: sensitivity 0.478; SCC: sensitivity 0.519 |
| dark_skin_evaluated | **FAIL** | Fitzpatrick 4-6 test images: 4 (need >= 50) |
| ood_detection | **FAIL** | confidence separates lesion from non-lesion images with AUROC 0.296 (need >= 0.9) |

**Known limitations:** Dermoscopic images only.; ISIC images without a patient or lesion identifier are used for training only.; Fitzpatrick type is recorded for a minority of the images.

### 0e29da33555a4a7b94399a3084e1da94  — dinov2_vit_s14_frozen + logistic_regression_probe

* **Status:** `research_only` (research only; intended use: Research only. Not clinically validated. Not offered to patients.)
* **Task:** dermoscopy_lesion_research · classes AK, BCC, BKL, DF, MEL, NV, SCC, VASC
* **Dataset(s):** isic-dermoscopy (isic-dermoscopy v1, 11974 images, train/val/test, licence Per image: CC-0 or CC-BY (contributor licence), version of CC-BY not stated by ISIC); derm12345 (derm12345 v1, 4821 images, train/val/test, licence CC BY 4.0); split images {'train': 12338, 'val': 1680, 'test': 2053}; patients {'train': 5922, 'val': 766, 'test': 1008}
* **Training:** seed 17, device cpu, image 224px, fine-tune `none (frozen backbone)`, epochs 0, 306 s; code revision `src-274ecbdf1224807a`; experiment `derm-isic-derm12345`, feature kind `cls+mean`, C=0.003
* **Pretrained weights:** `data/models/pretrained/dinov2_small/model.safetensors` sha256 `04d27f3400d059fc…`
* **Artifact:** `0e29da33555a4a7b94399a3084e1da94.npz` sha256 `f9e6e22ce9ee3645…`
* **Held-out test (n=2053):** macro-F1 **0.472** (95% CI [0.444, 0.497]), balanced accuracy 0.534, accuracy 0.586, ECE 0.047 (uncalibrated 0.038, T=0.95)
* **Baselines on the same test set:** majority_class: macro-F1 0.077; dinov2_knn_cosine: macro-F1 0.351; resnet18_frozen_features_logreg: macro-F1 0.405

| Class | n (test) | Sensitivity | Specificity | Precision | AUROC | Missed |
|---|---|---|---|---|---|---|
| AK | 66 | 0.348 | 0.946 | 0.177 | 0.867 | 43 |
| BCC | 222 | 0.495 | 0.933 | 0.472 | 0.908 | 112 |
| BKL | 302 | 0.381 | 0.915 | 0.436 | 0.799 | 187 |
| DF | 45 | 0.622 | 0.954 | 0.231 | 0.920 | 17 |
| MEL | 318 | 0.503 | 0.924 | 0.548 | 0.873 | 158 |
| NV | 911 | 0.728 | 0.907 | 0.862 | 0.920 | 248 |
| SCC | 121 | 0.430 | 0.955 | 0.377 | 0.910 | 69 |
| VASC | 68 | 0.765 | 0.973 | 0.491 | 0.958 | 16 |

Held-out test by source: derm12345: n=828, acc 0.711, balanced acc 0.59; isic-dermoscopy: n=1225, acc 0.501, balanced acc 0.495

Subgroups (Fitzpatrick): 1-2: n=102, acc 0.5; 3: n=38, acc 0.421 (unreliable); 4-6: n=4, acc 0.25 (unreliable); NA: n=1909, acc 0.595

Robustness (macro-F1): blur 0.308; dark 0.429; jpeg20 0.351; rotate90 0.465

Out-of-distribution: 19 non-lesion photos got mean confidence 0.717 vs 0.62 for real test images; AUROC of confidence for telling them apart = **0.374** (0.5 = chance; below 0.5 = non-lesion photos look *more* confident).

| Release gate | Result | Detail |
|---|---|---|
| licence_gate_passed | PASS | licence LicenseRef-ISIC-Contributor-CC0-or-CC-BY with evidence hash |
| min_test_images | PASS | test n=2053 (need >= 200) |
| no_near_duplicates_in_test | PASS | near-duplicates test/train: 0 |
| beat_baselines_macro_f1 | PASS | macro-F1 0.472 vs best baseline 0.405 |
| improvement_supported_by_ci | PASS | 95% CI lower bound 0.444 must exceed the best baseline 0.405 |
| max_ece_calibrated | PASS | ECE 0.047 (limit 0.15) |
| robustness_report_present | PASS | robustness table present |
| subgroup_report_present | PASS | Fitzpatrick subgroup table present |
| critical_class_sensitivity | **FAIL** | MEL: sensitivity 0.503; BCC: sensitivity 0.495; SCC: sensitivity 0.43 |
| dark_skin_evaluated | **FAIL** | Fitzpatrick 4-6 test images: 4 (need >= 50) |
| ood_detection | **FAIL** | confidence separates lesion from non-lesion images with AUROC 0.374 (need >= 0.9) |

**Known limitations:** Dermoscopic images only.; Two centres; DERM12345 has no skin-tone labels.; ISIC images without a patient or lesion identifier are used for training only.

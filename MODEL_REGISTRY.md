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
* **Pretrained weights:** `F:\Cosmo Experts\backend\data\models\pretrained\resnet18_tv_in1k.pth` sha256 `ef5596a83ccfde67…`
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

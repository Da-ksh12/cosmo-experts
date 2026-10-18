"""Research-only inference for REGISTERED image models. Never patient-facing: the gateway route is admin-only, and `tasks.predict` still refuses.

Safety properties:
* the model artifact (and the pretrained backbone) are re-hashed against the model card before loading; a mismatch is refused;
* the input image is checked for basic usability first (decode, size, blur, exposure) and rejected without prediction if unusable;
* preprocessing is the SAME function used for training (`trainer._square_resize`), so serving reproduces the evaluated behaviour;
* output is calibrated with the card's temperature, uses the card's abstention threshold, and always carries the card's known limitations."""
from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .. import db
from ..imaging import suitability
from . import trainer as T

_cache: dict[str, dict] = {}
_lock = threading.Lock()


class ServingError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.public_message = message


def _card(model_id: str) -> tuple[dict, str]:
    with db.context.as_system():                     # internal lookup; who may run a model is decided by the calling route
        row = db.q1("SELECT data,status FROM model_versions WHERE id=?", (model_id,))
    if not row:
        raise ServingError("Unknown model.")
    return json.loads(row["data"]), row["status"]


def abstain_threshold(card: dict) -> float:
    """Smallest confidence threshold at which the held-out accuracy on accepted cases reached 0.80 (else 0.90)."""
    for s in card["test_metrics"].get("selective_prediction", []):
        if s["accuracy_on_accepted"] is not None and s["accuracy_on_accepted"] >= 0.80:
            return float(s["threshold"])
    return 0.90


def _load(model_id: str) -> dict:
    with _lock:
        if model_id in _cache:
            return _cache[model_id]
        card, status = _card(model_id)
        if status not in ("research_only", "approved_for_defined_use"):
            raise ServingError("This model is not available for inference.")
        art = Path(card["artifact_path"])
        if not art.is_file() or T.sha256_file(art) != card["artifact_sha256"]:
            raise ServingError("The model file is missing or does not match its recorded checksum; refusing to load it.")
        size, classes, temperature = card["image_size"], card["classes"], float(card["test_metrics"]["temperature"])
        if art.suffix == ".npz":
            from . import probe
            wf = card["pretrained_weights"]
            if not Path(wf).is_file() or T.sha256_file(wf) != card["pretrained_sha256"]:
                raise ServingError("The backbone weights do not match the recorded checksum; refusing to load them.")
            z = np.load(art, allow_pickle=False)
            bundle = {"kind": "probe", "backbone": probe.load_backbone(wf, size=size), "mean": z["mean"], "scale": z["scale"], "coef": z["coef"], "intercept": z["intercept"]}
        else:
            import torch
            m = T.build_model(card["architecture"], len(classes), None, "head")
            m.load_state_dict(torch.load(art, map_location="cpu", weights_only=True))
            bundle = {"kind": "cnn", "model": m.eval()}
        bundle.update(card=card, classes=classes, size=size, temperature=temperature, threshold=abstain_threshold(card))
        _cache.clear()          # keep one model resident: RAM is the binding constraint on small hosts
        _cache[model_id] = bundle
        return bundle


def predict(model_id: str, data: bytes, request_id: str = "", enforce_quality: bool = True) -> dict:
    t0 = time.time()
    b = _load(model_id)
    card = b["card"]
    try:
        img = suitability.decode(data)
    except ValueError:
        return _rejected(model_id, card, ["The file could not be read as an image."], t0, request_id)
    q = suitability.quality_metrics(img)
    problems = [suitability.MESSAGES[r] for r in suitability.quality_issues(q, "skin_closeup")]
    if problems and enforce_quality:
        return _rejected(model_id, card, problems, t0, request_id)

    import cv2
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    arr = np.asarray(T._square_resize(pil, b["size"]))[None]
    if b["kind"] == "probe":
        from . import probe
        feat = probe.extract(b["backbone"], arr, "cpu")
        logits = probe.head_logits((feat - b["mean"]) / b["scale"], b["coef"], b["intercept"])
    else:
        logits = T.predict_logits(b["model"], arr, "cpu")
    p = T.softmax(logits, b["temperature"])[0]
    order = np.argsort(-p)
    top = [{"label": b["classes"][i], "probability": round(float(p[i]), 4)} for i in order[:3]]
    conf = float(p[order[0]])
    ood = card["test_metrics"].get("ood", {}).get("auroc_confidence_separates_in_vs_out")
    return {
        "status": "abstained" if conf < b["threshold"] else "predicted", "research_only": True,
        "top": top, "all_probabilities": {c: round(float(x), 4) for c, x in zip(b["classes"], p)}, "confidence": round(conf, 4),
        "abstain_below": b["threshold"], "abstained": conf < b["threshold"],
        "interpretation": "Research output only. Not a diagnosis and not for clinical decisions or patient display.",
        "warnings": ["Trained on PAD-UFES-20 smartphone lesion photos (mostly Fitzpatrick 1-3); not valid for other image types or darker skin tones.",
                     f"Confidence cannot flag unsupported images (measured AUROC {ood}); a confident answer does not mean the image is suitable." if ood is not None else
                     "Out-of-distribution behaviour was not evaluated.",
                     "Held-out sensitivity for melanoma, BCC and SCC was below the 0.80 release requirement."],
        "model": _model_info(model_id, card), "image_quality": q, "processing_ms": round((time.time() - t0) * 1000), "request_id": request_id, "timestamp": db.now(),
    }


def _model_info(model_id: str, card: dict) -> dict:
    return {"id": model_id, "architecture": card["architecture"], "dataset": f'{card["dataset_id"]} v{card["dataset_version"]}', "code_revision": card.get("code_revision"),
            "artifact_sha256": card["artifact_sha256"][:16], "test_macro_f1": card["test_metrics"]["macro_f1"]}


def _rejected(model_id, card, problems, t0, request_id) -> dict:
    return {"status": "rejected_input", "research_only": True, "problems": problems, "corrective_action": "Provide a sharp, well-lit image of at least 300 px.",
            "model": _model_info(model_id, card), "processing_ms": round((time.time() - t0) * 1000), "request_id": request_id, "timestamp": db.now()}

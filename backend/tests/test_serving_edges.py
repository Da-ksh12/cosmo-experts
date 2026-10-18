"""Serving + probe edge cases against small REAL models trained on synthetic data inside the test."""
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app import db
from app.datasets import importer
from app.training import probe, serving, trainer

DINO = Path(__file__).resolve().parent.parent / "data" / "models" / "pretrained" / "dinov2_small" / "model.safetensors"


@pytest.fixture(scope="module", autouse=True)
def _database():
    db.init()


def textured(seed, size=420):
    return np.random.default_rng(seed).integers(40, 210, (size, size, 3), dtype=np.uint8)


def png(arr):
    return cv2.imencode(".png", arr)[1].tobytes()


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    """Version of a synthetic two-class dataset + a trained CNN and (if the weights exist) a trained probe."""
    root = tmp_path_factory.mktemp("serv")
    rows = ["filename,label,group_id,fitzpatrick,source"]
    rng = np.random.default_rng(0)
    for i in range(70):
        cls = "a" if i % 2 == 0 else "b"
        img = np.zeros((96, 96, 3), np.uint8)
        img[:] = (30, 30, 200) if cls == "a" else (200, 60, 30)
        img = np.clip(img + rng.integers(0, 40, img.shape), 0, 255).astype(np.uint8)
        cv2.imwrite(str(root / f"{i}.png"), img)
        rows.append(f"{i}.png,{cls},g{i // 2},{(i % 3) + 1},synthetic")
    (root / "l.csv").write_text("\n".join(rows))
    ver = importer.ingest_folder("serv-ds", root, "tester", root / "l.csv")["version_id"]
    cnn = trainer.train("lesion_classification_research", "serv-ds", ver, "tester", epochs=1, batch=16, image_size=96)["model_id"]
    out = {"version": ver, "cnn": cnn, "probe": None}
    if DINO.is_file():
        out["probe"] = probe.train_probe("lesion_classification_research", "serv-ds", ver, "tester", str(DINO), {"source": "test"}, image_size=224)["model_id"]
    return out


@pytest.fixture(autouse=True)
def clean_cache():
    serving._cache.clear()
    yield
    serving._cache.clear()


def card(mid):
    return json.loads(db.q1("SELECT data FROM model_versions WHERE id=?", (mid,))["data"])


def setcard(mid, **kw):
    c = card(mid); c.update(kw)
    db.x("UPDATE model_versions SET data=? WHERE id=?", (json.dumps(c), mid))


# ---------------------------------------------------------------- registry-level refusals
def test_unknown_model_and_non_servable_status(tiny):
    with pytest.raises(serving.ServingError, match="Unknown"):
        serving.predict("does-not-exist", png(textured(1)))
    mid = tiny["cnn"]
    for st in ("archived", "rejected", "draft"):
        db.x("UPDATE model_versions SET status=? WHERE id=?", (st, mid))
        with pytest.raises(serving.ServingError, match="not available"):
            serving.predict(mid, png(textured(1)))
    db.x("UPDATE model_versions SET status='research_only' WHERE id=?", (mid,))
    assert serving.predict(mid, png(textured(1)))["status"] in ("predicted", "abstained")


def test_missing_artifact_and_checksum_mismatch(tiny):
    mid = tiny["cnn"]
    c = card(mid)
    art = Path(c["artifact_path"]); good = art.read_bytes()
    art.unlink()
    with pytest.raises(serving.ServingError, match="missing|checksum"):
        serving.predict(mid, png(textured(1)))
    art.write_bytes(good)
    setcard(mid, artifact_sha256="0" * 64)
    with pytest.raises(serving.ServingError, match="checksum"):
        serving.predict(mid, png(textured(1)))
    setcard(mid, artifact_sha256=c["artifact_sha256"])
    assert serving.predict(mid, png(textured(1)))["status"] in ("predicted", "abstained")


@pytest.mark.skipif(not DINO.is_file(), reason="DINOv2 weights not downloaded")
def test_probe_backbone_checksum_mismatch_is_refused(tiny):
    mid = tiny["probe"]
    good = card(mid)["pretrained_sha256"]
    setcard(mid, pretrained_sha256="f" * 64)
    with pytest.raises(serving.ServingError, match="backbone"):
        serving.predict(mid, png(textured(1)))
    setcard(mid, pretrained_sha256=good)
    assert serving.predict(mid, png(textured(1)))["model"]["architecture"].startswith("dinov2")


def test_abstain_threshold_selection():
    t = lambda rows: serving.abstain_threshold({"test_metrics": {"selective_prediction": rows}})
    assert t([{"threshold": 0.0, "accuracy_on_accepted": 0.6}, {"threshold": 0.7, "accuracy_on_accepted": 0.81}, {"threshold": 0.9, "accuracy_on_accepted": 0.95}]) == 0.7
    assert t([{"threshold": 0.5, "accuracy_on_accepted": 0.6}]) == 0.90                       # never reaches 0.80 -> most conservative
    assert t([{"threshold": 0.9, "accuracy_on_accepted": None}]) == 0.90
    assert t([]) == 0.90


# ---------------------------------------------------------------- hostile / unusual inputs
def test_exif_orientation_is_honoured(tiny):
    """Decoding must undo EXIF rotation, checked on pixels of an ASYMMETRIC image (noise is rotation-invariant and would prove nothing)."""
    from app.imaging import suitability
    upright = textured(7).copy()
    upright[:80, :200] = (0, 0, 255)                                   # a red marker in the top-left corner
    for tag, stored in ((6, np.rot90(upright, k=1)), (3, np.rot90(upright, k=2)), (8, np.rot90(upright, k=-1))):
        im = Image.fromarray(cv2.cvtColor(np.ascontiguousarray(stored), cv2.COLOR_BGR2RGB))
        exif = Image.Exif(); exif[0x0112] = tag
        buf = io.BytesIO(); im.save(buf, "PNG", exif=exif)
        assert np.array_equal(suitability.decode(buf.getvalue()), upright), f"orientation {tag}"
    mid = tiny["cnn"]
    assert serving.predict(mid, buf.getvalue())["status"] in ("predicted", "abstained")


@pytest.mark.parametrize("mode", ["L", "RGBA", "P", "CMYK", "I;16"])
def test_unusual_pixel_formats_do_not_crash(tiny, mode):
    mid = tiny["cnn"]
    base = Image.fromarray(cv2.cvtColor(textured(11), cv2.COLOR_BGR2RGB))
    im = base.convert("L").convert(mode) if mode == "I;16" else base.convert(mode)
    buf = io.BytesIO()
    im.save(buf, "TIFF" if mode == "CMYK" else "PNG")
    out = serving.predict(mid, buf.getvalue())
    assert out["status"] in ("predicted", "abstained", "rejected_input") and out["research_only"] is True


def test_truncated_corrupt_empty_and_oversized_inputs_are_rejected_not_crashed(tiny):
    mid = tiny["cnn"]
    ok = cv2.imencode(".jpg", textured(5))[1].tobytes()
    for name, data in {"truncated": ok[: len(ok) // 3], "empty": b"", "random": np.random.default_rng(1).bytes(5000), "text": b"hello"}.items():
        r = serving.predict(mid, data)
        assert r["status"] == "rejected_input" and "predict" not in r.get("top", {}) and not r.get("top"), name
    big = io.BytesIO(); Image.new("L", (9000, 9000)).save(big, "PNG")                    # ~81 MP decompression-bomb style input
    r = serving.predict(mid, big.getvalue())
    assert r["status"] == "rejected_input"


def test_unusable_quality_is_rejected_and_can_be_forced_for_equivalence_checks(tiny):
    mid = tiny["cnn"]
    dark = (textured(3) * 0.2).astype(np.uint8)
    r = serving.predict(mid, png(dark))
    assert r["status"] == "rejected_input" and any("dark" in p for p in r["problems"]) and "top" not in r
    forced = serving.predict(mid, png(dark), enforce_quality=False)
    assert forced["status"] in ("predicted", "abstained")


def test_outputs_are_valid_probabilities_and_consistent(tiny):
    for key in ("cnn", "probe"):
        mid = tiny[key]
        if not mid:
            continue
        r = serving.predict(mid, png(textured(21)))
        p = list(r["all_probabilities"].values())
        assert abs(sum(p) - 1) < 2e-3 and all(0 <= x <= 1 for x in p)
        assert r["top"][0]["probability"] == max(p) and r["confidence"] == r["top"][0]["probability"]
        assert r["abstained"] == (r["confidence"] < r["abstain_below"]) and r["status"] == ("abstained" if r["abstained"] else "predicted")
        assert r["model"]["id"] == mid and r["research_only"] is True and r["interpretation"].startswith("Research output only")


def test_concurrent_requests_load_once_and_agree(tiny):
    mid = tiny["cnn"]
    data = png(textured(33))
    with ThreadPoolExecutor(8) as ex:
        outs = list(ex.map(lambda _: serving.predict(mid, data)["all_probabilities"], range(12)))
    assert all(o == outs[0] for o in outs) and list(serving._cache) == [mid]


def test_only_one_model_stays_resident(tiny):
    serving.predict(tiny["cnn"], png(textured(2)))
    if tiny["probe"]:
        serving.predict(tiny["probe"], png(textured(2)))
        assert list(serving._cache) == [tiny["probe"]]


# ---------------------------------------------------------------- probe / trainer failure modes
def test_probe_head_logits_binary_and_multiclass():
    X = np.eye(3, dtype=np.float32)
    z2 = probe.head_logits(X, np.array([[1.0, 0.0, 0.0]]), np.array([0.5]))
    assert z2.shape == (3, 2) and np.allclose(z2[:, 0], -z2[:, 1])
    z3 = probe.head_logits(X, np.eye(3), np.zeros(3))
    assert z3.shape == (3, 3) and np.allclose(z3, X)


@pytest.mark.skipif(not DINO.is_file(), reason="DINOv2 weights not downloaded")
def test_probe_trains_with_ood_set_and_resnet_baseline_and_records_provenance(tiny, tmp_path):
    ood = tmp_path / "ood"; ood.mkdir()
    for i in range(5):
        cv2.imwrite(str(ood / f"o{i}.png"), textured(100 + i, 300))
    c = probe.train_probe("lesion_classification_research", "serv-ds", tiny["version"], "tester", str(DINO), {"source": "t"}, image_size=224, ood_dir=str(ood))
    t = c["test_metrics"]
    assert t["ood"]["n"] == 5 and 0 <= t["ood"]["auroc_confidence_separates_in_vs_out"] <= 1 and set(t["robustness"]) == {"blur", "dark", "jpeg20", "rotate90"}
    assert c["pretrained_sha256"] == trainer.sha256_file(DINO) and c["selected_C"] in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0) and c["code_revision"].startswith("src-")
    assert abs(c["split_sizes"]["train"] + c["split_sizes"]["val"] + c["split_sizes"]["test"] - 70) <= 2


@pytest.mark.skipif(not DINO.is_file(), reason="DINOv2 weights not downloaded")
def test_probe_refuses_a_single_class_dataset(tmp_path):
    root = tmp_path / "one"; root.mkdir()
    rows = ["filename,label,group_id,fitzpatrick,source"]
    for i in range(30):
        cv2.imwrite(str(root / f"{i}.png"), textured(200 + i, 96)); rows.append(f"{i}.png,only,g{i},1,s")
    (root / "l.csv").write_text("\n".join(rows))
    ver = importer.ingest_folder("one-ds", root, "t", root / "l.csv")["version_id"]
    with pytest.raises(Exception):
        probe.train_probe("lesion_classification_research", "one-ds", ver, "t", str(DINO), {}, image_size=224)


def test_trainer_validation_errors(tiny):
    with pytest.raises(ValueError, match="unknown dataset version"):
        trainer.train("t", "serv-ds", "nope", "x")
    with pytest.raises(ValueError, match="unsupported architecture"):
        trainer.build_model("vgg", 2, None, "head")
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (tiny["version"],))["manifest"])
    sp = trainer.stratified_group_split([e for e in mf["entries"] if e["label"]], 17)
    groups = {k: {e["group_id"] for e in v} for k, v in sp.items()}
    assert not (groups["train"] & groups["test"] or groups["train"] & groups["val"] or groups["val"] & groups["test"])        # patient-level, no leakage


def test_training_can_be_stopped_and_reports_progress(tiny):
    events, flag = [], {"n": 0}

    def stop():
        flag["n"] += 1
        return flag["n"] > 2
    with pytest.raises(trainer.Stopped):
        trainer.train("lesion_classification_research", "serv-ds", tiny["version"], "tester", epochs=3, batch=8, image_size=96, progress=events.append, should_stop=stop)
    assert any("log" in e for e in events)                                       # real log lines were emitted before the stop
    assert db.q1("SELECT COUNT(*) c FROM model_versions WHERE data LIKE '%\"epochs\": 3%'")["c"] == 0      # a stopped run registers nothing

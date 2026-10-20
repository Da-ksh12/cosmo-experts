"""Phase 5 training layer: resumable image cache and feature store, the auto-tuner, subject-level splits across sources, the leakage audit, a full probe run on synthetic images (real backbones),
and serving of the resulting model. Synthetic images test the CODE; nothing here says anything about a clinical task."""
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import MODEL_DIR
from app.datasets import importer, registry
from app.main import app
from app.training import featuretrain as FT, features as FE, serving, trainer as T

REAL = Path(__file__).resolve().parent.parent / "data" / "models" / "pretrained"
HAVE_WEIGHTS = (REAL / "dinov2_small" / "model.safetensors").is_file() and (REAL / "resnet18_tv_in1k.pth").is_file()
pytestmark = pytest.mark.skipif(not HAVE_WEIGHTS, reason="the pretrained backbone weights are not present on this machine")
CLASSES = ("MEL", "BCC", "NEV")
COLORS = {"MEL": (30, 30, 200), "BCC": (30, 200, 30), "NEV": (200, 60, 30)}


@pytest.fixture(scope="module", autouse=True)
def client():
    with TestClient(app):
        yield


@pytest.fixture(scope="module", autouse=True)
def real_backbones():
    saved = {k: dict(v) for k, v in FE.BACKBONES.items()}
    FE.BACKBONES["dinov2_s14"].update(weights=REAL / "dinov2_small" / "model.safetensors", provenance=REAL / "dinov2_small" / "provenance.json")
    FE.BACKBONES["resnet18_in1k"].update(weights=REAL / "resnet18_tv_in1k.pth", provenance=REAL / "resnet18_tv_in1k.provenance.json")
    yield
    for k, v in saved.items():
        FE.BACKBONES[k].update(v)


def make_source(root: Path, n: int, seed: int, nogroup: set[int] = frozenset(), official: bool = False, prov_prefix: str = "t") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    rows = ["filename,label,group_id,fitzpatrick,source"]
    for i in range(n):
        cls = CLASSES[i % 3]
        img = np.full((72, 72, 3), COLORS[cls], np.uint8)
        img = np.clip(img.astype(int) + rng.integers(0, 50, img.shape), 0, 255).astype(np.uint8)
        cv2.circle(img, (int(rng.integers(15, 55)), int(rng.integers(15, 55))), int(rng.integers(4, 12)), (255, 255, 255), -1)
        cv2.imwrite(str(root / f"{seed}_{i}.png"), img)
        grp = f"u{seed}_{i}" if i in nogroup else f"g{seed}_{i // 2}"
        tag = f"{prov_prefix}:{'nogroup' if i in nogroup else 'group'}" + (f":{'test' if i % 7 == 0 else 'train'}:x" if official else "")
        rows.append(f"{seed}_{i}.png,{cls},{grp},{(i % 6) + 1},{tag}")
    (root.parent / f"{root.name}.csv").write_text("\n".join(rows))
    return root.parent / f"{root.name}.csv"


def approved(did: str):
    d = dict(id=did, name=did, url="https://example.org", maintainer="x", description="x", intended_task="x", modality="clinical photographs", annotations=["lesion diagnosis"], populations="x",
             claimed_license="CC BY 4.0", attribution="x", download_method="x", version="1", limitations="x", terms_verified=True, terms_snapshot_hash="hh", verified_spdx="CC-BY-4.0",
             commercial_eligibility="yes", approved_uses=["model_training"], local_path="", file_hashes={}, permission_document="", approval_status="approved")
    registry.save(d)


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    base = tmp_path_factory.mktemp("p5")
    out = {}
    for did, n, seed, kw in (("p5t-main", 63, 1, {"nogroup": {3, 10, 17, 24, 31}}), ("p5t-ext", 18, 2, {})):
        approved(did)
        root = base / did
        csv = make_source(root, n, seed, **kw)
        out[did] = importer.ingest_folder(did, root, "admin1", csv)
    yield out
    with db.context.as_system():
        db.x("DELETE FROM dataset_versions WHERE dataset_id LIKE 'p5t-%'")
        db.x("DELETE FROM datasets WHERE id LIKE 'p5t-%'")
        db.x("DELETE FROM model_versions WHERE task='lesion_classification_research' AND data LIKE '%p5t-main%'")


# ---------------------------------------------------------------- units: splits, leakage, case aggregation
def entries(n, nogroup=(), prov=None):
    """An image with no patient identifier is its own group (as in the real manifests); the others share a patient with one neighbour."""
    return [{"label": CLASSES[i % 3], "group_id": (f"u{i}" if i in nogroup else f"g{i // 2}"), "sha256": f"s{i}", "file": f"{i}.png", "_src": "a", "_row": i,
             "_gkey": (f"a:u{i}" if i in nogroup else f"a:g{i // 2}"), "_nogroup": i in nogroup, "label_provenance": (prov(i) if prov else "x")} for i in range(n)]


def test_images_without_an_identifier_are_trained_on_but_never_validated_or_tested():
    src = {"spec": FT.SourceSpec("a", "d", "v", unknown_group_train_only=True), "entries": entries(84, nogroup=set(range(0, 84, 5)))}
    sp = FT.split_source(src, 17)
    unknown = {i for i in range(84) if i % 5 == 0}
    assert all(e["_row"] in unknown for e in sp["train"] if e["_nogroup"]) and not any(e["_nogroup"] for e in sp["val"] + sp["test"])
    assert unknown <= {e["_row"] for e in sp["train"]}
    groups = [{e["_gkey"] for e in sp[k]} for k in ("train", "val", "test")]
    assert not (groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])


def test_the_official_split_is_respected_and_patients_who_straddle_it_go_to_training():
    es = entries(84, prov=lambda i: f"s:{'test' if (i // 2) % 7 == 0 else 'train'}:c")                        # a patient's images share their official split
    es[1]["group_id"] = es[0]["group_id"] = "straddler"; es[0]["_gkey"] = es[1]["_gkey"] = "a:straddler"      # ... except this one: row 0 is official test, row 1 official train, same patient
    es[1]["label_provenance"] = "s:train:c"
    src = {"spec": FT.SourceSpec("a", "d", "v", split_policy="official"), "entries": es}
    sp = FT.split_source(src, 17)
    assert all(":test:" in e["label_provenance"] for e in sp["test"]) and 0 not in {e["_row"] for e in sp["test"]} and {0, 1} <= {e["_row"] for e in sp["train"]}
    assert sp["_note"]["patients_in_both_official_splits_moved_to_train"] == 1


def test_the_same_deterministic_splitter_gives_pad_ufes_20_the_same_test_set_as_the_earlier_models():
    es = entries(120)
    a = FT.split_source({"spec": FT.SourceSpec("a", "d", "v"), "entries": es}, 17)
    b = T.stratified_group_split(es, 17)
    assert [e["_row"] for e in a["test"]] == [e["_row"] for e in b["test"]] and [e["_row"] for e in a["val"]] == [e["_row"] for e in b["val"]]


def test_near_duplicate_rows_are_found_by_thumbnail_correlation_and_distinct_images_are_not():
    rng = np.random.default_rng(0)
    base = rng.integers(0, 255, (5, 224, 224, 3), np.uint8)
    other = rng.integers(0, 255, (3, 224, 224, 3), np.uint8)
    noisy = np.clip(base[[1, 3]].astype(int) + rng.integers(-3, 4, (2, 224, 224, 3)), 0, 255).astype(np.uint8)           # re-encoded copies of images 1 and 3
    A = FT.thumbs(np.concatenate([noisy, other]), list(range(5)))
    B = FT.thumbs(base, list(range(5)))
    assert FT.near_duplicate_rows(A, B).tolist() == [0, 1]


def test_case_level_scores_average_the_logits_of_a_cases_photographs():
    es = [{"_gkey": "c1", "fitzpatrick": 5, "file": "a"}, {"_gkey": "c1", "fitzpatrick": 5, "file": "b"}, {"_gkey": "c2", "fitzpatrick": 2, "file": "c"}]
    Z = np.array([[3.0, 0.0], [0.0, 1.0], [0.0, 4.0]])                      # image 2 alone would be wrong for c1; the mean of its two photographs is right
    out = FT._case_level(es, Z, 1.0, np.array([0, 0, 1]), ["x", "y"])
    assert out["n"] == 2 and out["accuracy"] == 1.0 and out["unit"].startswith("case")


# ---------------------------------------------------------------- resumable cache and features, auto-tuner
def test_the_image_cache_and_feature_store_resume_where_they_stopped_and_give_identical_results(sources, tmp_path):
    es = [e for e in json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (sources["p5t-main"]["version_id"],))["manifest"])["entries"]][:40]
    root = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (sources["p5t-main"]["version_id"],))["manifest"])["root"]
    state = {"n": 0}
    FE.CACHE_CHUNK = 16
    def stop_after_first(*_):
        state["n"] += 1
        return state["n"] >= 1
    with pytest.raises(FE.Paused) as e1:
        FE.image_cache(root, es, should_stop=stop_after_first)
    assert 0 < e1.value.done < len(es)
    X = FE.image_cache(root, es)
    FE.CACHE_CHUNK = 192
    assert X.shape == (40, 224, 224, 3) and np.array_equal(np.asarray(X[0]), np.asarray(T._square_resize(__import__("PIL.Image", fromlist=["Image"]).open(Path(root) / es[0]["file"]), 224)))
    state["n"] = 0
    with pytest.raises(FE.Paused):
        FE.extract_features("dinov2_s14", X, es, batch=8, should_stop=stop_after_first)
    prog = json.loads((FE.store_dir("dinov2_s14", es) / "progress.json").read_text())
    assert prog["rows"] == 8
    resumed = np.array(FE.extract_features("dinov2_s14", X, es, batch=8))
    fresh = FE.extract_batch("dinov2_s14", FE.load_backbone("dinov2_s14"), np.asarray(X[:8]))
    assert resumed.shape == (40, 768) and np.allclose(resumed[:8], fresh, atol=1e-4) and np.isfinite(resumed).all()
    cls = resumed[:, :384]
    from app.training import probe
    assert np.allclose(cls[:8], probe.extract(FE.load_backbone("dinov2_s14"), np.asarray(X[:8]), "cpu"), atol=1e-4)        # the CLS part is what the earlier probes and serving computed


def test_the_autotuner_measures_candidates_and_a_tight_machine_gets_small_batches(sources, monkeypatch):
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (sources["p5t-main"]["version_id"],))["manifest"])
    X = FE.image_cache(mf["root"], mf["entries"][:24])
    t = FE.autotune("dinov2_s14", X, "cpu", sample=16)
    assert t["chosen"] in t["measured"] and t["chosen"]["images_per_s"] > 0 and "fp32" in t["precision"]
    monkeypatch.setattr(FE, "available_mb", lambda: 300.0)
    assert {m["batch"] for m in FE.autotune("dinov2_s14", X, "cpu", sample=16)["measured"]} == {8}


def test_a_run_pauses_safely_when_free_memory_stays_too_low(sources, monkeypatch):
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (sources["p5t-ext"]["version_id"],))["manifest"])
    X = FE.image_cache(mf["root"], mf["entries"])
    monkeypatch.setattr(FE, "available_mb", lambda: 5.0)
    monkeypatch.setattr(FE, "PAUSE_AFTER_S", 0.0)
    monkeypatch.setattr(FE, "POLL_S", 0.01)
    with pytest.raises(FE.Paused, match="free memory"):
        FE.extract_features("resnet18_in1k", X, mf["entries"], batch=8)
    monkeypatch.undo()
    assert FE.extract_features("resnet18_in1k", X, mf["entries"], batch=8).shape == (18, 512)                            # and it carries on once memory is back


# ---------------------------------------------------------------- a full run
@pytest.fixture(scope="module")
def card(sources):
    specs = [FT.SourceSpec("main", "p5t-main", sources["p5t-main"]["version_id"], unknown_group_train_only=True), FT.SourceSpec("ext", "p5t-ext", sources["p5t-ext"]["version_id"], role="external")]
    c = FT.run("lesion_classification_research", "p5-unit-test", specs, "admin1", intended_use="Research only.", limitations=["synthetic"], serving_warnings=["Synthetic test model."], robustness_n=8)
    yield c
    with db.context.as_system():
        db.x("DELETE FROM model_versions WHERE id=?", (c["model_id"],))
    for p in MODEL_DIR.glob(f"lesion_classification_research/{c['model_id']}*"):
        p.unlink(missing_ok=True)


def test_a_run_registers_an_experimental_research_model_with_everything_needed_to_reproduce_it(card):
    row = db.q1("SELECT status, data FROM model_versions WHERE id=?", (card["model_id"],))
    assert row["status"] == "research_only" and json.loads(row["data"])["experiment"] == "p5-unit-test" and card["approval"] is None
    assert card["classes"] == sorted(CLASSES) and card["feature_kind"] in ("cls", "cls+mean") and card["pretrained_sha256"] and card["artifact_sha256"] == T.sha256_file(card["artifact_path"])
    assert [s["name"] for s in card["sources"]] == ["main", "ext"] and all(s["manifest_sha256"] and s["license"]["spdx"] == "CC-BY-4.0" for s in card["sources"])
    r = card["resources"]
    assert r["autotune"]["chosen"] and r["peak_working_set_mb"] > 0 and r["artifact_bytes"] > 0 and {"dinov2_features", "leakage_audit", "baselines"} <= set(r["stage_seconds"]) and r["device"] == "cpu"
    ev = db.q("SELECT event FROM model_events WHERE model_id=?", (card["model_id"],))
    assert [e["event"] for e in ev] == ["registered"]
    run = db.q1("SELECT status, model_id FROM training_runs WHERE id=?", (card["pipeline"]["run_id"],))
    assert run["status"] == "done" and run["model_id"] == card["model_id"]
    assert (MODEL_DIR / "lesion_classification_research" / f"{card['model_id']}.model-card.md").is_file()


def test_the_model_selection_is_on_validation_and_every_baseline_is_reported_beside_it(card):
    assert {c["feature_kind"] for c in card["model_selection"]} == {"cls", "cls+mean"} and all("val_macro_f1" in c for c in card["model_selection"])
    assert set(card["baselines"]) == {"majority_class", "dinov2_knn_cosine", "resnet18_frozen_features_logreg"}
    t = card["test_metrics"]
    assert t["n"] == card["split_sizes"]["test"] and 0 <= t["macro_f1"] <= 1 and t["temperature"] > 0
    assert set(t["robustness"]) == {"blur", "dark", "jpeg20", "rotate90"}                      # same layout as the earlier models: every entry is a perturbation
    assert t["robustness_reference"]["subset_n"] == 8 and 0 <= t["robustness_reference"]["clean"]["macro_f1"] <= 1


def test_subjects_never_straddle_splits_and_unidentified_images_are_only_in_training(card, sources):
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (sources["p5t-main"]["version_id"],))["manifest"])
    nogroup = {e["sha256"] for e in mf["entries"] if "nogroup" in e["label_provenance"]}
    assert card["split_sizes"]["train"] >= len(nogroup) and sum(card["split_sizes"].values()) <= len(mf["entries"])
    assert card["test_metrics"]["by_identifier_known"].keys() == {"patient/lesion id known"}


def test_an_external_source_is_scored_but_never_part_of_training_validation_or_test(card):
    ext = card["test_metrics"]["external"]["ext"]
    assert ext["n"] > 0 and "never used for training" in ext["note"] and card["test_metrics"]["by_source"].keys() == {"main"}
    assert sum(card["split_sizes"].values()) < sum(s["images"] for s in card["sources"])             # the external images are not in any split


def test_the_registered_model_is_served_through_the_existing_research_interface(card, tmp_path):
    img = np.full((120, 120, 3), COLORS["MEL"], np.uint8)
    img = np.clip(img.astype(int) + np.random.default_rng(5).integers(0, 50, img.shape), 0, 255).astype(np.uint8)
    ok, jpg = cv2.imencode(".jpg", img)
    out = serving.predict(card["model_id"], jpg.tobytes(), enforce_quality=False)
    assert out["status"] in ("predicted", "abstained") and out["research_only"] and set(out["all_probabilities"]) == set(card["classes"]) and abs(sum(out["all_probabilities"].values()) - 1) < 1e-3
    assert "Synthetic test model." in out["warnings"] and any("release gates" in w for w in out["warnings"])
    # serving computes exactly what the evaluation computed: same preprocessing, same features, same head
    from PIL import Image
    from app.training import probe
    z = np.load(card["artifact_path"], allow_pickle=False)
    pil = Image.fromarray(cv2.cvtColor(cv2.imdecode(jpg, 1), cv2.COLOR_BGR2RGB))
    f = FE.extract_batch("dinov2_s14", FE.load_backbone("dinov2_s14"), np.asarray(T._square_resize(pil, 224))[None])[:, :384 if card["feature_kind"] == "cls" else 768]
    p = T.softmax(probe.head_logits((f - z["mean"]) / z["scale"], z["coef"], z["intercept"]), float(z["temperature"]))[0]
    assert np.allclose([out["all_probabilities"][c] for c in card["classes"]], np.round(p, 4), atol=2e-4)


def test_licence_gate_is_enforced_before_any_data_is_read(sources):
    rec = registry.get("p5t-ext")
    rec["approval_status"] = "rejected"
    registry.save(rec)
    try:
        with pytest.raises(FT.Blocked, match="not approved|administrator"):
            FT.load_source(FT.SourceSpec("ext", "p5t-ext", sources["p5t-ext"]["version_id"], role="external"))
    finally:
        rec["approval_status"] = "approved"
        registry.save(rec)


# ---------------------------------------------------------------- the fine-tune comparison arm (ResNet18, same sources, same splits)
@pytest.fixture(scope="module")
def ft(sources, card):
    from app.training import finetune
    specs = [FT.SourceSpec("main", "p5t-main", sources["p5t-main"]["version_id"], unknown_group_train_only=True), FT.SourceSpec("ext", "p5t-ext", sources["p5t-ext"]["version_id"], role="external")]
    args = dict(epochs=2, batch=8, intended_use="Research only.", limitations=["synthetic"], serving_warnings=["Synthetic test model."], robustness_n=8)
    with pytest.raises(FE.Paused) as stopped:                                  # a time limit that is already over: one complete epoch, a checkpoint, then a safe stop
        finetune.run("lesion_classification_research", "p5-ft-unit-test", specs, "admin1", max_seconds=1e-6, **args)
    paused = db.q1("SELECT status, error FROM training_runs WHERE config LIKE '%p5-ft-unit-test%' ORDER BY started_at DESC")
    c = finetune.run("lesion_classification_research", "p5-ft-unit-test", specs, "admin1", **args)
    yield {"card": c, "stopped": stopped.value, "paused": paused}
    with db.context.as_system():
        db.x("DELETE FROM model_versions WHERE id=?", (c["model_id"],))
    for p in MODEL_DIR.glob(f"lesion_classification_research/{c['model_id']}*"):
        p.unlink(missing_ok=True)


def test_a_fine_tune_stops_safely_after_a_complete_epoch_and_resumes_from_its_checkpoint(ft):
    assert ft["stopped"].done == 1 and ft["paused"]["status"] == "stopped" and "checkpoint is kept" in ft["paused"]["error"]
    c = ft["card"]
    assert c["resumed"]["from_epoch"] == 1 and [h["epoch"] for h in c["history"]] == [1, 2] and c["architecture"] == "resnet18" and c["finetune"] == "layer4"


def test_the_fine_tune_is_scored_on_the_same_images_as_the_probe_and_the_card_holds_the_whole_comparison(ft, card):
    c = ft["card"]
    assert c["split_hash"] == card["split_hash"] and c["test_metrics"]["n"] == card["test_metrics"]["n"]               # the same test images
    assert set(c["baselines"]) == {"majority_class", "dinov2_frozen_probe", "dinov2_knn_cosine", "resnet18_frozen_features_logreg"}
    for key in ("macro_f1", "balanced_accuracy", "accuracy"):                                                    # the probe re-computed here IS the registered probe
        assert c["baselines"]["dinov2_frozen_probe"][key] == card["test_metrics"][key]
    assert c["near_duplicates_removed_before_evaluation"] == card["near_duplicates_removed_before_evaluation"]
    assert c["test_metrics"]["external"]["ext"]["n"] > 0 and set(c["test_metrics"]["robustness"]) == {"blur", "dark", "jpeg20", "rotate90"}


def test_the_fine_tuned_model_is_registered_experimental_and_served_by_the_existing_interface(ft):
    c = ft["card"]
    row = db.q1("SELECT status FROM model_versions WHERE id=?", (c["model_id"],))
    assert row["status"] == "research_only" and c["approval"] is None and c["artifact_sha256"] == T.sha256_file(c["artifact_path"])
    img = np.full((120, 120, 3), COLORS["BCC"], np.uint8)
    ok, jpg = cv2.imencode(".jpg", np.clip(img.astype(int) + np.random.default_rng(6).integers(0, 50, img.shape), 0, 255).astype(np.uint8))
    out = serving.predict(c["model_id"], jpg.tobytes(), enforce_quality=False)
    assert out["research_only"] and set(out["all_probabilities"]) == set(c["classes"]) and abs(sum(out["all_probabilities"].values()) - 1) < 1e-3 and out["model"]["architecture"] == "resnet18"


def test_the_fine_tuned_model_carries_the_unresolved_imagenet_licence_statement(ft):
    c = ft["card"]
    assert any("unresolved" in x and "legal review" in x for x in c["limitations"]) and "unresolved" in c["weights_provenance_note"]

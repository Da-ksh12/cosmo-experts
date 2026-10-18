"""Vision evaluation framework: the metrics agree with the trainer's, uncertainty helpers behave, subgroups and paired comparisons are honest, and a registered model can be
re-evaluated on its own reproduced split. Synthetic fixtures test the CODE; they are labelled as synthetic evidence and prove nothing about any real model."""
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.metrics import roc_auc_score

from app import db
from app.ai.evaluation import common, vision as ev
from app.main import app


def rand_probs(n=300, k=4, seed=0, skill=1.2):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, k, n)
    logits = rng.normal(size=(n, k))
    logits[np.arange(n), y] += skill
    P = np.exp(logits) / np.exp(logits).sum(1, keepdims=True)
    return y, P


# ---------------------------------------------------------------- numbers agree with the trainer
def test_metrics_match_the_trainers_definitions():
    from app.training import trainer as T
    y, P = rand_probs()
    classes = ["a", "b", "c", "d"]
    mine = ev.evaluate(y, P, classes, n_boot=50)
    theirs = T.full_metrics(P, y, classes)
    for k in ("macro_f1", "balanced_accuracy", "accuracy"):
        assert mine[k] == pytest.approx(theirs[k], abs=1e-3), k                                       # the trainer rounds to three decimals
    assert mine["confusion_matrix"] == theirs["confusion_matrix"]
    for c in classes:
        assert mine["per_class"][c]["sensitivity"] == pytest.approx(theirs["per_class"][c]["sensitivity"], abs=1e-3)
        assert mine["per_class"][c]["specificity"] == pytest.approx(theirs["per_class"][c]["specificity"], abs=1e-3)
        assert mine["per_class"][c]["n"] == theirs["per_class"][c]["n"]
        assert mine["per_class"][c]["auroc"] == pytest.approx(theirs["per_class"][c]["auroc"], abs=2e-3)


def test_hand_computed_confusion_example():
    y = np.array([0] * 10 + [1] * 10)
    pred = np.array([0] * 8 + [1] * 2 + [1] * 6 + [0] * 4)
    P = np.eye(2)[pred] * 0.9 + 0.05
    r = ev.evaluate(y, P, ["x", "y"], n_boot=20)
    x = r["per_class"]["x"]
    assert (x["tp"], x["fn"], x["fp"], x["tn"]) == (8, 2, 4, 6)
    assert x["sensitivity"] == 0.8 and x["specificity"] == 0.6 and x["precision"] == pytest.approx(8 / 12, abs=1e-3)
    assert r["balanced_accuracy"] == pytest.approx((0.8 + 0.6) / 2) and r["confusion_matrix"] == [[8, 2], [4, 6]]
    assert x["sensitivity_ci95"][0] < 0.8 < x["sensitivity_ci95"][1] and x["reliable"] is False        # 10 positives: below the reliability floor


def test_wilson_and_bootstrap_intervals():
    assert common.wilson(8, 10) == [0.4902, 0.9433]
    assert common.wilson(0, 0) is None and common.wilson(0, 10)[0] == 0.0 and common.wilson(10, 10)[1] == 1.0
    f = lambda idx: float(np.mean(np.arange(100)[idx]))
    a, b = common.bootstrap(f, 100, 200, seed=3), common.bootstrap(f, 100, 200, seed=3)
    assert a == b and a[0] < 49.5 < a[1]                                                               # reproducible and brackets the mean
    assert common.bootstrap(f, 3) is None                                                              # too few samples: no interval is better than a false one


def test_auroc_matches_scikit_learn_including_ties():
    rng = np.random.default_rng(1)
    pos, neg = rng.integers(0, 5, 40).astype(float), rng.integers(0, 5, 60).astype(float)
    assert ev.auroc(pos, neg) == pytest.approx(roc_auc_score(np.r_[np.ones(40), np.zeros(60)], np.r_[pos, neg]))
    assert ev.auroc(np.array([]), neg) is None


def test_calibration_detects_overconfidence():
    rng = np.random.default_rng(2)
    y = rng.integers(0, 2, 2000)
    good = np.where(rng.random(2000) < 0.8, y, 1 - y)                    # 80 % accurate
    conf = 0.8
    P_cal = np.zeros((2000, 2)); P_cal[np.arange(2000), good] = conf; P_cal[np.arange(2000), 1 - good] = 1 - conf
    P_over = np.zeros((2000, 2)); P_over[np.arange(2000), good] = 0.99; P_over[np.arange(2000), 1 - good] = 0.01
    c, o = ev.calibration(P_cal, y), ev.calibration(P_over, y)
    assert c["ece"] < 0.05 and o["ece"] > 0.15 and o["nll"] > c["nll"] and sum(b["n"] for b in c["reliability"]) == 2000


def test_selective_prediction_and_ood_reading():
    y, P = rand_probs(500, 3, seed=5, skill=2.0)
    sel = ev.selective(P, y)
    assert sel[0]["coverage"] == 1.0 and sel[-1]["coverage"] < sel[0]["coverage"]
    Pin = np.array([[0.6, 0.4]] * 60)
    Pout = np.array([[0.95, 0.05]] * 60)                                  # unsupported images get HIGHER confidence
    o = ev.ood(Pin, Pout, "fixture")
    assert o["auroc_confidence_separates_in_vs_out"] == 0.0 and "HIGHER confidence" in o["reading"] and o["reliable"]
    assert ev.ood(Pin[:5], Pout[:5], "tiny")["reliable"] is False


def test_subgroups_report_counts_flag_unreliable_groups_and_only_compare_reliable_ones():
    y, P = rand_probs(400, 3, seed=7)
    pred = P.argmax(1)
    groups = {"site": ["a"] * 200 + ["b"] * 180 + ["c"] * 8 + ["d"] * 12, "sex": [None] * 400}
    sg = ev.subgroups(y, pred, groups, 3, ["x", "y", "z"], critical=["z"])
    lv = sg["site"]["levels"]
    assert lv["a"]["reliable"] and lv["b"]["reliable"] and not lv["c"]["reliable"] and "accuracy" not in lv["c"]            # under 10 images: counts only
    assert not lv["d"]["reliable"] and "accuracy" in lv["d"]
    gap = sg["site"]["largest_gap_among_reliable_groups"]
    assert {gap["lowest"], gap["highest"]} == {"a", "b"} and "not by itself evidence of bias" in gap["note"]
    assert sg["sex"]["levels"]["NA"]["n"] == 400 and sg["sex"]["largest_gap_among_reliable_groups"] is None
    assert "critical_class_sensitivity" in lv["a"] and lv["a"]["critical_class_sensitivity"]["z"]["ci95"]


def test_paired_difference_is_zero_for_identical_models_and_positive_for_a_better_one():
    y, P = rand_probs(400, 3, seed=9, skill=1.5)
    pred = P.argmax(1)
    same = ev.paired_difference(y, pred, pred.copy(), 3, n_boot=200)
    assert same["difference_a_minus_b"] == 0 and same["ci95"] == [0.0, 0.0] and not same["interval_excludes_zero"]
    worse = pred.copy()
    worse[:120] = (worse[:120] + 1) % 3
    d = ev.paired_difference(y, pred, worse, 3, n_boot=300, seed=1)
    assert d["difference_a_minus_b"] > 0 and d["interval_excludes_zero"] and d["p_a_better"] > 0.97
    assert ev.paired_difference(y, pred, worse, 3, n_boot=300, seed=1) == d                              # deterministic


def test_report_header_separates_evidence_classes():
    h = common.header("x", "synthetic_fixture", {"a": 1})
    assert h["real_world_evidence"] is False and h["config_sha256"] == common.sha256_json({"a": 1}) and h["code_revision"]
    assert common.header("x", "real_world_clinical", {})["real_world_evidence"] is True
    with pytest.raises(ValueError):
        common.header("x", "totally_real", {})


# ---------------------------------------------------------------- a registered model re-evaluated on its reproduced split
@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp("evalfx") / "imgs"
    root.mkdir()
    rng = np.random.default_rng(0)
    rows = ["filename,label,group_id,fitzpatrick,source"]
    for i in range(80):
        cls = "a" if i % 2 == 0 else "b"
        img = np.zeros((96, 96, 3), np.uint8)
        img[:] = (30, 30, 200) if cls == "a" else (200, 60, 30)
        img = np.clip(img + rng.integers(0, 40, img.shape), 0, 255).astype(np.uint8)
        cv2.circle(img, (int(rng.integers(20, 76)), int(rng.integers(20, 76))), int(rng.integers(5, 15)), (255, 255, 255), -1)
        cv2.imwrite(str(root / f"{i}.png"), img)
        rows.append(f"{i}.png,{cls},g{i // 2},{(i % 3) + 1},synthetic")
    (root.parent / "labels.csv").write_text("\n".join(rows))
    with TestClient(app):
        from app.datasets import importer
        from app.training import trainer
        imp = importer.ingest_folder("ai-eval-fixture", root, "admin1", root.parent / "labels.csv")
        card = trainer.train("lesion_classification_research", "ai-eval-fixture", imp["version_id"], "admin1", epochs=2, image_size=64)
        yield card, imp
        with db.context.as_system():
            db.x("DELETE FROM model_versions WHERE id=?", (card["model_id"],))
            db.x("DELETE FROM dataset_versions WHERE dataset_id='ai-eval-fixture'")
        shutil.rmtree(Path(card["artifact_path"]).parent / f"{card['model_id']}.pt", ignore_errors=True)


def test_a_registered_model_reproduces_its_card_on_its_own_split_and_reports_gates_unchanged(trained):
    from app.ai.evaluation import models as m
    from app.training import trainer
    card, imp = trained
    rep = m.evaluate_models([card["model_id"]], split="test", ood_dir=Path("does-not-exist"))                  # default 1000 resamples, as the trainer
    r = rep["reports"][card["model_id"]]
    assert r["reproduces_model_card"] == {"split": "test", "accuracy_matches_card": True, "macro_f1_matches_card": True, "confusion_matrix_matches_card": True}
    assert r["dataset"]["split_hash"] == card["split_hash"] and r["dataset"]["id"] == "ai-eval-fixture"
    assert r["model"]["artifact_sha256"] == card["artifact_sha256"] and r["model"]["registry_status"] == "research_only"
    diff = [(a, b) for a, b in zip(r["release_gates"]["recomputed"], r["release_gates"]["stored_card"]) if a["passed"] != b["passed"]]
    assert r["release_gates"]["agree"] is True, diff
    assert r["release_gates"]["passed"] is False                   # an 80-image synthetic fixture cannot pass the gates, and the gates are not edited
    assert [g["gate"] for g in r["release_gates"]["recomputed"]] == [g["gate"] for g in trainer.evaluate_gates(card)]
    assert rep["evidence_class"] == "public_research_dataset" and rep["real_world_evidence"] is False
    assert "fitzpatrick" in r["subgroups"] and r["baseline_comparison"]["baseline"] in card["baselines"]


def test_evaluation_refuses_the_training_split_and_a_split_that_cannot_be_reproduced(trained):
    from app.ai.evaluation import models as m
    card, imp = trained
    with pytest.raises(ValueError, match="never train"):
        m.evaluate_models([card["model_id"]], split="train")
    with db.context.as_system():
        row = db.q1("SELECT data FROM model_versions WHERE id=?", (card["model_id"],))
        tampered = json.loads(row["data"])
        tampered["split_hash"] = "0" * 64
        db.x("UPDATE model_versions SET data=? WHERE id=?", (json.dumps(tampered), card["model_id"]))
    try:
        with pytest.raises(ValueError, match="does not match the hash"):
            m.evaluate_models([card["model_id"]], split="test", ood_dir=Path("does-not-exist"))
    finally:
        with db.context.as_system():
            db.x("UPDATE model_versions SET data=? WHERE id=?", (row["data"], card["model_id"]))


def test_two_registered_models_on_the_same_split_are_compared_pairwise(trained):
    from app.ai.evaluation import models as m
    card, imp = trained
    with db.context.as_system():
        row = db.q1("SELECT data,status,task,created_at FROM model_versions WHERE id=?", (card["model_id"],))
        twin = json.loads(row["data"]); twin["model_id"] = "aieval-twin"
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", ("aieval-twin", row["task"], json.dumps(twin), row["status"], row["created_at"]))
    try:
        rep = m.evaluate_models([card["model_id"], "aieval-twin"], split="test", n_boot=100, ood_dir=Path("does-not-exist"))
        (k, pair), = rep["paired_comparisons"].items()
        assert pair["macro_f1"]["difference_a_minus_b"] == 0 and not pair["macro_f1"]["interval_excludes_zero"]      # identical artifact, identical predictions
    finally:
        with db.context.as_system():
            db.x("DELETE FROM model_versions WHERE id='aieval-twin'")

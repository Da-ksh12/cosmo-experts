"""Dataset registry upgrades, safe import, training orchestration and the reusable task pipeline. Synthetic fixtures test the CODE; the pipeline stamps them as synthetic and they can never
pass the release gates."""
import json
import os
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db
from app.ai import resources
from app.datasets import importer, registry
from app.main import app
from app.training import cards, orchestrator, pipeline, task_specs, tasks, trainer


@pytest.fixture(scope="module", autouse=True)
def client():
    with TestClient(app) as c:
        yield c


def tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


def make_images(root: Path, n=80, per_group=2, fitz=True, classes=("a", "b")):
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    rows = ["filename,label,group_id,fitzpatrick,source"]
    for i in range(n):
        cls = classes[i % len(classes)]
        img = np.zeros((96, 96, 3), np.uint8)
        img[:] = (30, 30, 200) if cls == classes[0] else (200, 60, 30)
        img = np.clip(img + rng.integers(0, 40, img.shape), 0, 255).astype(np.uint8)
        cv2.circle(img, (int(rng.integers(20, 76)), int(rng.integers(20, 76))), int(rng.integers(5, 15)), (255, 255, 255), -1)
        cv2.imwrite(str(root / f"{i}.png"), img)
        rows.append(f"{i}.png,{cls},g{i // per_group},{(i % 3) + 1 if fitz else ''},synthetic")
    (root.parent / f"{root.name}-labels.csv").write_text("\n".join(rows))
    return root.parent / f"{root.name}-labels.csv"


@pytest.fixture(scope="module")
def fixture_dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("trainfx") / "imgs"
    labels = make_images(root)
    imp = importer.ingest_folder("ai-train-fixture", root, "admin1", labels)
    yield imp, root
    with db.context.as_system():
        db.x("DELETE FROM dataset_versions WHERE dataset_id='ai-train-fixture'")
        db.x("DELETE FROM training_runs WHERE dataset_id='ai-train-fixture'")


def cfg_for(imp, **kw):
    base = dict(task_id="lesion_classification_research", dataset_id="ai-train-fixture", version_id=imp["version_id"], epochs=1, image_size=64, batch=8)
    base.update(kw)
    return orchestrator.TrainConfig(**base)


# ---------------------------------------------------------------- configuration
def test_config_hash_is_stable_excludes_scheduling_options_and_rejects_unknown_fields():
    a = orchestrator.TrainConfig(task_id="t", dataset_id="d", version_id="v")
    b = orchestrator.TrainConfig(task_id="t", dataset_id="d", version_id="v", resume=True, allow_long_run=True)
    assert a.config_hash() == b.config_hash() and a.config_hash() != orchestrator.TrainConfig(task_id="t", dataset_id="d", version_id="v", seed=18).config_hash()
    with pytest.raises(Exception):
        orchestrator.TrainConfig(task_id="t", dataset_id="d", version_id="v", surprise=1)
    with pytest.raises(Exception):
        orchestrator.TrainConfig(task_id="t", dataset_id="d", version_id="v", epochs=0)


# ---------------------------------------------------------------- resource plan
def test_plan_of_a_small_run_is_feasible_and_reports_its_basis(fixture_dataset):
    imp, _ = fixture_dataset
    p = orchestrator.plan(cfg_for(imp))
    assert p["feasible"] and p["blockers"] == [] and p["device"] == "cpu" and p["seconds_basis"] in ("default estimate", "recorded runs") and p["train_images"] > 0
    assert p["config_sha256"] == cfg_for(imp).config_hash()


def test_plan_refuses_what_does_not_fit_with_reasons(fixture_dataset, monkeypatch):
    imp, _ = fixture_dataset
    monkeypatch.setenv("COSMO_TRAIN_RAM_FRACTION", "0.8")                                                 # the suite relaxes the guard; this test is about the guard
    monkeypatch.setattr(orchestrator, "available_mb", lambda: 300.0)
    p = orchestrator.plan(cfg_for(imp))
    assert not p["feasible"] and any("free memory" in b for b in p["blockers"])
    monkeypatch.undo()
    monkeypatch.setenv("COSMO_TRAIN_MAX_SECONDS", "1")
    p = orchestrator.plan(cfg_for(imp, epochs=100))
    assert not p["feasible"] and any("exceeds" in b for b in p["blockers"])
    assert all("exceeds" not in b for b in orchestrator.plan(cfg_for(imp, epochs=100, allow_long_run=True))["blockers"])               # the explicit override only lifts the duration limit
    monkeypatch.undo()
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10 * 10 ** 9, 9 * 10 ** 9, 5 * 10 ** 8))
    assert any("disk space" in b for b in orchestrator.plan(cfg_for(imp))["blockers"])


def test_plan_refuses_unknown_incomplete_and_altered_datasets(fixture_dataset, tmp_path):
    imp, root = fixture_dataset
    assert any("Unknown dataset version" in b for b in orchestrator.plan(cfg_for(imp, version_id="nope"))["blockers"])
    with db.context.as_system():
        row = db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (imp["version_id"],))
        mf = json.loads(row["manifest"])
        db.x("UPDATE dataset_versions SET manifest=? WHERE id=?", (json.dumps(dict(mf, complete=False)), imp["version_id"]))
    try:
        assert any("incomplete" in b for b in orchestrator.plan(cfg_for(imp))["blockers"])
        with pytest.raises(ValueError, match="incomplete"):
            trainer.train("lesion_classification_research", "ai-train-fixture", imp["version_id"], "admin1", epochs=1, image_size=64)
    finally:
        with db.context.as_system():
            db.x("UPDATE dataset_versions SET manifest=? WHERE id=?", (row["manifest"], imp["version_id"]))
    victim = root / "3.png"
    original = victim.read_bytes()
    victim.write_bytes(original + b"tamper")
    try:
        mf2 = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (imp["version_id"],))["manifest"])
        assert importer.verify_files(mf2, sample=None)["mismatched"] == ["3.png"]                      # a full check always finds it
        (root / "4.png").rename(root / "4.moved")
        assert importer.verify_files(mf2, sample=None)["missing"] == ["4.png"]
        (root / "4.moved").rename(root / "4.png")
    finally:
        victim.write_bytes(original)
    import types
    orig = importer.verify_files
    importer.verify_files = lambda *a, **k: {"ok": False, "missing": [], "mismatched": ["3.png"], "checked": 30}
    try:
        assert any("no longer matches its manifest" in b for b in orchestrator.plan(cfg_for(imp))["blockers"])         # the plan samples 30 files and refuses on any difference
    finally:
        importer.verify_files = orig


def test_a_requested_gpu_that_is_missing_blocks_and_an_unverified_one_warns(fixture_dataset, monkeypatch):
    imp, _ = fixture_dataset
    monkeypatch.setattr(resources, "_torch_accelerator", lambda: None)
    assert any("not available" in b for b in orchestrator.plan(cfg_for(imp, device="cuda"))["blockers"])
    monkeypatch.setattr(resources, "_torch_accelerator", lambda: ("cuda", "Fake GPU"))
    p = orchestrator.plan(cfg_for(imp, device="cuda"))
    assert p["device"] == "cuda" and p["device_verified"] is False and any("Unverified" in w for w in p["warnings"])
    assert trainer.pick_device() == "cpu"                                                              # a GPU that merely exists is not used


# ---------------------------------------------------------------- running
def test_a_run_is_recorded_and_the_next_estimate_uses_the_recorded_time(fixture_dataset):
    imp, _ = fixture_dataset
    res = orchestrator.run_training(cfg_for(imp), "admin1")
    assert res["status"] == "done" and res["model_id"]
    row = db.q1("SELECT * FROM training_runs WHERE id=?", (res["run_id"],))
    assert row["status"] == "done" and row["model_id"] == res["model_id"] and row["config_hash"] == cfg_for(imp).config_hash() and json.loads(row["plan"])["feasible"]
    assert json.loads(row["config"])["seed"] == 17 and row["finished_at"] >= row["started_at"] and row["device"] == "cpu"
    assert orchestrator.plan(cfg_for(imp))["seconds_basis"] == "recorded runs"
    assert res["card"]["task"] == "lesion_classification_research" and res["card"]["seed"] == 17
    assert any(r["id"] == res["run_id"] for r in orchestrator.list_runs())
    with db.context.as_system():
        db.x("DELETE FROM model_versions WHERE id=?", (res["model_id"],))


def test_only_one_run_at_a_time(fixture_dataset):
    imp, _ = fixture_dataset
    assert orchestrator._active.acquire(blocking=False)
    try:
        with pytest.raises(orchestrator.NotFeasible, match="already in progress"):
            orchestrator.run_training(cfg_for(imp), "admin1")
    finally:
        orchestrator._active.release()


def test_a_failed_run_is_recorded_without_leaking_text_and_leaves_no_partial_artifact(fixture_dataset, monkeypatch):
    imp, _ = fixture_dataset
    from app.config import MODEL_DIR
    d = MODEL_DIR / "lesion_classification_research"
    d.mkdir(parents=True, exist_ok=True)

    def boom(*a, **k):
        (d / "halfwritten0001.pt").write_bytes(b"partial")
        raise RuntimeError("CUDA out of memory token=sbp_abcdefghijklmnopqrstuvwxyz12345")
    monkeypatch.setattr(trainer, "train", boom)
    res = orchestrator.run_training(cfg_for(imp), "admin1")
    assert res["status"] == "failed" and "sbp_abcdefghijklmnopqrstuvwxyz12345" not in json.dumps(res)
    row = db.q1("SELECT status,error FROM training_runs WHERE id=?", (res["run_id"],))
    assert row["status"] == "failed" and "out of memory" in row["error"] and "sbp_abc" not in row["error"]
    assert not (d / "halfwritten0001.pt").exists()
    assert db.q1("SELECT 1 x FROM audit WHERE action='training.failed' AND target=?", (res["run_id"],))
    assert orchestrator._active.acquire(blocking=False)                                                # the lock was released
    orchestrator._active.release()


def test_a_stopped_run_is_recorded_as_stopped(fixture_dataset, monkeypatch):
    imp, _ = fixture_dataset

    def stop(*a, **k):
        raise trainer.Stopped()
    monkeypatch.setattr(trainer, "train", stop)
    res = orchestrator.run_training(cfg_for(imp), "admin1")
    assert res["status"] == "stopped" and db.q1("SELECT status FROM training_runs WHERE id=?", (res["run_id"],))["status"] == "stopped"


def test_runs_left_running_by_a_crashed_process_are_marked_interrupted():
    rid = db.uid()
    db.x("INSERT INTO training_runs(id,task,dataset_id,version_id,config,config_hash,plan,status,started_at,actor,device) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (rid, "t", "d", "v", "{}", "h", "{}", "running", db.now(), "admin1", "cpu"))
    assert orchestrator.mark_interrupted() >= 1
    r = db.q1("SELECT status,error FROM training_runs WHERE id=?", (rid,))
    assert r["status"] == "interrupted" and "resumed" in r["error"]


# ---------------------------------------------------------------- task specifications and readiness
def test_every_image_task_has_a_specification_and_segmentation_is_not_claimed():
    for t in tasks.TASKS:
        if t["id"] in ("image_suitability", "other_uncertain"):
            continue
        spec = task_specs.get(t["id"])
        assert spec is not None and spec.intended_task and spec.segmentation_supported is False, t["id"]
        assert spec.min_subjects >= 100 if t["id"] != "lesion_classification_research" else spec.min_subjects >= 200
    assert task_specs.get("lesion_classification_research").critical_classes == ("MEL", "BCC", "SCC")


def test_tasks_without_suitable_licensed_data_are_reported_blocked_with_reasons():
    acne = pipeline.readiness("acne_features")
    assert acne["status"] == "blocked" and any("licence gate" in b for b in acne["blockers"]) and acne["specification"]["intended_task"].startswith("Grade the visible severity")
    assert "segmentation" in acne and "masks" in acne["segmentation"]
    for t in ("pigmentation", "redness_irritation", "texture_scarring", "hair_thinning", "scalp_findings"):
        r = pipeline.readiness(t)
        assert r["status"] == "blocked" and any("No catalogued dataset is compatible" in b for b in r["blockers"]), t
    with pytest.raises(KeyError):
        pipeline.readiness("not_a_task")


def test_a_compatible_approved_imported_dataset_makes_a_task_ready(tmp_path):
    rec = dict(id="fx-acne", name="Fixture acne set", url="https://example.org", modality="facial photographs", annotations=["acne severity grade", "lesion counts"], intended_task="acne grading",
               approval_status="approved", terms_verified=True, terms_snapshot_hash="h" * 64, verified_spdx="CC-BY-4.0", claimed_license="CC BY 4.0", attribution="x", approved_uses=["model_training"])
    registry.save(rec)
    try:
        r = pipeline.readiness("acne_features")
        mine = next(c for c in r["candidates"] if c["dataset"] == "fx-acne")
        assert mine["compatible"] and mine["licence_gate_allowed"] and mine["versions"] == 0
        assert r["status"] == "blocked" and any("has not been imported" in b for b in r["blockers"])
        labels = make_images(tmp_path / "acne", 12, classes=("g0", "g1"))
        importer.ingest_folder("fx-acne", tmp_path / "acne", "admin1", labels)
        assert pipeline.readiness("acne_features")["status"] == "ready"
    finally:
        with db.context.as_system():
            db.x("DELETE FROM dataset_versions WHERE dataset_id='fx-acne'")
            db.x("DELETE FROM datasets WHERE id='fx-acne'")


def test_dataset_validation_reports_what_makes_a_run_meaningless():
    spec = task_specs.get("acne_features")
    entries = [{"file": f"{i}.png", "label": "a" if i % 2 else "b", "group_id": f"g{i // 2}", "fitzpatrick": "2.0", "sha256": str(i)} for i in range(60)]
    v = pipeline.validate_dataset(spec, {"entries": entries, "complete": True})
    assert not v["ok"] and any("fewer than 100 images" in p for p in v["problems"]) and any("distinct subjects" in p for p in v["problems"])
    big = [{"file": f"{i}.png", "label": "abcd"[i % 4], "group_id": f"g{i // 2}", "fitzpatrick": "2.0", "sha256": str(i)} for i in range(800)]
    ok = pipeline.validate_dataset(spec, {"entries": big, "complete": True})
    assert ok["ok"] and ok["split"]["subjects_per_split"]["train"] > 0 and any("Fitzpatrick IV-VI" in w for w in ok["warnings"])
    assert not pipeline.validate_dataset(spec, {"entries": big, "complete": False})["ok"]
    schema_task = task_specs.TaskSpec("x", "x", "classification", ("p", "q"), "photo", 2, 2, False, (), ())
    bad = pipeline.validate_dataset(schema_task, {"entries": [{"file": "a", "label": "zzz", "group_id": "g1", "sha256": "1"}, {"file": "b", "label": "p", "group_id": "g2", "sha256": "2"}] * 5, "complete": True})
    assert any("not in the task's label scheme" in p for p in bad["problems"])


def test_subject_leakage_between_splits_is_detected():
    with pytest.raises(pipeline.Blocked, match="leakage"):
        pipeline.verify_subject_split({"train": [{"group_id": "p1", "label": "a"}], "test": [{"group_id": "p1", "label": "a"}]})
    ok = pipeline.verify_subject_split({"train": [{"group_id": "p1", "label": "a"}], "test": [{"group_id": "p2", "label": "b"}]})
    assert ok["subjects_per_split"] == {"train": 1, "test": 1}


def test_run_task_blocks_without_the_gate_and_a_fixture_run_is_stamped_synthetic_and_cannot_pass_the_gates(fixture_dataset):
    imp, _ = fixture_dataset
    with pytest.raises(pipeline.Blocked):
        pipeline.run_task(cfg_for(imp, task_id="acne_features"), "admin1")                               # not in the registry, no gate: blocked
    res = pipeline.run_task(cfg_for(imp), "admin1", fixture=True)
    assert res["status"] == "done" and res["gates_passed"] is False and len(res["cards"]) == 2
    mid = res["model_id"]
    row = json.loads(db.q1("SELECT data FROM model_versions WHERE id=?", (mid,))["data"])
    assert row["evidence_class"] == "synthetic_fixture" and row["pipeline"]["fixture"] is True and any("SYNTHETIC FIXTURE" in x for x in row["limitations"])
    gates = {g["gate"]: g for g in trainer.evaluate_gates(row)}
    assert gates["licence_gate_passed"]["passed"] is False and gates["min_test_images"]["passed"] is False
    from app.config import MODEL_DIR
    md = (MODEL_DIR / "lesion_classification_research" / f"{mid}.model-card.md").read_text(encoding="utf-8")
    assert "RESEARCH MODEL" in md and "synthetic_fixture" in md and "[ ] licence_gate_passed" in md
    with db.context.as_system():
        db.x("DELETE FROM model_versions WHERE id=?", (mid,))


# ---------------------------------------------------------------- cards
def test_dataset_card_states_what_is_recorded_and_nothing_more(fixture_dataset):
    imp, root = fixture_dataset
    rec = registry.get("pad-ufes-20")
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (imp["version_id"],))["manifest"])
    c = cards.dataset_card(rec, mf)
    assert c["approval_status"] == "unreviewed" and c["license"]["terms_verified"] is False and c["license"]["verified_spdx"] == "not verified"
    assert c["version"]["images"] == 80 and c["version"]["subjects"] == 40 and set(c["version"]["class_distribution"]) == {"a", "b"} and c["version"]["manifest_sha256"]
    assert any("Fitzpatrick IV-VI make up 0.0 %" in w for w in c["representation_warnings"])
    md = cards.dataset_card_markdown(c)
    assert "not an approval" in md and str(root) not in md and "F:\\" not in md
    assert cards.dataset_card(registry.get("acne04"), None)["representation_warnings"] == ["Not imported: no counts, class balance or skin-tone distribution are known."]


def test_registry_normalises_older_records_and_validates_import_status():
    r = registry.get("acne04")
    assert r["import_status"] == "not_imported" and r["restrictions"] == [] and r["publisher"] is None
    registry.set_import_status("acne04", "failed", "network")
    assert registry.get("acne04")["import_status"] == "failed"
    registry.set_import_status("acne04", "not_imported")
    with pytest.raises(ValueError):
        registry.set_import_status("acne04", "finished-ish")


# ---------------------------------------------------------------- safe import
def test_class_mapping_excludes_unmapped_labels_and_cancelled_imports_are_incomplete(tmp_path, monkeypatch):
    root = tmp_path / "m"
    labels = make_images(root, 12, classes=("x", "y", "z"))
    r = importer.ingest_folder("map-test", root, "admin1", labels, class_mapping={"x": "mild", "y": "severe"})
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (r["version_id"],))["manifest"])
    assert {e["label"] for e in mf["entries"]} == {"mild", "severe"} and all(e["label_original"] in ("x", "y") for e in mf["entries"])
    assert len(mf["unmapped_labels_excluded"]) == 4 and r["unmapped_labels_excluded"] == 4 and mf["class_mapping"] == {"x": "mild", "y": "severe"} and mf["complete"] is True
    assert mf["labels_source"]["sha256"] and mf["importer"].startswith("importer-")
    real = importer.dhash
    seen = {"n": 0}

    def hooked(im):
        seen["n"] += 1
        if seen["n"] == 3:
            importer.cancel("cancel-test")
        return real(im)
    monkeypatch.setattr(importer, "dhash", hooked)
    r2 = importer.ingest_folder("cancel-test", root, "admin1", labels)
    assert r2["complete"] is False
    assert json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (r2["version_id"],))["manifest"])["complete"] is False
    with db.context.as_system():
        db.x("DELETE FROM dataset_versions WHERE dataset_id IN ('map-test','cancel-test')")


def test_archive_extraction_refuses_bombs_links_traversal_and_cleans_up(tmp_path):
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.png", b"\0" * (30 * 1024 * 1024))                                                  # compresses ~1000:1
    with pytest.raises(importer.ImportError_, match="compression ratio"):
        importer.safe_unzip(z, tmp_path / "out1")
    assert not list((tmp_path / "out1").rglob("*.png"))
    many = tmp_path / "many.zip"
    with zipfile.ZipFile(many, "w") as zf:
        for i in range(5):
            zf.writestr(f"{i}.png", b"x")
    with pytest.raises(importer.ImportError_, match="too many"):
        importer.safe_unzip(many, tmp_path / "out2", max_members=3)
    with pytest.raises(importer.ImportError_, match="size limit"):
        importer.safe_unzip(many, tmp_path / "out3", max_total=3)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("ok.png", b"fine")
        zf.writestr("../escape.png", b"bad")
    with pytest.raises(importer.ImportError_, match="Unsafe path"):
        importer.safe_unzip(evil, tmp_path / "out4")
    assert not (tmp_path / "out4" / "ok.png").exists() and not (tmp_path / "escape.png").exists()      # the file written before the failure was removed
    link = tmp_path / "link.zip"
    with zipfile.ZipFile(link, "w") as zf:
        info = zipfile.ZipInfo("l.png")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "target")
    with pytest.raises(importer.ImportError_, match="Symbolic link"):
        importer.safe_unzip(link, tmp_path / "out5")
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("sub/a.png", b"1"); zf.writestr("notes.exe", b"2")
    assert importer.safe_unzip(good, tmp_path / "out6") == 1 and (tmp_path / "out6" / "sub" / "a.png").exists() and not (tmp_path / "out6" / "notes.exe").exists()


def test_stale_partial_downloads_are_swept_but_recent_ones_are_kept(tmp_path):
    old, new = tmp_path / "a.bin.part", tmp_path / "b.bin.part"
    old.write_bytes(b"1"); new.write_bytes(b"2")
    t = os.stat(old).st_mtime - 3 * 86400
    os.utime(old, (t, t))
    assert importer.sweep_partials(tmp_path, older_than_s=86400) == ["a.bin.part"] and not old.exists() and new.exists()


# ---------------------------------------------------------------- admin API
def test_admin_endpoints_for_plans_readiness_cards_and_runs(client, fixture_dataset):
    imp, _ = fixture_dataset
    a, d, p = tok(client, "admin1"), tok(client, "doctor1"), tok(client, "patient1")
    body = {"task_id": "lesion_classification_research", "dataset_id": "ai-train-fixture", "version_id": imp["version_id"], "epochs": 1, "image_size": 64}
    for who in (d, p):
        assert client.post("/api/admin/train/plan", json=body, headers=who).status_code == 403
        assert client.get("/api/admin/training-runs", headers=who).status_code == 403
        assert client.get("/api/admin/tasks/acne_features/readiness", headers=who).status_code == 403
    plan = client.post("/api/admin/train/plan", json=body, headers=a).json()
    assert plan["feasible"] is True and plan["device"] == "cpu"
    assert client.get("/api/admin/tasks/acne_features/readiness", headers=a).json()["status"] == "blocked"
    assert client.get("/api/admin/tasks/nope/readiness", headers=a).status_code == 404
    card = client.get("/api/admin/datasets/pad-ufes-20/card", headers=a).json()
    assert card["id"] == "pad-ufes-20" and card["approval_status"] in ("unreviewed", "approved")
    assert client.get("/api/admin/datasets/pad-ufes-20/card?markdown=true", headers=a).text.startswith("# Dataset card")
    assert client.get("/api/admin/datasets/zzz/card", headers=a).status_code == 404
    assert client.get("/api/admin/models/zzz/card", headers=a).status_code == 404
    bad = dict(body, version_id="missing")
    r = client.post("/api/admin/train", json=dict(bad, dataset_id="pad-ufes-20", task_id="lesion_classification_research"), headers=a)
    assert r.status_code in (404, 409)
    assert client.post("/api/admin/datasets/cleanup-partials", headers=a).status_code == 200 and client.post("/api/admin/datasets/cleanup-partials", headers=d).status_code == 403

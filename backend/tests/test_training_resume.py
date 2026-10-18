"""Checkpointing and resume: real (tiny, CPU) training runs on a synthetic dataset. These prove the machinery, not model quality."""
import json

import cv2
import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from app import db
from app.datasets import importer
from app.main import app
from app.training import trainer

TASK = "lesion_classification_research"
EPOCHS = 4


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    db.init()
    root = tmp_path_factory.mktemp("resume")
    rows = ["filename,label,group_id,fitzpatrick,source"]
    rng = np.random.default_rng(5)
    for i in range(60):
        cls = "a" if i % 2 == 0 else "b"
        img = np.zeros((96, 96, 3), np.uint8)
        img[:] = (30, 30, 200) if cls == "a" else (200, 60, 30)
        img = np.clip(img + rng.integers(0, 60, img.shape), 0, 255).astype(np.uint8)
        cv2.imwrite(str(root / f"{i}.png"), img)
        rows.append(f"{i}.png,{cls},g{i // 2},{(i % 3) + 1},synthetic")
    (root / "l.csv").write_text("\n".join(rows))
    return importer.ingest_folder("resume-ds", root, "tester", root / "l.csv")["version_id"]


def run(ds, ckdir, **kw):
    args = dict(epochs=EPOCHS, batch=8, image_size=96, finetune="head", checkpoint_dir=ckdir)
    args.update(kw)
    return trainer.train(TASK, "resume-ds", ds, "tester", **args)


def stop_after_epoch(n_epochs, steps_per_epoch):
    """should_stop callback that fires in the middle of epoch n_epochs+1 (after the checkpoint of epoch n_epochs exists)."""
    calls = {"n": 0}

    def f():
        calls["n"] += 1
        return calls["n"] > n_epochs * steps_per_epoch + 1
    return f


def steps_per_epoch(ds, batch=8):
    mf = json.loads(db.q1("SELECT manifest FROM dataset_versions WHERE id=?", (ds,))["manifest"])
    sp = trainer.stratified_group_split([e for e in mf["entries"] if e["label"]], 17)
    return (len(sp["train"]) + batch - 1) // batch


def only(ckdir):
    files = list(ckdir.glob("*.json"))
    assert len(files) == 1, files
    return files[0], json.loads(files[0].read_text())


def test_a_completed_run_leaves_no_checkpoint_and_records_no_resume(ds, tmp_path):
    card = run(ds, tmp_path)
    assert card["resumed"] is None and list(tmp_path.iterdir()) == []


def test_stopping_keeps_the_last_completed_epoch_and_resuming_continues_it(ds, tmp_path):
    spe = steps_per_epoch(ds)
    with pytest.raises(trainer.Stopped):
        run(ds, tmp_path, should_stop=stop_after_epoch(2, spe))
    meta_path, meta = only(tmp_path)
    assert meta["epoch"] == 2 and meta["epochs"] == EPOCHS and len(meta["sha256"]) == 64
    events = []
    card = run(ds, tmp_path, resume=True, progress=events.append)
    assert card["resumed"]["from_epoch"] == 2 and [h["epoch"] for h in card["history"]] == [1, 2, 3, 4]
    assert any("resumed from checkpoint after epoch 2/4" in e.get("log", "") for e in events)
    assert list(tmp_path.glob("*")) == []                                                  # discarded after the successful finish


def test_resumed_training_matches_an_uninterrupted_run(ds, tmp_path):
    torch.set_num_threads(1)
    full = run(ds, tmp_path / "full")
    spe = steps_per_epoch(ds)
    with pytest.raises(trainer.Stopped):
        run(ds, tmp_path / "part", should_stop=stop_after_epoch(2, spe))
    res = run(ds, tmp_path / "part", resume=True)
    a = torch.load(full["artifact_path"], weights_only=True)
    b = torch.load(res["artifact_path"], weights_only=True)
    worst = max(float((a[k].float() - b[k].float()).abs().max()) for k in a)
    # CPU float math with identical seeds and restored RNG/optimizer/scheduler state: expect (near-)identical weights; the tolerance is recorded, not assumed
    assert worst < 1e-4, worst
    assert [h["val_macro_f1"] for h in full["history"]] == [h["val_macro_f1"] for h in res["history"]]
    assert full["test_metrics"]["accuracy"] == res["test_metrics"]["accuracy"] and full["split_hash"] == res["split_hash"]


def test_resume_without_a_matching_checkpoint_starts_fresh_and_says_so(ds, tmp_path):
    events = []
    card = run(ds, tmp_path, resume=True, progress=events.append)
    assert card["resumed"] is None and any("no matching checkpoint" in e.get("log", "") for e in events)
    assert [h["epoch"] for h in card["history"]] == [1, 2, 3, 4]


def test_a_changed_configuration_can_never_resume_someone_elses_checkpoint(ds, tmp_path):
    spe = steps_per_epoch(ds)
    with pytest.raises(trainer.Stopped):
        run(ds, tmp_path, should_stop=stop_after_epoch(1, spe))
    _, meta = only(tmp_path)
    for change in ({"lr": 5e-4}, {"epochs": 3}, {"seed": 99}, {"batch": 16}, {"finetune": "layer4"}):
        card = run(ds, tmp_path, resume=True, **change)
        assert card["resumed"] is None, change                                             # different fingerprint: the stored checkpoint is not touched
        assert (tmp_path / f"{meta['fingerprint']}.pt").is_file(), change


def test_resume_false_ignores_an_existing_checkpoint(ds, tmp_path):
    spe = steps_per_epoch(ds)
    with pytest.raises(trainer.Stopped):
        run(ds, tmp_path, should_stop=stop_after_epoch(2, spe))
    card = run(ds, tmp_path, resume=False)
    assert card["resumed"] is None and len(card["history"]) == EPOCHS


def test_a_tampered_or_truncated_checkpoint_is_refused_not_loaded(ds, tmp_path):
    spe = steps_per_epoch(ds)
    with pytest.raises(trainer.Stopped):
        run(ds, tmp_path, should_stop=stop_after_epoch(2, spe))
    meta_path, meta = only(tmp_path)
    pt = tmp_path / f"{meta['fingerprint']}.pt"
    data = bytearray(pt.read_bytes()); data[len(data) // 2] ^= 1
    pt.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="integrity"):
        run(ds, tmp_path, resume=True)
    pt.write_bytes(bytes(data[: len(data) // 2]))
    with pytest.raises(ValueError, match="integrity"):
        run(ds, tmp_path, resume=True)
    meta_path.write_text(json.dumps({**meta, "fingerprint": "someone-else"}))
    with pytest.raises(ValueError, match="integrity"):
        run(ds, tmp_path, resume=True)


def test_checkpoints_are_listed_for_administrators_only(ds, tmp_path, monkeypatch):
    monkeypatch.setattr(trainer, "CHECKPOINT_DIR", tmp_path)
    spe = steps_per_epoch(ds)
    with pytest.raises(trainer.Stopped):
        trainer.train(TASK, "resume-ds", ds, "tester", epochs=EPOCHS, batch=8, image_size=96, finetune="head", should_stop=stop_after_epoch(1, spe))
    with TestClient(app) as c:
        def tok(u): return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}
        rows = c.get("/api/admin/train/checkpoints", headers=tok("admin1")).json()
        assert len(rows) == 1 and rows[0]["epoch"] == 1 and rows[0]["dataset"] == "resume-ds" and "state" not in rows[0]
        assert c.get("/api/admin/train/checkpoints", headers=tok("doctor1")).status_code == 403
        assert c.get("/api/admin/train/checkpoints", headers=tok("patient1")).status_code == 403
        bad = c.post("/api/admin/train", json={"task_id": TASK, "dataset_id": "resume-ds", "version_id": ds, "resume": "maybe"}, headers=tok("admin1"))
        assert bad.status_code == 422

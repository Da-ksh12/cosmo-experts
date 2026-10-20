"""Phase 5 data acquisition and licence governance: label maps, selection rules, verified downloads, and recorded licence decisions. No network is used: HTTP is replaced by a fake."""
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.datasets import governance, registry, sources as S
from app.main import app
from app.training import pipeline, task_specs, tasks, trainer


@pytest.fixture(scope="module", autouse=True)
def client():
    with TestClient(app):
        yield
    with db.context.as_system():
        db.x("DELETE FROM datasets WHERE id LIKE 'p5test-%'")


def rec(i, dx3, **kw):
    base = dict(id=f"ISIC_{i:07d}", license="CC-BY", attribution="Hospital A", dx1="Benign", dx3=dx3, confirm="histopathology", patient=f"IP_{i // 3}", lesion=f"IL_{i}", fst="II", sex="male", age=50,
                site="Trunk", itype="dermoscopic", full={"url": "https://x/f.jpg", "size": 9}, thumb={"url": "https://x/t.jpg", "size": 5}, px=[10, 10])
    base.update(kw)
    return base


# ---------------------------------------------------------------- label maps and selection
def test_dermoscopy_classes_map_only_what_is_listed_and_count_the_rest():
    recs = [rec(1, "Nevus"), rec(2, "Melanoma in situ"), rec(3, "Melanoma Invasive"), rec(4, "Solar or actinic keratosis"), rec(5, "Seborrheic keratosis"), rec(6, "Hemangioma"),
            rec(7, "Verruca"), rec(8, None), rec(9, "Squamous cell carcinoma in situ")]
    sel, rep = S.isic_select(recs, "dermoscopy")
    assert {r["id"]: r["label"] for r in sel} == {"ISIC_0000001": "NV", "ISIC_0000002": "MEL", "ISIC_0000003": "MEL", "ISIC_0000004": "AK", "ISIC_0000005": "BKL", "ISIC_0000006": "VASC", "ISIC_0000009": "SCC"}
    assert rep["excluded_unmapped_diagnosis"] == {"Verruca": 1, "None": 1} and rep["selected"] == 7


def test_clinical_selection_leaves_out_pad_ufes_20s_own_images_and_uses_pad_class_names():
    recs = [rec(1, "Basal cell carcinoma", attribution="Federal University of Espírito Santo (UFES)"), rec(2, "Basal cell carcinoma", attribution="Hospital Italiano de Buenos Aires"),
            rec(3, "Solar or actinic keratosis", attribution="Federal University of Espírito Santo (UFES)"), rec(4, "Melanoma, NOS", attribution="Memorial Sloan Kettering Cancer Center")]
    sel, rep = S.isic_select(recs, "clinical")
    assert [(r["id"], r["label"]) for r in sel] == [("ISIC_0000002", "BCC"), ("ISIC_0000004", "MEL")]
    assert rep["excluded_ufes_pad_ufes_20"] == 2


def test_selection_is_deterministic_caps_classes_and_prefers_images_whose_patient_is_known():
    recs = [rec(i, "Nevus", patient=None if i % 2 else f"IP_{i}", lesion=None if i % 2 else f"IL_{i}") for i in range(1, 41)]
    a, ra = S.isic_select(recs, "dermoscopy", {"NV": 10}, seed=3)
    b, _ = S.isic_select(list(reversed(recs)), "dermoscopy", {"NV": 10}, seed=3)
    c, _ = S.isic_select(recs, "dermoscopy", {"NV": 10}, seed=4)
    assert [r["id"] for r in a] == [r["id"] for r in b] and [r["id"] for r in a] != [r["id"] for r in c]
    assert len(a) == 10 and all(r["group_known"] for r in a) and ra["capped"] == {"NV": {"available": 40, "kept": 10}}


def test_a_missing_identifier_becomes_its_own_group_and_fitzpatrick_becomes_a_number():
    sel, _ = S.isic_select([rec(1, "Nevus", patient=None, lesion=None, fst="V"), rec(2, "Nevus", patient="IP_9", fst=None)], "dermoscopy")
    assert sel[0]["group_id"] == "ISIC_0000001" and sel[0]["group_known"] is False and sel[0]["fst_num"] == 5
    assert sel[1]["group_id"] == "IP_9" and sel[1]["fst_num"] is None


def test_derm12345_codes_map_to_the_dermoscopy_classes_and_unlisted_codes_are_excluded():
    row = lambda label, main: {"label": label, "main_class_1": main}                        # noqa: E731
    assert [S.derm_class(row(c, m)) for c, m in (("jd", "dysplastic"), ("jb", "banal"), ("mel", "melanoma"), ("lm", "melanoma"), ("bcc", "keratinocytic"), ("bd", "keratinocytic"), ("scc", "keratinocytic"),
                                                 ("sk", "keratinocytic"), ("ak", "keratinocytic"), ("ha", "vascular"), ("df", "fibro_histiocytic"))] == ["NV", "NV", "MEL", "MEL", "BCC", "SCC", "SCC", "BKL", "AK", "VASC", "DF"]
    assert all(S.derm_class(row(c, m)) is None for c, m in (("ch", "keratinocytic"), ("mpd", "keratinocytic"), ("dfsp", "fibro_histiocytic"), ("ks", "vascular"), ("ls", "lentigo"), ("isl", "lentigo")))


def test_scin_cases_need_a_leading_condition_with_enough_weight_and_enough_cases():
    cases, labels = [], {}
    def case(i, weights, fsts=("FST2", "FST2", "FST3"), imgs=2):
        cid = f"c{i}"
        cases.append({"case_id": cid, **{f"image_{k}_path": (f"dataset/images/{cid}_{k}.png" if k <= imgs else "") for k in (1, 2, 3)}, **{f"image_{k}_shot_type": "CLOSE_UP" for k in (1, 2, 3)}})
        labels[cid] = {"weighted_skin_condition_label": str(weights) if weights else "", "monk_skin_tone_label_us": "3",
                       **{f"dermatologist_fitzpatrick_skin_type_label_{k}": f for k, f in zip((1, 2, 3), fsts)}}
    for i in range(45):
        case(i, {"Eczema": 0.6, "Psoriasis": 0.4})
    for i in range(45, 50):
        case(i, {"Rare thing": 0.9})                                 # strong, but only 5 cases: not a class
    for i in range(50, 60):
        case(i, {"Eczema": 0.4, "Tinea": 0.35, "Urticaria": 0.25})   # leading weight below 0.5
    case(60, None)
    rows, rep = S.scin_select(cases, labels, min_weight=0.5, min_cases=40)
    assert {r["label"] for r in rows} == {"Eczema"} and len(rows) == 90 and rep["qualifying_cases"] == 45
    assert rep["conditions_excluded_too_few_cases"] == {"Rare thing": 5} and rep["leading_weight_below_threshold"] == 10 and rep["no_weighted_label"] == 1
    assert {r["fst"] for r in rows} == {2} and {r["monk"] for r in rows} == {3}


# ---------------------------------------------------------------- verified downloads
class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, etag: str | None = None):
        super().__init__(body)
        self.headers = {"ETag": etag} if etag else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_http(monkeypatch):
    calls = []
    store = {}

    def request(url, headers=None):
        return url
    def opener_open(req, timeout=0):
        calls.append(req)
        body, etag = store[req]
        return FakeResponse(body, etag)
    monkeypatch.setattr(S, "_request", request)
    monkeypatch.setattr(S._OPENER, "open", opener_open)
    return store, calls


def test_a_download_is_checked_against_the_servers_checksum_and_a_bad_one_is_discarded(tmp_path, fake_http):
    import hashlib
    store, calls = fake_http
    body = b"x" * 1000
    store["u1"] = (body, '"' + hashlib.md5(body).hexdigest() + '"')
    assert S.fetch_file("u1", tmp_path / "a.jpg", etag_check=True) == "downloaded" and (tmp_path / "a.jpg").read_bytes() == body
    assert S.fetch_file("u1", tmp_path / "a.jpg", etag_check=True) == "cached" and len(calls) == 1       # a completed file is not fetched again: an interrupted run resumes
    store["u2"] = (body, '"' + "0" * 32 + '"')
    with pytest.raises(S.SourceError, match="checksum"):
        S.fetch_file("u2", tmp_path / "b.jpg", etag_check=True)
    assert not (tmp_path / "b.jpg").exists() and not (tmp_path / "b.jpg.part").exists()
    store["u3"] = (body, '"abc-3"')                                                                    # a multipart ETag is not an MD5: it is not used as one
    assert S.fetch_file("u3", tmp_path / "c.jpg", etag_check=True) == "downloaded"


def test_expected_size_and_md5_are_enforced_when_the_source_publishes_them(tmp_path, fake_http):
    import hashlib
    store, _ = fake_http
    body = b"y" * 64
    store["u"] = (body, None)
    with pytest.raises(S.SourceError, match="Size mismatch"):
        S.fetch_file("u", tmp_path / "s.bin", size=65)
    with pytest.raises(S.SourceError, match="Checksum mismatch"):
        S.fetch_file("u", tmp_path / "m.bin", md5_hex="1" * 32)
    assert S.fetch_file("u", tmp_path / "ok.bin", size=64, md5_hex=hashlib.md5(body).hexdigest()) == "downloaded"


def test_fetch_many_reports_failures_instead_of_hiding_them(tmp_path, fake_http):
    store, _ = fake_http
    store["good"] = (b"1", None)
    res = S.fetch_many([{"url": "good", "dest": tmp_path / "g"}, {"url": "missing", "dest": tmp_path / "m"}], workers=2)
    assert res["downloaded"] == 1 and len(res["failed"]) == 1 and res["failed"][0]["url"] == "missing"


def test_the_isic_listing_refuses_any_record_outside_the_eligible_licences_and_resumes(tmp_path, monkeypatch):
    page = lambda lic, nxt: {"count": 4, "next": nxt, "results": [{"isic_id": f"ISIC_{lic}{n}", "copyright_license": lic, "attribution": "x", "files": {"full": {"url": "u", "size": 1}, "thumbnail_256": {"url": "u", "size": 1}},   # noqa: E731
                                                                  "metadata": {"clinical": {"diagnosis_3": "Nevus"}, "acquisition": {}}} for n in range(2)]}
    monkeypatch.setattr(S, "get_json", lambda url, timeout=90: page("CC-BY-NC", None))
    with pytest.raises(S.SourceError, match="outside the eligible licences"):
        S.isic_list("dermoscopy", tmp_path / "l.jsonl")
    (tmp_path / "l.jsonl").unlink(missing_ok=True); (tmp_path / "l.cursor").unlink(missing_ok=True)
    pages = {S.ISIC_API + "images/search/?" + S.urllib.parse.urlencode({"query": S.ISIC_QUERIES["dermoscopy"], "limit": 100}): page("CC-0", S.ISIC_API + "page2"), S.ISIC_API + "page2": page("CC-BY", None)}
    monkeypatch.setattr(S, "get_json", lambda url, timeout=90: pages[url])
    got = S.isic_list("dermoscopy", tmp_path / "l.jsonl")
    assert len(got) == 4 and (tmp_path / "l.cursor").read_text() == "DONE"
    monkeypatch.setattr(S, "get_json", lambda url, timeout=90: (_ for _ in ()).throw(AssertionError("a finished listing must not be fetched again")))
    assert len(S.isic_list("dermoscopy", tmp_path / "l.jsonl")) == 4


def test_the_isic_queries_never_select_the_non_commercial_pool_and_do_not_require_optional_fields():
    for q in S.ISIC_QUERIES.values():
        assert "CC-BY-NC" not in q and 'copyright_license:"CC-0"' in q and 'copyright_license:"CC-BY"' in q
        assert 'image_manipulation:"instrument only"' not in q                                       # that filter silently drops every record without the field (it hid all 2,298 PAD-UFES-20 copies)


# ---------------------------------------------------------------- licence governance
def new_dataset(i, **kw):
    d = dict(id=f"p5test-{i}", name="Test", url="https://example.org", maintainer="x", description="x", intended_task="x", modality="clinical photographs", annotations=["lesion diagnosis"], populations="x",
             claimed_license="verify", attribution="x", download_method="x", version="1", limitations="x", terms_verified=False, terms_snapshot_hash="", terms_checked_at=None, permission_document="",
             commercial_eligibility="unknown", approved_uses=[], local_path="", file_hashes={})
    d.update(kw)
    db.x("INSERT INTO datasets VALUES(?,?,?,?)", (d["id"], json.dumps(d), "unreviewed", db.now()))
    return d["id"]


EVID = [{"label": "terms", "url": "https://example.org/terms", "sha256": "ab" * 32, "bytes": 10, "excerpt": "licence text"}]


def test_a_licence_decision_is_recorded_with_its_evidence_and_the_gate_then_allows_training():
    did = new_dataset("a")
    r = governance.record(did, "phase5-agent", approve=True, spdx="CC-BY-4.0", claimed_license="CC BY 4.0", permission_document="", notes="n", evidence=EVID)
    assert r["approval_status"] == "approved" and r["terms_verified"] and r["verified_by"] == "phase5-agent" and r["evidence"][0]["sha256"] == "ab" * 32
    assert registry.license_gate(r, "model_training", commercial=True)["allowed"]
    acts = [a["action"] for a in db.q("SELECT action FROM audit WHERE target=?", (did,))]
    assert {"dataset.fetch_terms", "dataset.verify_terms", "dataset.approve"} <= set(acts)


def test_the_gate_cannot_be_talked_into_an_approval_it_would_refuse():
    did = new_dataset("b")
    with pytest.raises(governance.GateRefused) as e:
        governance.record(did, "phase5-agent", approve=True, spdx="CC-BY-NC-4.0", claimed_license="CC BY-NC 4.0", permission_document="", notes="n", evidence=EVID)
    assert any("Commercial use is not confirmed" in r for r in e.value.reasons)
    assert registry.get(did)["approval_status"] != "approved" or not registry.license_gate(registry.get(did), "model_training", commercial=True)["allowed"]


def test_a_refusal_is_a_recorded_decision_that_blocks_training():
    did = new_dataset("c")
    r = governance.record(did, "phase5-agent", approve=False, spdx="CC-BY-NC-4.0", claimed_license="CC BY-NC 4.0", permission_document="x", notes="non-commercial", evidence=EVID)
    assert r["approval_status"] == "rejected" and r["approved_uses"] == [] and r["permission_document"] == ""
    assert not registry.license_gate(r, "model_training", commercial=True)["allowed"]


def test_custom_licences_pass_only_through_a_written_permission_statement():
    did = new_dataset("d")
    with pytest.raises(governance.GateRefused):
        governance.record(did, "phase5-agent", approve=True, spdx="LicenseRef-Custom", claimed_license="custom", permission_document="", notes="n", evidence=EVID)
    r = governance.record(did, "phase5-agent", approve=True, spdx="LicenseRef-Custom", claimed_license="custom", permission_document="The licence grants commercial use; sha256 ...", notes="n", evidence=EVID)
    assert registry.license_gate(r, "model_training", commercial=True)["allowed"]


# ---------------------------------------------------------------- registries
def test_new_tasks_and_datasets_are_registered_and_the_releases_gates_still_apply_to_them():
    for t in ("dermoscopy_lesion_research", "skin_condition_research"):
        assert tasks.get_task(t)["status"]["status"] in ("research_only",) and task_specs.get(t) is not None
    ids = {s["id"] for s in registry.SEED}
    assert {"isic-dermoscopy", "isic-clinical", "scin", "derm12345"} <= ids
    card = {"task": "skin_condition_research", "test_metrics": {"n": 500, "macro_f1": 0.5, "macro_f1_ci95": [0.4, 0.6], "ece": 0.05, "robustness": {"x": 1}, "subgroups_by_fitzpatrick": {"4-6": {"n": 60}}},
            "baselines": {"b": {"macro_f1": 0.3}}, "near_duplicates_vs_train": {"test": 0}, "dataset_license": {"spdx": "X", "terms_snapshot_hash": "h"}}
    names = [g["gate"] for g in trainer.evaluate_gates(card)]
    assert "dark_skin_evaluated" in names and "ood_detection" in names and "critical_class_sensitivity" not in names     # no class singled out, but skin tone and unsupported images still count
    card["task"] = "dermoscopy_lesion_research"
    assert "critical_class_sensitivity" in [g["gate"] for g in trainer.evaluate_gates(card)]
    assert registry.compatibility(registry.SEED[[s["id"] for s in registry.SEED].index("scin")] | {"id": "scin"}, tasks.get_task("skin_condition_research"))["compatible"]


def test_the_single_dataset_pipeline_refuses_images_without_a_patient_or_lesion_identifier():
    """The older admin route splits by group_id; an image with no real identifier is its own group, so one patient could end up in train and test. The refusal points to the Phase 5 pipeline."""
    spec = task_specs.get("dermoscopy_lesion_research")
    entries = [{"label": ("MEL" if i % 2 else "NV"), "group_id": f"g{i // 2}", "label_provenance": ("isic:CC-0:histopathology:nogroup" if i % 10 == 0 else "isic:CC-0:histopathology:group")} for i in range(400)]
    out = pipeline.validate_dataset(spec, {"entries": entries, "complete": True})
    assert not out["ok"] and any("no patient or lesion identifier" in p and "Phase 5" in p for p in out["problems"])
    for e in entries:
        e["label_provenance"] = "isic:CC-0:histopathology:group"
    assert not any("identifier" in p for p in pipeline.validate_dataset(spec, {"entries": entries, "complete": True})["problems"])

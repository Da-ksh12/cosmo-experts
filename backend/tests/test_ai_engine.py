"""Unified AI engine: capability registry, routing rules, bounded resources, safety review and provenance. Fake handlers are used only to test the engine's own rules; the real
capabilities are exercised in their own test files."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.ai import registry, safety
from app.ai.engine import AIEngine, HandlerContext, HandlerResult
from app.ai.errors import RejectedInput, ResourceExhausted
from app.ai.resources import CancelToken, ModelCache, ResourceManager, select_device
from app.main import app

PATIENT = {"id": "patient1", "name": "P", "role": "patient"}
DOCTOR = {"id": "doctor1", "name": "D", "role": "doctor"}
ADMIN = {"id": "admin1", "name": "A", "role": "admin"}


@pytest.fixture(scope="module", autouse=True)
def _app():
    with TestClient(app):
        yield


@pytest.fixture(autouse=True)
def _clean():
    registry.invalidate()
    yield
    for k in ("tesseract", "ocr_lang:hin", "ocr_lang:eng", "approved_kb", "llm_backend"):
        registry.set_override(k, None)
    with db.context.as_system():
        db.x("DELETE FROM model_versions WHERE id LIKE 'aitest-%'")
        db.x("DELETE FROM settings WHERE key LIKE 'active_model:%'")
    registry.invalidate()


def engine(max_conc=2, max_queue=4):
    return AIEngine(ResourceManager(max_concurrency=max_conc, max_queue=max_queue))


def ok_handler(result=None, **kw):
    return lambda payload, ctx: HandlerResult("ok", result if result is not None else {"echo": True}, **kw)


def add_model(mid, task, status="research_only", arch="resnet18"):
    card = {"model_id": mid, "task": task, "architecture": arch, "artifact_sha256": "ab" * 32, "test_metrics": {}, "baselines": {}, "dataset_license": {}}
    with db.context.as_system():
        db.x("INSERT INTO model_versions VALUES(?,?,?,?,?)", (mid, task, json.dumps(card), status, db.now()))


def activate(task, mid):
    with db.context.as_system():
        db.set_setting(f"active_model:{task}", mid)


# ---------------------------------------------------------------- registry
def test_image_capabilities_come_from_the_single_task_registry():
    from app.training import tasks
    ids = {d.id for d in registry.DEFS}
    assert "image.quality" in ids and "image.image_suitability" not in ids
    for t in tasks.TASKS:
        if t["id"] != "image_suitability":
            assert f"image.{t['id']}" in ids, t["id"]


def test_tasks_without_a_validated_model_resolve_unavailable_and_never_patient_facing():
    for cid in ("image.acne_features", "image.pigmentation", "image.redness_irritation", "image.texture_scarring", "image.hair_thinning", "image.scalp_findings",
                "image.other_uncertain", "translation.text", "llm.generate"):
        s = registry.resolve(registry.get(cid))
        assert s.availability == "unavailable" and s.approval == "none" and not s.patient_facing and s.status == "unavailable", cid
        assert s.reasons, cid


def test_a_registered_research_model_is_research_only_and_administrator_only():
    add_model("aitest-r1", "lesion_classification_research")
    s = registry.resolve(registry.get("image.lesion_classification_research"))
    assert s.availability == "available" and s.approval == "research_only" and s.status == "research_only"
    assert s.audiences == ["admin"] and not s.patient_facing and s.model.model_id == "aitest-r1" and s.model.lifecycle in ("experimental", "evaluation_failed")
    assert s.model.artifact_sha256_prefix == "ab" * 8


def test_an_approved_but_not_active_model_is_never_silently_used():
    add_model("aitest-a1", "acne_features", status="approved_for_defined_use")
    s = registry.resolve(registry.get("image.acne_features"))
    assert s.availability == "unavailable" and s.model is None                                           # approved is not the same as active
    activate("acne_features", "aitest-a1")
    s = registry.resolve(registry.get("image.acne_features"))
    assert s.availability == "available" and s.approval == "approved_for_defined_use" and s.patient_facing and s.model.lifecycle == "active"


def test_the_active_pointer_cannot_promote_a_research_model():
    add_model("aitest-r2", "acne_features", status="research_only")
    activate("acne_features", "aitest-r2")
    s = registry.resolve(registry.get("image.acne_features"))
    assert s.approval == "research_only" and not s.patient_facing and s.model.model_id == "aitest-r2"


def test_research_only_tasks_never_reach_patients_even_if_marked_approved():
    add_model("aitest-a2", "lesion_classification_research", status="approved_for_defined_use")
    activate("lesion_classification_research", "aitest-a2")
    s = registry.resolve(registry.get("image.lesion_classification_research"))
    assert "patient" not in s.audiences and not s.patient_facing                                          # intended use: research benchmarking only


def test_dependencies_decide_availability_and_are_reported_without_paths():
    registry.set_override("tesseract", (True, "ok")); registry.set_override("ocr_lang:hin", (False, "OCR language pack 'hin' is not installed"))
    s = registry.resolve(registry.get("document.ocr.hindi"))
    assert s.availability == "unavailable" and any("hin" in r for r in s.reasons)
    registry.set_override("ocr_lang:hin", (True, "installed"))
    s = registry.resolve(registry.get("document.ocr.hindi"))
    assert s.availability == "available" and s.approval == "research_only" and not s.patient_facing    # present is not validated
    registry.set_override("tesseract", (False, "Tesseract OCR is not installed"))
    s = registry.resolve(registry.get("document.extract"))
    assert s.availability == "degraded" and s.reasons                                                    # digital PDFs still work; scans do not
    assert all("\\" not in d.detail and "/usr" not in d.detail for d in registry.resolve(registry.get("document.extract")).dependencies)


def test_deterministic_helpers_are_patient_facing_and_clinician_review_is_marked():
    q = registry.resolve(registry.get("image.quality"))
    assert q.patient_facing and q.approval == "not_required" and q.status == "available"
    e = registry.resolve(registry.get("document.extract"))
    assert e.approval == "review_required" and e.status == "clinician_review_required"


# ---------------------------------------------------------------- engine rules
def test_unknown_and_unavailable_capabilities_answer_honestly_and_never_run_a_handler():
    e = engine()
    called = []
    r = e.run("image.nonexistent", {}, user=PATIENT)
    assert r.status == "unsupported" and r.message == safety.UNSUPPORTED_MSG and r.result is None
    e.register("image.acne_features", lambda p, c: called.append(1) or HandlerResult("ok", {"severity": "mild"}))
    r = e.run("image.acne_features", {}, user=ADMIN)
    assert r.status == "unavailable" and r.result is None and called == []                               # no model: the handler is not even invoked
    assert "unavailable" in r.labels and r.state["availability"] == "unavailable"
    r2 = e.run("translation.text", {}, user=PATIENT)
    assert r2.status == "unavailable" and r2.result is None


def test_engine_has_no_handler_for_an_available_capability_means_unavailable_not_a_fake_result():
    e = engine()
    r = e.run("image.quality", {"data": b"x"}, user=PATIENT)
    assert r.status == "unavailable" and r.result is None


def test_audience_and_research_gating():
    e = engine()
    e.register("image.lesion_classification_research", ok_handler({"top": []}))
    e.register("image.quality", ok_handler())
    add_model("aitest-r3", "lesion_classification_research")
    for who in (PATIENT, DOCTOR):
        assert e.run("image.lesion_classification_research", {}, user=who, allow_research=True).status == "forbidden"
    assert e.run("image.lesion_classification_research", {}, user=ADMIN).status == "forbidden"            # the route must ask for research explicitly
    r = e.run("image.lesion_classification_research", {}, user=ADMIN, allow_research=True)
    assert r.status == "ok" and "research_only" in r.labels and r.result["research_only"] is True
    assert r.result["interpretation"] == safety.EXPERIMENTAL_NOTE and r.provenance.model.model_id == "aitest-r3" and r.safety.clinician_review
    assert e.run("image.quality", {}, user=PATIENT).status == "ok"


def test_patients_never_receive_internal_reasons_while_staff_do():
    e = engine()
    p = e.run("image.acne_features", {}, user=PATIENT)
    s = e.run("image.acne_features", {}, user=DOCTOR)
    assert "reasons" not in p.state and s.state["reasons"]
    caps = {c.id: c for c in e.capabilities(PATIENT)}
    assert "image.lesion_classification_research" not in caps and "llm.generate" not in caps and not hasattr(caps["image.quality"], "reasons")
    full = {c.id: c for c in e.capabilities(ADMIN)}
    assert "image.lesion_classification_research" in full and full["image.quality"].availability in ("available", "degraded")


def test_handler_failures_become_error_envelopes_with_no_output_and_no_leaked_text(caplog):
    e = engine()
    e.register("image.quality", lambda p, c: (_ for _ in ()).throw(RuntimeError("secret db password=hunter2 at /srv/x.py")))
    r = e.run("image.quality", {}, user=PATIENT)
    assert r.status == "error" and r.result is None and "hunter2" not in r.model_dump_json() and "/srv" not in r.message
    assert "hunter2" not in caplog.text
    e.register("image.quality", lambda p, c: None)
    assert e.run("image.quality", {}, user=PATIENT).status == "error"                                       # None is not a result
    e.register("image.quality", lambda p, c: {"verdict": "accepted"})
    assert e.run("image.quality", {}, user=PATIENT).status == "error"                                       # neither is a bare dict


def test_validators_reject_bad_input_before_any_work():
    e = engine()
    ran = []

    def validator(payload):
        if not payload.get("data"):
            raise RejectedInput("The image is empty.")
    e.register("image.quality", lambda p, c: ran.append(1) or HandlerResult("ok", {}), validator)
    r = e.run("image.quality", {"data": b""}, user=PATIENT)
    assert r.status == "rejected_input" and r.message == "The image is empty." and ran == []


def test_a_timeout_answers_at_once_cancels_the_run_and_frees_the_slot():
    e = engine(max_conc=1, max_queue=0)
    stopped = []

    def slow(payload, ctx: HandlerContext):
        for _ in range(200):
            ctx.check()                                                                                  # cooperative cancellation point
            time.sleep(0.02)
        return HandlerResult("ok", {})

    def wrapped(payload, ctx):
        try:
            return slow(payload, ctx)
        finally:
            stopped.append(time.monotonic())
    e.register("image.quality", wrapped)
    t0 = time.monotonic()
    r = e.run("image.quality", {}, user=PATIENT, timeout=0.15)
    assert r.status == "timeout" and r.result is None and time.monotonic() - t0 < 1.0
    deadline = time.time() + 3
    while not stopped and time.time() < deadline:
        time.sleep(0.02)
    assert stopped, "the handler never stopped after the timeout"
    while e.resources.snapshot()["pending"] and time.time() < deadline:
        time.sleep(0.02)
    assert e.resources.snapshot()["pending"] == 0
    e.register("image.quality", ok_handler())
    assert e.run("image.quality", {}, user=PATIENT).status == "ok"                                          # the slot came back


def test_work_that_ignores_cancellation_keeps_its_slot_until_it_really_finishes():
    e = engine(max_conc=1, max_queue=0)
    release = threading.Event()
    e.register("image.quality", lambda p, c: release.wait(5) and HandlerResult("ok", {}) or HandlerResult("ok", {}))
    assert e.run("image.quality", {}, user=PATIENT, timeout=0.1).status == "timeout"
    assert e.resources.snapshot()["pending"] == 1                                                           # still running, still counted
    assert e.run("image.quality", {}, user=PATIENT, timeout=0.1).status == "resource_exhausted"            # so the next request is refused, not piled on top
    release.set()
    deadline = time.time() + 3
    while e.resources.snapshot()["pending"] and time.time() < deadline:
        time.sleep(0.02)
    assert e.resources.snapshot()["pending"] == 0


def test_the_queue_is_bounded_and_overflow_is_refused_immediately():
    e = engine(max_conc=1, max_queue=1)
    gate = threading.Event()
    e.register("image.quality", lambda p, c: gate.wait(5) and HandlerResult("ok", {}))
    results = []
    threads = [threading.Thread(target=lambda: results.append(e.run("image.quality", {}, user=PATIENT, timeout=5).status)) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.3)                                                                                         # one running, one waiting
    t0 = time.monotonic()
    third = e.run("image.quality", {}, user=PATIENT, timeout=5)
    assert third.status == "resource_exhausted" and time.monotonic() - t0 < 0.5 and third.result is None
    gate.set()
    for t in threads:
        t.join(5)
    assert results == ["ok", "ok"]


def test_per_capability_concurrency_limit_is_respected():
    e = engine(max_conc=4, max_queue=4)
    running, peak, lock = 0, 0, threading.Lock()

    def h(payload, ctx):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.15)
        with lock:
            running -= 1
        return HandlerResult("ok", {})
    e.register("speech.synthesize", h)                                                                      # limits.concurrency == 1
    registry.set_override("tts:any", (True, "voice"))
    ts = [threading.Thread(target=lambda: e.run("speech.synthesize", {"text": "x"}, user=PATIENT, timeout=5)) for _ in range(3)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert peak == 1
    registry.set_override("tts:any", None)


def test_the_callers_database_identity_travels_into_the_worker():
    e = engine()
    seen = {}

    def h(payload, ctx):
        c = db.context.current()
        seen.update(user=c.user_id, role=c.role)
        return HandlerResult("ok", {})
    e.register("image.quality", h)
    with db.context.as_user("patient1", "patient"):
        assert e.run("image.quality", {}, user=PATIENT).status == "ok"
    assert seen == {"user": "patient1", "role": "patient"}


def test_unsafe_output_is_withheld_not_edited():
    e = engine()
    e.register("image.quality", lambda p, c: HandlerResult("ok", {"x": 1}, message="You have melanoma and you should stop your medicine."))
    r = e.run("image.quality", {}, user=PATIENT)
    assert r.status == "abstained" and r.result is None and r.message == safety.REFUSE_MSG
    assert {"diagnostic_claim", "medication_change"} <= set(r.safety.violations)
    e.register("image.quality", lambda p, c: HandlerResult("ok", {"diagnosis": "melanoma"}))
    r = e.run("image.quality", {}, user=PATIENT)
    assert r.status == "abstained" and "structured:diagnosis" in r.safety.violations


def test_a_verbatim_quotation_from_a_source_is_allowed_and_labelled():
    e = engine()
    e.register("image.quality", lambda p, c: HandlerResult("ok", {}, message='Your report says: "Diagnosis of iron deficiency anaemia".', evidence=["Diagnosis of iron deficiency anaemia"]))
    r = e.run("image.quality", {}, user=PATIENT)
    assert r.status == "ok" and "quoted_from_source" in r.labels


def test_provenance_records_what_produced_the_result():
    e = engine()
    e.register("image.quality", ok_handler({"verdict": "accepted"}, processing_version="suitability-1.1.0", dataset_ref="synthetic-fixture"))
    r = e.run("image.quality", {}, user=PATIENT, request_id="req-1")
    p = r.provenance
    assert r.request_id == "req-1" and p.capability == "image.quality" and p.capability_version == "1.1.0" and p.engine_version.startswith("ai-engine-")
    assert p.code_revision and p.device == "cpu" and p.processing_version == "suitability-1.1.0" and p.dataset_ref == "synthetic-fixture"
    assert r.timings.total_ms >= 0 and r.timings.run_ms >= 0


def test_binary_artifacts_never_enter_the_json_envelope():
    e = engine()
    registry.set_override("tts:any", (True, "voice"))
    e.register("speech.synthesize", lambda p, c: HandlerResult("ok", {"mime": "audio/wav"}, artifacts={"audio": b"RIFFxxxx"}))
    r = e.run("speech.synthesize", {"text": "hello"}, user=PATIENT)
    assert r.artifacts == {"audio": b"RIFFxxxx"} and "RIFF" not in r.model_dump_json()
    registry.set_override("tts:any", None)


def test_health_reports_state_without_secrets():
    h = engine().health()
    blob = json.dumps(h, default=str)
    assert h["counts"]["available"] >= 1 and "resources" in h and h["device"]["kind"] == "cpu"
    for needle in ("SUPABASE", "COSMO_SECRET", "password", "service_role"):
        assert needle.lower() not in blob.lower()


# ---------------------------------------------------------------- resources
def test_model_cache_loads_once_under_concurrency_and_evicts_least_recently_used():
    cache = ModelCache(capacity=1)
    calls = []

    def loader(name):
        def f():
            calls.append(name)
            time.sleep(0.1)
            return {"model": name}
        return f
    out = []
    ts = [threading.Thread(target=lambda: out.append(cache.get("a", loader("a")))) for _ in range(6)]
    [t.start() for t in ts]
    [t.join(5) for t in ts]
    assert calls == ["a"] and len({id(o) for o in out}) == 1                                               # single flight
    cache.get("b", loader("b"))
    assert cache.stats()["resident"] == ["b"] and cache.loads == 2                                          # capacity 1: "a" was evicted
    assert cache.unload("b") and cache.stats()["resident"] == []


def test_model_loading_is_refused_explicitly_when_memory_is_short(monkeypatch):
    from app.ai import resources
    monkeypatch.setattr(resources, "available_mb", lambda: 100.0)
    with pytest.raises(ResourceExhausted):
        ModelCache().get("big", lambda: object(), est_mb=500)
    ModelCache().get("small", lambda: object(), est_mb=0)                                                  # no estimate: nothing to refuse


def test_device_adapter_never_claims_an_unverified_gpu(tmp_path, monkeypatch):
    from app.ai import resources
    rec = tmp_path / "gpu.json"
    monkeypatch.setattr(resources, "_torch_accelerator", lambda: None)
    assert select_device(record_path=rec).kind == "cpu"
    with pytest.raises(Exception) as e:
        select_device("cuda", rec)
    assert "not available" in str(e.value)
    monkeypatch.setattr(resources, "_torch_accelerator", lambda: ("cuda", "Fake GPU"))
    d = select_device("auto", rec)
    assert d.kind == "cpu" and "no verification record" in d.note                                           # present but unverified: not used automatically
    explicit = select_device("cuda", rec)
    assert explicit.kind == "cuda" and explicit.verified is False and "Unverified" in explicit.note
    rec.write_text(json.dumps({"passed": True, "device_kind": "cuda", "device_name": "Fake GPU", "verified_at": "2030-01-01"}))
    d = select_device("auto", rec)
    assert d.kind == "cuda" and d.verified is True
    with pytest.raises(Exception):
        select_device("tpu", rec)


def test_cancel_token_expiry_and_cancellation():
    t = CancelToken(deadline=time.monotonic() - 1)
    with pytest.raises(Exception) as e:
        t.raise_if_cancelled()
    assert getattr(e.value, "status", "") == "timeout"
    t2 = CancelToken()
    t2.cancel()
    with pytest.raises(Exception) as e2:
        t2.raise_if_cancelled()
    assert getattr(e2.value, "status", "") == "cancelled"


# ---------------------------------------------------------------- shared safety policy
@pytest.mark.parametrize("text,kind", [
    ("Can you diagnose me from this photo?", "diagnosis_request"), ("do I have cancer", "diagnosis_request"), ("is this mole melanoma", "diagnosis_request"),
    ("what is wrong with me", "diagnosis_request"), ("Should I stop taking my tablets?", "medication_advice"), ("how many mg should I take", "medication_advice"),
    ("can I double the dose", "medication_advice"), ("switch to another medicine?", "medication_advice"),
    ("I have chest pain", "urgent"), ("What does my ferritin result mean?", "education"), ("How much does the peel cost?", "education")])
def test_requests_are_classified(text, kind):
    assert safety.classify_request(text).kind == kind


def test_output_review_blocks_unsupported_claims_and_allows_neutral_text():
    ok = safety.review_text("Your report prints ferritin 8 ng/mL with a reference range of 15 - 150. Your doctor can explain what it means.")
    assert ok.allowed and not ok.violations
    for bad, code in [("This looks like a melanoma.", "diagnostic_claim"), ("You should stop your iron tablets.", "medication_change"),
                      ("The model is definitely right.", "certainty_claim"), ("Call an ambulance right now.", "unapproved_emergency_guidance"),
                      ("You have diabetes.", "diagnostic_claim"), ("I recommend you increase the dose.", "medication_change")]:
        r = safety.review_text(bad)
        assert not r.allowed and code in r.codes(), bad


def test_the_urgent_policy_message_is_the_only_permitted_emergency_wording():
    msg = safety.urgent_message()
    assert safety.review_text(msg).allowed
    assert not safety.review_text("Please go to the nearest emergency room immediately.").allowed


def test_model_derived_and_experimental_output_carries_its_limits():
    r = safety.review_text("Possible match: class A.", model_derived=True, experimental=True)
    assert "research_only" in r.labels and safety.CONFIDENCE_NOTE in r.disclaimers and safety.EXPERIMENTAL_NOTE in r.disclaimers


def test_contradictory_reports_are_flagged_not_resolved():
    rows = [{"name": "Ferritin", "value": "8", "unit": "ng/mL", "doc_date": "2026-03-01", "document_id": "d1", "source_page": 1},
            {"name": "ferritin", "value": "80", "unit": "ng/mL", "doc_date": "2026-03-01", "document_id": "d2", "source_page": 2},
            {"name": "TSH", "value": "2", "unit": "mIU/L", "doc_date": "2026-03-01", "document_id": "d1"},
            {"name": "TSH", "value": "2", "unit": "mIU/L", "doc_date": "2026-03-01", "document_id": "d2"}]
    c = safety.detect_contradictions(rows)
    assert len(c) == 1 and c[0]["test"] == "Ferritin" and {v["value"] for v in c[0]["values"]} == {"8", "80"} and "Neither is chosen" in c[0]["note"]


def test_abstention_messages_are_standard():
    for reason in ("unsupported", "unavailable", "insufficient_evidence", "clinical_decision", "urgent", "experimental_only"):
        a = safety.abstention(reason)
        assert a["abstained"] is True and a["reason"] == reason and a["message"]


# ---------------------------------------------------------------- API
def _tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


def test_capability_endpoint_is_role_appropriate_and_honest():
    with TestClient(app) as c:
        assert c.get("/api/v1/ai/capabilities").status_code == 401
        p = c.get("/api/v1/ai/capabilities", headers=_tok(c, "patient1")).json()
        ids = {x["id"]: x for x in p}
        assert set(ids["image.quality"]) == {"id", "kind", "name", "available", "status", "limitations"}
        assert "image.lesion_classification_research" not in ids and ids["image.acne_features"]["available"] is False and ids["image.acne_features"]["status"] == "unavailable"
        a = {x["id"]: x for x in c.get("/api/v1/ai/capabilities", headers=_tok(c, "admin1")).json()}
        assert a["image.acne_features"]["reasons"] and a["image.lesion_classification_research"]["audiences"] == ["admin"]
        assert c.get("/api/v1/ai/health", headers=_tok(c, "patient1")).status_code == 403
        assert c.get("/api/v1/ai/health", headers=_tok(c, "doctor1")).status_code == 403
        h = c.get("/api/v1/ai/health", headers=_tok(c, "admin1")).json()
        assert h["device"]["kind"] == "cpu" and h["counts"]["unavailable"] >= 1


def test_gpu_verification_script_writes_nothing_without_an_accelerator_and_a_record_only_after_a_matching_run(tmp_path):
    from scripts import verify_gpu
    out = tmp_path / "gpu.json"
    res = verify_gpu.verify(out)                                           # this machine has no accelerator
    assert res["passed"] is False and res["written"] is False and "No CUDA or MPS" in res["reason"] and not out.exists()
    res = verify_gpu.verify(out, device_override="cpu")                    # logic check only: the CPU stands in for a device in a temporary file
    assert res["passed"] is True and out.exists() and json.loads(out.read_text())["max_abs_diff"] <= verify_gpu.ATOL

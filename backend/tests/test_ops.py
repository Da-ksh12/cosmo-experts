"""Production foundation: secrets from files, startup refusal, shared rate limiting (fakeredis), Prometheus metrics."""
import re
import time

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app import metrics, prod_checks, ratelimit, secrets_env
from app.main import app

GOOD = {"COSMO_MODE": "production", "COSMO_SECRET": "s" * 40, "COSMO_AUTH": "supabase", "SUPABASE_URL": "https://x.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "k", "COSMO_STORAGE": "supabase", "COSMO_CORS": "https://app.example.org",
        "COSMO_BACKUP_KEY": "A" * 43 + "=", "COSMO_ALLOW_NO_CONSENT": "1",
        "COSMO_DB": "postgres", "COSMO_DATABASE_URL": "postgresql://cosmo_app:pw@db.example.org:5432/cosmo?sslmode=require", "COSMO_COOKIE_SECURE": "1",
        "SUPABASE_ANON_KEY": "anon-test", "COSMO_ADMIN_CLINICAL_ACCESS": "none", "COSMO_DB_CONTEXT_SECRET": "c" * 40, "COSMO_SCANNER": "clamd"}      # consent is covered by tests/test_consent.py


# ---------------------------------------------------------------- secrets from files
def test_file_secrets_are_loaded_but_never_override_and_fail_loudly(tmp_path):
    f = tmp_path / "s"; f.write_text("from-file\n")
    env = {"COSMO_SECRET_FILE": str(f)}
    assert secrets_env.load(env) == ["COSMO_SECRET"] and env["COSMO_SECRET"] == "from-file"
    env = {"COSMO_SECRET_FILE": str(f), "COSMO_SECRET": "explicit"}
    assert secrets_env.load(env) == [] and env["COSMO_SECRET"] == "explicit"
    with pytest.raises(SystemExit, match="unreadable"):
        secrets_env.load({"COSMO_SECRET_FILE": str(tmp_path / "missing")})
    (tmp_path / "e").write_text("  \n")
    with pytest.raises(SystemExit, match="empty"):
        secrets_env.load({"COSMO_SECRET_FILE": str(tmp_path / "e")})
    env = {"HOME_FILE": str(f), "PATH_FILE": str(f)}                               # only allow-listed names are honoured
    assert secrets_env.load(env) == [] and "HOME" not in env


# ---------------------------------------------------------------- production startup validation
def test_a_correct_production_configuration_has_no_problems():
    assert prod_checks.problems(GOOD) == []
    prod_checks.enforce(GOOD)


@pytest.mark.parametrize("change,needle", [
    ({"COSMO_SECRET": "dev-only-secret-change-me"}, "COSMO_SECRET"), ({"COSMO_SECRET": "short"}, "COSMO_SECRET"),
    ({"COSMO_AUTH": "local"}, "COSMO_AUTH"), ({"SUPABASE_SERVICE_ROLE_KEY": ""}, "COSMO_AUTH"),
    ({"COSMO_STORAGE": "local"}, "COSMO_STORAGE"), ({"COSMO_RATE_LIMIT": "off"}, "RATE_LIMIT"),
    ({"COSMO_CORS": "*"}, "COSMO_CORS"), ({"COSMO_CORS": "http://localhost:3000"}, "COSMO_CORS"), ({"COSMO_CORS": "https://a.org,http://b.org"}, "COSMO_CORS"),
    ({"COSMO_ALLOW_LOCAL_HTTP": "1"}, "LOCAL_HTTP"), ({"COSMO_ASSISTANT_MODE": "llm"}, "ASSISTANT_MODE"),
    ({"COSMO_BACKUP_KEY": ""}, "BACKUP_KEY"), ({"COSMO_BACKUP_KEY": "bm90LTMyLWJ5dGVz"}, "BACKUP_KEY"), ({"COSMO_BACKUP_KEY": "!!!"}, "BACKUP_KEY"),
])
def test_each_unsafe_production_setting_blocks_startup(change, needle):
    env = {**GOOD, **change}
    assert any(needle in p for p in prod_checks.problems(env)), prod_checks.problems(env)
    with pytest.raises(SystemExit, match="Refusing to start"):
        prod_checks.enforce(env)


def test_explicit_acknowledgements_are_honoured_and_other_modes_are_not_checked():
    assert prod_checks.problems({**GOOD, "COSMO_STORAGE": "local", "COSMO_ALLOW_LOCAL_STORAGE": "1"}) == []
    assert prod_checks.problems({**GOOD, "COSMO_ASSISTANT_MODE": "llm", "COSMO_LLM_APPROVED": "1"}) == []
    prod_checks.enforce({"COSMO_MODE": "local"})                                   # nothing enforced outside production


# ---------------------------------------------------------------- rate limiting
def test_memory_limiter_window_and_independent_keys():
    s = ratelimit.MemoryStore()
    assert [s.hit("a", 3, 60)[0] for _ in range(4)] == [True, True, True, False]
    ok, retry = s.hit("a", 3, 60)
    assert not ok and 1 <= retry <= 60 and s.hit("b", 3, 60)[0]
    t = ratelimit.MemoryStore()
    assert t.hit("w", 1, 1)[0] and not t.hit("w", 1, 1)[0]
    time.sleep(1.05)
    assert t.hit("w", 1, 1)[0]


def test_redis_limiter_is_shared_between_instances_and_rejections_are_not_counted():
    server = fakeredis.FakeServer()
    a = ratelimit.RedisStore(fakeredis.FakeRedis(server=server))
    b = ratelimit.RedisStore(fakeredis.FakeRedis(server=server))                    # a second application instance
    assert a.hit("login:1.2.3.4", 3, 60)[0] and b.hit("login:1.2.3.4", 3, 60)[0] and a.hit("login:1.2.3.4", 3, 60)[0]
    ok, retry = b.hit("login:1.2.3.4", 3, 60)
    assert not ok and 1 <= retry <= 60                                              # the 4th attempt across both instances is refused
    assert b.r.zcard("cosmo:rl:login:1.2.3.4") == 3                                 # the rejected attempt was removed
    assert a.hit("login:9.9.9.9", 3, 60)[0]                                         # other keys unaffected
    a.reset()
    assert a.hit("login:1.2.3.4", 3, 60)[0]


def test_redis_outage_falls_back_to_in_process_limiting_and_is_counted():
    class Down:
        name = "redis"

        def hit(self, *a):
            raise ConnectionError("down")

        def reset(self):
            raise ConnectionError("down")
    lim = ratelimit.Limiter(Down())
    assert [lim.hit("k", 2, 60)[0] for _ in range(3)] == [True, True, False]        # still limited, per process
    assert lim.errors == 3
    lim.reset()


def test_the_middleware_uses_the_shared_store(monkeypatch):
    monkeypatch.delenv("COSMO_RATE_LIMIT", raising=False)
    server = fakeredis.FakeServer()
    old = ratelimit._limiter
    try:
        ratelimit.set_limiter(ratelimit.Limiter(ratelimit.RedisStore(fakeredis.FakeRedis(server=server))))
        with TestClient(app) as c:
            codes = [c.post("/api/auth/login", json={"user_id": "nobody", "password": "x"}).status_code for _ in range(10)]
        assert codes[:8].count(429) == 0 and codes[8:] == [429, 429]
        keys = list(fakeredis.FakeRedis(server=server).scan_iter("cosmo:rl:login:*"))
        assert len(keys) == 1                                                       # the counter lives in Redis, not in the process
    finally:
        ratelimit.set_limiter(old)


def test_limiter_from_env_selects_redis_only_when_configured():
    assert isinstance(ratelimit.Limiter.from_env({}).store, ratelimit.MemoryStore)
    assert isinstance(ratelimit.Limiter.from_env({"COSMO_REDIS_URL": "redis://127.0.0.1:9/0"}).store, ratelimit.RedisStore)


# ---------------------------------------------------------------- metrics
def test_metrics_endpoint_is_hidden_without_a_token_and_protected_with_one(monkeypatch):
    with TestClient(app) as c:
        monkeypatch.delenv("COSMO_METRICS_TOKEN", raising=False)
        assert c.get("/metrics").status_code == 404
        monkeypatch.setenv("COSMO_METRICS_TOKEN", "t" * 32)
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Basic " + "t" * 32}).status_code == 401
        c.get("/api/v1/health/live"); c.get("/api/documents")                       # a 200 and a 401
        r = c.get("/metrics", headers={"Authorization": "Bearer " + "t" * 32})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain") and r.headers["cache-control"] == "no-store"
    t = r.text
    assert 'cosmo_http_requests_total{method="GET",route="/api/v1/health/live",status="2xx"}' in t
    assert re.search(r'cosmo_http_requests_total\{method="GET",route="/api/documents",status="4xx"\} \d+', t)
    assert "cosmo_database_up 1" in t and 'cosmo_private_storage_up{backend="local"} 1' in t
    for name in ("cosmo_jobs_queued", "cosmo_backup_last_success_timestamp_seconds", "cosmo_data_disk_free_bytes", "cosmo_urgent_policy_approved 0",
                 "cosmo_kb_articles_approved", "cosmo_process_uptime_seconds", "cosmo_ratelimit_store_errors_total"):
        assert name in t, name


def test_metric_labels_are_route_templates_not_raw_paths_and_histograms_are_consistent():
    metrics.reset()
    with TestClient(app) as c:
        for i in range(5):
            c.get(f"/api/documents/id-{i}/file")                                    # unauthenticated, distinct ids
            c.get(f"/no/such/path/{i}")
    t = metrics.render()
    assert "id-0" not in t and "/no/such/path" not in t
    assert 'route="/api/documents/{doc_id}/file"' in t and 'route="unmatched"' in t
    m = re.search(r'duration_seconds_count\{method="GET",route="unmatched"\} (\d+)', t)
    inf = re.search(r'duration_seconds_bucket\{method="GET",route="unmatched",le="\+Inf"\} (\d+)', t)
    assert m and inf and m.group(1) == inf.group(1) == "5"


def test_a_failing_collector_does_not_break_the_scrape(monkeypatch):
    from app import storage
    def boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(storage, "healthy", boom)
    t = metrics.render()
    assert "cosmo_database_up 1" in t and 'cosmo_collector_errors{collector="store"} 1' in t


def test_production_refuses_the_silent_lexical_retrieval_fallback(monkeypatch):
    from app.assistant import retrieval
    monkeypatch.setattr(retrieval.SentenceTransformerEmbedder, "available", property(lambda self: False))
    assert any("dense embedding model" in p for p in prod_checks.retrieval_problems({}))
    with pytest.raises(SystemExit, match="Refusing to start"):
        prod_checks.enforce(GOOD)
    assert prod_checks.retrieval_problems({"COSMO_ALLOW_LEXICAL": "1"}) == []               # explicit, visible acceptance
    assert any("dense" in p for p in prod_checks.retrieval_problems({"COSMO_RETRIEVAL": "tfidf"}))


def test_production_starts_when_the_dense_model_is_present():
    from app.assistant import retrieval
    if not retrieval.SentenceTransformerEmbedder().available:
        pytest.skip("embedding model not downloaded")
    assert prod_checks.retrieval_problems({}) == []
    prod_checks.enforce(GOOD)

"""API and operational security: CORS, headers, size limits, bounded concurrency, timeouts, safe errors, SSRF guard, and a static check for dangerous calls."""
import ast
import asyncio
import json
import logging
import socket
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import limits, netguard
from app.config import MAX_UPLOAD_BYTES
from app.main import app

APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def login(client):
    return {"Authorization": "Bearer " + client.post("/api/auth/login", json={"user_id": "patient1", "password": "cosmo-dev"}).json()["token"]}


# ---------------------------------------------------------------- CORS
def test_cors_allows_only_listed_origins_methods_and_headers(client):
    ok = client.options("/api/me", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:3000" and "access-control-allow-credentials" not in ok.headers
    assert "TRACE" not in ok.headers["access-control-allow-methods"] and "*" not in ok.headers["access-control-allow-methods"] and "*" not in ok.headers["access-control-allow-headers"]
    evil = client.options("/api/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in evil.headers
    bad_header = client.options("/api/me", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-custom-thing"})
    assert bad_header.status_code == 400
    assert client.get("/api/me", headers={"Origin": "https://evil.example"}).headers.get("access-control-allow-origin") is None


def test_a_wildcard_origin_is_refused_in_production():
    from app import prod_checks
    from tests.test_ops import GOOD
    assert prod_checks.problems({**GOOD, "COSMO_CORS": "*"}) and prod_checks.problems({**GOOD, "COSMO_CORS": "http://app.example.org"})


# ---------------------------------------------------------------- headers and request ids
def test_api_responses_carry_security_headers_and_a_sanitised_request_id(client, monkeypatch):
    r = client.get("/api/me", headers={"X-Request-ID": "abc<script>123\r\nX-Evil: 1"})
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"
    assert "default-src 'none'" in h["content-security-policy"] and "camera=()" in h["permissions-policy"] and h["cross-origin-resource-policy"] == "same-site"
    assert h["x-request-id"] == "abcscript123X-Evil1" and "strict-transport-security" not in h                          # plain http in development: no HSTS
    monkeypatch.setenv("COSMO_MODE", "production")
    assert "max-age=31536000" in client.get("/api/me").headers["strict-transport-security"]


# ---------------------------------------------------------------- size limits
def test_oversized_json_is_refused_whether_declared_or_streamed(client, monkeypatch):
    h = login(client)
    big = b"{" + b'"a":"' + b"x" * (1024 * 1024 + 10) + b'"}'
    r = client.post("/api/conversations", content=big, headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"] == "This request is too large." and r.json()["request_id"]

    def chunks():                                                                                                            # no Content-Length: chunked transfer
        for _ in range(40):
            yield b"x" * 65536
    r = client.post("/api/consent/accept", content=chunks(), headers={**h, "Content-Type": "application/json"})                # a route that reads its body
    assert r.status_code == 413
    monkeypatch.setenv("COSMO_MAX_JSON_KB", "4")
    assert client.post("/api/conversations", content=b"{" + b" " * 8000 + b"}", headers={**h, "Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/conversations", headers=h).status_code == 200                                                    # normal requests are unaffected


def test_upload_routes_get_the_upload_limit_and_not_more(client):
    h = login(client)
    over = b"%PDF-" + b"x" * (MAX_UPLOAD_BYTES + 2 * 1024 * 1024)
    r = client.post("/api/documents", files={"file": ("a.pdf", over)}, headers=h)
    assert r.status_code == 413
    just = b"%PDF-" + b"x" * (MAX_UPLOAD_BYTES - 100)
    r = client.post("/api/documents", files={"file": ("a.pdf", just)}, headers=h)
    assert r.status_code in (413, 422) and r.status_code != 400                                                                # reaches the route (too large / not a valid PDF), not cut off earlier
    assert limits.UPLOAD_ROUTES.match("POST /api/documents") and limits.UPLOAD_ROUTES.match("POST /api/v1/images/check") and not limits.UPLOAD_ROUTES.match("POST /api/auth/login")


def test_a_non_numeric_content_length_is_a_400_not_a_crash(client):
    r = client.post("/api/conversations", content=b"{}", headers={"Content-Length": "abc", **login(client)})
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------- concurrency and timeouts (the middleware alone, with controllable endpoints)
def run_asgi(inner, requests, **env):
    import os
    old = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})

    async def go():
        transport = httpx.ASGITransport(app=limits.RequestLimits(inner))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(*[requests(c, i) for i in range(len(requests.__defaults__ or [1]))]) if False else await requests(c)
    try:
        return asyncio.run(go())
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


async def slow_app(scope, receive, send):
    if scope["type"] != "http":
        return
    tail = scope["path"].rsplit("/", 1)[1]
    await asyncio.sleep(float(tail) if tail.replace(".", "").isdigit() else 0)
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"done"})


def test_requests_beyond_the_concurrency_cap_are_refused_at_once_not_queued():
    async def scenario(c):
        a = asyncio.create_task(c.get("/work/0.6"))
        await asyncio.sleep(0.1)
        b = await c.get("/work/0")                                                                                           # arrives while the only slot is taken
        live = await c.get("/api/v1/health/live")                                                                            # health checks are never shed
        return b, live, await a
    b, live, a = run_asgi(slow_app, scenario, COSMO_MAX_CONCURRENT_REQUESTS=1)
    assert b.status_code == 503 and b.headers["retry-after"] == "2" and "busy" in b.json()["detail"]
    assert live.status_code == 200 and a.status_code == 200 and a.text == "done"


def test_a_slot_is_released_after_each_request():
    async def scenario(c):
        return [(await c.get("/work/0")).status_code for _ in range(5)]
    assert run_asgi(slow_app, scenario, COSMO_MAX_CONCURRENT_REQUESTS=1) == [200] * 5


def test_slow_requests_time_out_with_a_504_and_the_slot_is_freed():
    async def scenario(c):
        r = await c.get("/work/3")
        return r, await c.get("/work/0")
    slow, after = run_asgi(slow_app, scenario, COSMO_REQUEST_TIMEOUT_S=1, COSMO_MAX_CONCURRENT_REQUESTS=1)
    assert slow.status_code == 504 and "too long" in slow.json()["detail"] and after.status_code == 200


def test_invalid_limit_settings_fail_loudly_not_silently():
    with pytest.raises(SystemExit):
        run_asgi(slow_app, lambda c: c.get("/work/0"), COSMO_MAX_CONCURRENT_REQUESTS="many")


# ---------------------------------------------------------------- safe errors
def test_unhandled_errors_return_a_generic_body_with_the_request_id_and_log_no_secrets(client, monkeypatch, caplog):
    from app.routers import patient
    secret = "Sup3r-Secret-Value-123"

    def boom(*a, **k):
        raise RuntimeError(f"db password={secret} at /srv/app/db.py line 1")
    monkeypatch.setattr(patient.core, "check_and_store_image", boom)
    monkeypatch.setattr(patient.consent, "enforced", lambda *a, **k: False)
    caplog.set_level(logging.ERROR)
    r = client.post("/api/images", data={"region": "face"}, files={"file": ("p.jpg", b"x")}, headers={**login(client), "X-Request-ID": "trace-77"})
    assert r.status_code == 500 and r.json() == {"detail": "Something went wrong on our side. Please try again.", "request_id": "trace-77"}
    assert secret not in r.text and "Traceback" not in r.text and "db.py" not in r.text and r.headers["x-request-id"] == "trace-77"
    assert "trace-77" in caplog.text and secret not in caplog.text


def test_validation_errors_never_echo_the_submitted_values(client):
    pw = "My-Very-Private-Password-9"
    r = client.post("/api/auth/login", json={"user_id": ["not", "a", "string"], "password": {"nested": pw}})
    assert r.status_code == 422 and pw not in r.text and "input" not in r.text
    body = r.json()
    assert body["detail"][0]["loc"] and body["request_id"]
    r2 = client.post("/api/auth/login", content=b"{not json " + pw.encode(), headers={"Content-Type": "application/json"})
    assert r2.status_code == 422 and pw not in r2.text


def test_signed_link_tokens_are_redacted_from_logs():
    from app.redact import redact
    assert redact("GET /api/files/eyJ1IjoiYSJ9abcdefgh.0123456789abcdef0123456789abcdef01234567 200") == "GET /api/files/[REDACTED] 200"
    assert redact("GET /api/documents/abc 200") == "GET /api/documents/abc 200"


# ---------------------------------------------------------------- SSRF guard
def resolver(*addrs):
    def r(host, port, type=0):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 0, "", (a, port)) for a in addrs]
    return r


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.1.1.1"])
def test_internal_addresses_are_refused(addr):
    with pytest.raises(netguard.Blocked):
        netguard.assert_public("https://dataset.example.org/f", env={}, resolver=resolver(addr))


def test_public_addresses_pass_and_any_internal_answer_among_several_blocks():
    netguard.assert_public("https://dataset.example.org/f", env={}, resolver=resolver("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"))
    with pytest.raises(netguard.Blocked):
        netguard.assert_public("https://dataset.example.org/f", env={}, resolver=resolver("93.184.216.34", "10.0.0.1"))               # a name that also points inside


@pytest.mark.parametrize("url", ["ftp://example.org/f", "file:///etc/passwd", "https://user:pw@example.org/f", "https:///nohost", "gopher://example.org", "javascript:alert(1)"])
def test_only_plain_http_and_https_without_credentials_are_allowed(url):
    with pytest.raises(netguard.Blocked):
        netguard.assert_public(url, env={}, resolver=resolver("93.184.216.34"))


def test_names_that_do_not_resolve_are_refused():
    def nope(*a, **k):
        raise socket.gaierror("no such host")
    with pytest.raises(netguard.Blocked):
        netguard.assert_public("https://nowhere.invalid/f", env={}, resolver=nope)


def test_the_local_http_test_hook_is_narrow():
    env = {"COSMO_ALLOW_LOCAL_HTTP": "1"}
    netguard.assert_public("http://127.0.0.1:9/f", env=env)
    for url in ("http://10.0.0.1/f", "https://127.0.0.1/f"):
        with pytest.raises(netguard.Blocked):
            netguard.assert_public(url, env=env, resolver=resolver("10.0.0.1" if "10." in url else "127.0.0.1"))
    with pytest.raises(netguard.Blocked):
        netguard.assert_public("http://127.0.0.1/f", env={}, resolver=resolver("127.0.0.1"))                                         # off by default


def test_redirects_to_internal_addresses_are_refused(monkeypatch):
    import urllib.request
    monkeypatch.setattr(socket, "getaddrinfo", resolver("169.254.169.254"))
    h = netguard._GuardedRedirects()
    with pytest.raises(netguard.Blocked):
        h.redirect_request(urllib.request.Request("https://dataset.example.org/f"), None, 302, "Found", {}, "https://metadata.internal/latest/meta-data")


def test_the_dataset_importer_and_registry_use_the_guard(tmp_path, monkeypatch):
    from app.datasets import importer, registry
    monkeypatch.setattr(socket, "getaddrinfo", resolver("10.1.2.3"))
    with pytest.raises(importer.ImportError_, match="not allowed"):
        importer.download("https://dataset.example.org/f.zip", tmp_path / "f.zip", None, "ssrf1")
    assert not (tmp_path / "f.zip").exists()
    with pytest.raises(netguard.Blocked):
        registry.official_manifest({"url": "https://data.mendeley.com/datasets/abc123"}, timeout=1)


# ---------------------------------------------------------------- dangerous calls
def test_no_shell_execution_dynamic_evaluation_or_unsafe_deserialisation_in_the_application():
    problems = []
    for path in list(APP_DIR.rglob("*.py")) + list((APP_DIR.parent / "scripts").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f"{f.value.id}.{f.attr}" if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else "")
            where = f"{path.name}:{n.lineno}"
            if name in ("eval", "exec", "os.system", "os.popen", "pickle.load", "pickle.loads", "yaml.load", "marshal.load", "marshal.loads"):
                problems.append(f"{where} {name}")
            if name.startswith("subprocess.") and name != "subprocess.CalledProcessError":
                if any(k.arg == "shell" and getattr(k.value, "value", None) is True for k in n.keywords):
                    problems.append(f"{where} shell=True")
                if n.args and not isinstance(n.args[0], (ast.List, ast.Tuple)) and not (isinstance(n.args[0], ast.Name) and n.args[0].id in ("args", "cmd")):
                    problems.append(f"{where} {name} with a non-list command")
            if name == "torch.load" and not any(k.arg == "weights_only" and getattr(k.value, "value", None) is True for k in n.keywords):
                problems.append(f"{where} torch.load without weights_only=True")
    assert problems == []

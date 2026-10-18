"""Live TLS check: Caddy (internal CA) in front of the real backend. Usage: python ops/test_tls_local.py <path-to-caddy> [--backend-python <python>]

Verifies: HTTPS answers with a certificate chain, TLS >= 1.2, HSTS and security headers, HTTP redirects to HTTPS, /metrics is not reachable through the proxy
even when the backend has a metrics token, and the proxied API works. Starts and stops its own processes on free ports."""
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait(url, ctx=None, tries=120):
    for _ in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=3, context=ctx)
        except urllib.error.HTTPError as e:
            return e
        except Exception:
            time.sleep(0.5)
    raise SystemExit(f"timeout waiting for {url}")


def main():
    caddy = sys.argv[1]
    py = sys.argv[sys.argv.index("--backend-python") + 1] if "--backend-python" in sys.argv else str(ROOT / "backend" / ".venv" / "Scripts" / "python.exe")
    up, http, https = port(), port(), port()
    data = tempfile.mkdtemp(prefix="tlstest-")
    env = {**os.environ, "COSMO_DATA_DIR": data, "COSMO_METRICS_TOKEN": "m" * 32}
    be = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(up), "--proxy-headers"], cwd=ROOT / "backend", env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cenv = {**os.environ, "TEST_HTTP_PORT": str(http), "TEST_HTTPS_PORT": str(https), "TEST_UPSTREAM_PORT": str(up), "XDG_DATA_HOME": data, "APPDATA": data}
    cd = subprocess.Popen([caddy, "run", "--config", str(ROOT / "ops" / "Caddyfile.local"), "--adapter", "caddyfile"], env=cenv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = {}
    try:
        wait(f"http://127.0.0.1:{up}/api/health")
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE   # the internal CA is deliberately not trusted by the OS
        r = wait(f"https://localhost:{https}/api/v1/health/live", ctx)
        results["https_status"] = r.status
        results["body"] = json.loads(r.read())
        results["hsts"] = r.headers.get("Strict-Transport-Security")
        results["x_content_type_options"] = r.headers.get("X-Content-Type-Options")
        results["x_frame_options"] = r.headers.get("X-Frame-Options")
        results["server_header_removed"] = r.headers.get("Server") is None
        results["backend_request_id_passed_through"] = bool(r.headers.get("X-Request-ID"))
        with socket.create_connection(("localhost", https), timeout=5) as raw, ctx.wrap_socket(raw, server_hostname="localhost") as tls:
            results["tls_version"] = tls.version()
            results["cipher"] = tls.cipher()[0]
            der = tls.getpeercert(binary_form=True)
        results["certificate_bytes"] = len(der)
        # strict verification must FAIL (certificate comes from an untrusted internal CA): proves a real certificate is being presented and checked
        try:
            urllib.request.urlopen(f"https://localhost:{https}/", timeout=5)
            results["untrusted_ca_rejected_by_default_client"] = False
        except (ssl.SSLError, urllib.error.URLError) as e:
            results["untrusted_ca_rejected_by_default_client"] = "CERTIFICATE_VERIFY_FAILED" in str(e)
        try:
            urllib.request.urlopen(f"https://localhost:{https}/metrics", timeout=5, context=ctx)
            results["metrics_via_proxy_status"] = 200
        except urllib.error.HTTPError as e:
            results["metrics_via_proxy_status"] = e.code
        direct = urllib.request.Request(f"http://127.0.0.1:{up}/metrics", headers={"Authorization": "Bearer " + "m" * 32})
        results["metrics_direct_on_private_port_status"] = urllib.request.urlopen(direct, timeout=5).status
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k): return None
        try:
            urllib.request.build_opener(NoRedirect).open(f"http://localhost:{http}/api/health", timeout=5)
            results["http_redirect"] = "NO REDIRECT"
        except urllib.error.HTTPError as e:
            results["http_redirect"] = f"{e.code} -> {e.headers.get('Location')}"
    finally:
        for p in (cd, be):
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
    ok = (results.get("https_status") == 200 and results.get("tls_version") in ("TLSv1.2", "TLSv1.3") and "max-age=31536000" in (results.get("hsts") or "")
          and results.get("metrics_via_proxy_status") == 404 and results.get("metrics_direct_on_private_port_status") == 200
          and str(results.get("http_redirect", "")).startswith("301 -> https://localhost:") and results.get("untrusted_ca_rejected_by_default_client"))
    results["PASS"] = bool(ok)
    print(json.dumps(results, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

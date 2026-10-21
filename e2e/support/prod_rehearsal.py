"""A LOCAL rehearsal of the production sign-in path, for e2e/android-release.mjs. It is NOT a deployment and talks to no cloud service.

It runs, in one process (so killing it leaves nothing behind):
  * the protocol-level fake of Supabase Auth from backend/tests/fake_supabase.py (real ES256 tokens, real JWKS; no e-mail is sent; no real project is touched),
  * the real backend (app.main) with COSMO_AUTH=supabase pointed at that fake, exactly the sign-in code path production uses (or, with --mode local, the demo-account mode that a release app must refuse),
  * a tiny control endpoint on 127.0.0.1 so a test can confirm a newly registered address (stand-in for clicking the link in the e-mail).

Usage (from the repository root, with the backend's Python):
    backend/.venv/Scripts/python.exe e2e/support/prod_rehearsal.py --port 8030 --control-port 8031 --data-dir <empty folder> --out <credentials.json> [--mode supabase|local]
It prints one line  READY {json}  when the backend answers, and runs until it is killed. The credentials written to --out belong to the fake only."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8030)
ap.add_argument("--control-port", type=int, default=8031)
ap.add_argument("--data-dir", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--mode", choices=["supabase", "local"], default="supabase")
args = ap.parse_args()

sys.path.insert(0, str(BACKEND))
spec = importlib.util.spec_from_file_location("fake_supabase", BACKEND / "tests" / "fake_supabase.py")
fake_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake_mod)

def password() -> str:
    return "Rh-" + secrets.token_urlsafe(15)                      # 12+ characters, as the password policy requires

creds: dict = {"mode": args.mode, "api": f"http://127.0.0.1:{args.port}", "control": f"http://127.0.0.1:{args.control_port}"}
fake = None
env = {
    "COSMO_DATA_DIR": args.data_dir, "COSMO_CORS": "https://localhost", "COSMO_RATE_LIMIT": "off", "COSMO_SECRET": secrets.token_urlsafe(48),
    "COSMO_STRUCTURED_LOGS": "0",
}
for k in ("COSMO_MODE", "COSMO_DB", "COSMO_DATABASE_URL", "COSMO_STORAGE", "COSMO_REQUIRE_CONSENT", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.pop(k, None)
models = BACKEND / "data" / "models"
if (models / "embed" / "bge-small-en-v1.5").exists():
    env["COSMO_EMBED_MODEL_DIR"] = str(models / "embed" / "bge-small-en-v1.5")
if (models / "stt" / "faster-whisper-base").exists():
    env["COSMO_WHISPER_MODEL_DIR"] = str(models / "stt" / "faster-whisper-base")

if args.mode == "supabase":
    fake = fake_mod.FakeSupabase()
    creds["doctor"] = {"email": "doctor@clinic.test", "password": password()}
    creds["patient"] = {"email": "patient@clinic.test", "password": password()}
    fake.add_user(creds["doctor"]["email"], creds["doctor"]["password"], role="doctor", confirmed=True, full_name="Dr Rehearsal")
    fake.add_user(creds["patient"]["email"], creds["patient"]["password"], role="patient", confirmed=True, full_name="Pat Rehearsal")
    env.update({"COSMO_AUTH": "supabase", "SUPABASE_URL": fake.url, "SUPABASE_ANON_KEY": fake_mod.ANON, "SUPABASE_SERVICE_ROLE_KEY": fake_mod.SERVICE,
                "COSMO_ALLOW_REGISTRATION": "1", "COSMO_COOKIE_SECURE": "0"})
else:
    env["COSMO_AUTH"] = "local"
    pw = password()
    env["COSMO_DEV_PASSWORD"] = pw
    creds["demo"] = {"user_id": "patient1", "password": pw}
os.environ.update(env)
Path(args.out).write_text(json.dumps(creds), encoding="utf-8")


class Control(BaseHTTPRequestHandler):
    def log_message(self, *a):                                                            # quiet
        pass

    def _send(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if fake is None:
            return self._send(404, {"error": "no fake in this mode"})
        if u.path == "/confirm":
            uid, user = fake.by_email(q.get("email", ""))
            if not user:
                return self._send(404, {"error": "unknown address"})
            user["confirmed"] = True
            return self._send(200, {"confirmed": user["email"]})
        self._send(404, {"error": "no such route"})

    def do_GET(self):
        if urlparse(self.path).path == "/users":
            return self._send(200, {"users": [{"email": u["email"], "confirmed": u["confirmed"], "role": u["role"]} for u in (fake.users.values() if fake else [])]})
        self._send(200, {"ok": True})


ctl = ThreadingHTTPServer(("127.0.0.1", args.control_port), Control)
threading.Thread(target=ctl.serve_forever, daemon=True).start()

import uvicorn  # noqa: E402  (after the environment is set: app.config reads it at import time)
from app.main import app  # noqa: E402


def announce():
    for _ in range(240):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/v1/health/live", timeout=2) as r:
                if r.status == 200:
                    print("READY " + json.dumps({"api": creds["api"], "control": creds["control"], "mode": args.mode}), flush=True)
                    return
        except Exception:
            time.sleep(1)
    print("FAILED backend did not start", flush=True)


threading.Thread(target=announce, daemon=True).start()
uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", proxy_headers=False)

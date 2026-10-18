"""A local, protocol-level fake of the parts of Supabase Auth (GoTrue) + PostgREST profiles that the backend uses.

It issues REAL ES256 JWTs and serves a REAL JWKS, so app.supabase_auth.Verifier runs its production code path. It exists to test the account lifecycle (sign-in,
refresh rotation, sign-out, registration, recovery, bans) without sending e-mail or touching a real project. It is a test double: nothing in the application imports it.
Behaviour switches: confirm_required, down, rate_limited; call_log records every request (method, path, query, body keys, whether a bearer was present)."""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

ANON, SERVICE = "anon-key-for-tests", "service-key-for-tests"


class FakeSupabase:
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.kid = "kid-" + uuid.uuid4().hex[:8]
        self.users: dict[str, dict] = {}            # id -> {email, password, confirmed, banned, role, full_name}
        self.refresh: dict[str, str] = {}            # refresh token -> user id (valid ones)
        self.call_log: list[dict] = []
        self.confirm_required = False
        self.down = False
        self.rate_limited = False
        self.access_ttl = 3600
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                auth = self.headers.get("Authorization", "")
                fake.call_log.append({"method": method, "path": u.path, "query": q, "body_keys": sorted(body), "body": body, "apikey": self.headers.get("apikey"),
                                      "bearer": auth[7:] if auth.startswith("Bearer ") else ""})
                if fake.down:
                    return self._send(500, {"msg": "internal error"})
                with fake.lock:
                    status, out = fake.route(method, u.path, q, body, auth, self.headers.get("apikey", ""))
                self._send(status, out)

            do_GET = lambda self: self._handle("GET")      # noqa: E731
            do_POST = lambda self: self._handle("POST")    # noqa: E731
            do_PUT = lambda self: self._handle("PUT")      # noqa: E731
            do_PATCH = lambda self: self._handle("PATCH")  # noqa: E731
            do_DELETE = lambda self: self._handle("DELETE")  # noqa: E731

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    # ------------------------------------------------------------------ tokens
    def jwks(self):
        jwk = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(self.key.public_key()))
        jwk.update(kid=self.kid, alg="ES256", use="sig")
        return {"keys": [jwk]}

    def access_token(self, uid: str, ttl: int | None = None, **claims) -> str:
        u = self.users[uid]
        now = int(time.time())
        payload = {"sub": uid, "aud": "authenticated", "iss": f"{self.url}/auth/v1", "exp": now + (self.access_ttl if ttl is None else ttl), "iat": now, "email": u["email"],
                   "role": "authenticated", "session_id": uuid.uuid4().hex, "app_metadata": {"role": "admin"}, "user_metadata": {"role": "admin"}}      # hostile claims on purpose
        payload.update(claims)
        return jwt.encode(payload, self.key, algorithm="ES256", headers={"kid": self.kid})

    def session(self, uid: str) -> dict:
        rt = "rt-" + uuid.uuid4().hex
        self.refresh[rt] = uid
        return {"access_token": self.access_token(uid), "refresh_token": rt, "expires_in": self.access_ttl, "token_type": "bearer", "user": {"id": uid, "email": self.users[uid]["email"]}}

    # ------------------------------------------------------------------ state helpers for tests
    def add_user(self, email: str, password: str, role: str = "patient", confirmed: bool = True, full_name: str = "Test User") -> str:
        uid = str(uuid.uuid4())
        self.users[uid] = {"email": email.lower(), "password": password, "confirmed": confirmed, "banned": False, "role": role, "full_name": full_name}
        return uid

    def by_email(self, email: str):
        return next(((i, u) for i, u in self.users.items() if u["email"] == email.lower()), (None, None))

    def calls(self, path_part: str):
        return [c for c in self.call_log if path_part in c["path"]]

    # ------------------------------------------------------------------ routing
    def route(self, method, path, q, body, auth, apikey):
        if path == "/auth/v1/.well-known/jwks.json":
            return 200, self.jwks()
        if path.startswith("/rest/v1/profiles"):
            if apikey != SERVICE:
                return 401, {"message": "bad key"}
            uid = q.get("id", "").replace("eq.", "")
            u = self.users.get(unquote(uid))
            if method == "GET":
                return 200, ([{"id": uid, "role": u["role"], "full_name": u["full_name"]}] if u else [])
            if method == "PATCH" and u:
                u["role"] = body.get("role", u["role"])
                return 200, [{"id": uid, "role": u["role"]}]
            return 200, []
        if self.rate_limited and path.startswith("/auth/v1/") and "admin" not in path:
            return 429, {"error_code": "over_request_rate_limit", "msg": "rate limited"}
        if path == "/auth/v1/token":
            if apikey != ANON:
                return 401, {"msg": "bad key"}
            if q.get("grant_type") == "password":
                uid, u = self.by_email(body.get("email", ""))
                if not u or u["password"] != body.get("password"):
                    return 400, {"error_code": "invalid_credentials", "msg": "Invalid login credentials"}
                if u["banned"]:
                    return 400, {"error_code": "user_banned", "msg": "User is banned"}
                if not u["confirmed"] or self.confirm_required and not u["confirmed"]:
                    return 400, {"error_code": "email_not_confirmed", "msg": "Email not confirmed"}
                return 200, self.session(uid)
            if q.get("grant_type") == "refresh_token":
                uid = self.refresh.pop(body.get("refresh_token", ""), None)             # rotation: a refresh token works once
                if not uid:
                    return 400, {"error_code": "refresh_token_not_found", "msg": "Invalid Refresh Token"}
                if self.users[uid]["banned"]:
                    return 400, {"error_code": "user_banned", "msg": "banned"}
                return 200, self.session(uid)
        if path == "/auth/v1/signup":
            email = (body.get("email") or "").lower()
            if self.by_email(email)[0]:
                return 200, {"id": str(uuid.uuid4()), "email": email, "identities": []}      # obfuscated duplicate, like Supabase with confirmations on
            uid = self.add_user(email, body.get("password", ""), "patient", confirmed=False, full_name=(body.get("data") or {}).get("full_name", ""))   # role is NEVER taken from the request
            return 200, {"id": uid, "email": email}
        if path == "/auth/v1/recover":
            return 200, {}
        if path == "/auth/v1/logout":
            return 200, {}
        if path == "/auth/v1/user" and method == "PUT":
            try:
                claims = jwt.decode(auth[7:], self.key.public_key(), algorithms=["ES256"], audience="authenticated")
            except jwt.PyJWTError:
                return 401, {"msg": "bad token"}
            self.users[claims["sub"]]["password"] = body.get("password")
            return 200, {"id": claims["sub"]}
        if path == "/auth/v1/admin/users" and method == "POST":
            if apikey != SERVICE:
                return 401, {"msg": "bad key"}
            if self.by_email(body.get("email", ""))[0]:
                return 422, {"error_code": "email_exists", "msg": "exists"}
            uid = self.add_user(body["email"], body["password"], "patient", confirmed=bool(body.get("email_confirm")), full_name=(body.get("user_metadata") or {}).get("full_name", ""))
            return 200, {"id": uid, "email": body["email"].lower()}
        if path.startswith("/auth/v1/admin/users/"):
            if apikey != SERVICE:
                return 401, {"msg": "bad key"}
            uid = unquote(path.rsplit("/", 1)[1])
            u = self.users.get(uid)
            if not u:
                return 404, {"error_code": "user_not_found", "msg": "not found"}
            if method == "PUT":
                if "ban_duration" in body:
                    u["banned"] = body["ban_duration"] != "none"
                if "password" in body:
                    u["password"] = body["password"]
                return 200, {"id": uid}
            if method == "DELETE":
                self.users.pop(uid)
                return 200, {}
        return 404, {"msg": "no such route"}

"""Minimal Supabase Auth (GoTrue) client used by the backend.

The base URL and keys come only from operator configuration (Settings), never from a request, so there is no server-side request forgery surface here. Every call has a
timeout; error bodies are parsed for a machine-readable code but their text is never returned to clients. The anon key is public by design but is still kept out of
logs. The service-role key is used only by the admin_* methods and never leaves the server."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .redact import safe_exc
from .settings import Settings


class GoTrueError(Exception):
    """status 0 = network failure; `code` is GoTrue's error code (e.g. invalid_credentials, email_not_confirmed, weak_password, over_request_rate_limit)."""

    def __init__(self, status: int, code: str = "", detail: str = ""):
        super().__init__(f"GoTrue {status} {code}")
        self.status, self.code, self.detail = status, code, detail


@dataclass
class Session:
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: str


class GoTrue:
    def __init__(self, base_url: str, anon_key: str, service_key: str = "", timeout: int = 10):
        self.base, self.anon, self.service, self.timeout = base_url.rstrip("/"), anon_key, service_key, timeout

    @classmethod
    def from_settings(cls, st: Settings | None = None) -> "GoTrue":
        st = st or Settings.from_env()
        if not st.supabase_url or not st.supabase_anon_key:
            raise GoTrueError(0, "not_configured", "Supabase URL and anon key are required")
        return cls(st.supabase_url, st.supabase_anon_key, st.supabase_service_key)

    # ------------------------------------------------------------------ transport
    def _call(self, method: str, path: str, body: dict | None = None, bearer: str | None = None, query: dict | None = None, service: bool = False) -> dict:
        url = f"{self.base}/auth/v1/{path.lstrip('/')}" + (("?" + urllib.parse.urlencode(query)) if query else "")
        key = self.service if service else self.anon
        if service and not key:
            raise GoTrueError(0, "not_configured", "service-role key not configured")
        headers = {"apikey": key, "Content-Type": "application/json", "Authorization": "Bearer " + (bearer or key)}
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:                    # noqa: S310 - operator-configured project URL
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read() or b"{}")
            except ValueError:
                data = {}
            raise GoTrueError(e.code, str(data.get("error_code") or data.get("code") or data.get("error") or ""), str(data.get("msg") or data.get("message") or "")[:200]) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GoTrueError(0, "network", safe_exc(e)) from None

    @staticmethod
    def _session(d: dict) -> Session:
        if not d.get("access_token") or not d.get("refresh_token"):
            raise GoTrueError(0, "bad_response", "no session in response")
        return Session(d["access_token"], d["refresh_token"], int(d.get("expires_in") or 3600), (d.get("user") or {}).get("id", ""))

    # ------------------------------------------------------------------ user flows (anon key)
    def password_login(self, email: str, password: str) -> Session:
        return self._session(self._call("POST", "token", {"email": email, "password": password}, query={"grant_type": "password"}))

    def refresh(self, refresh_token: str) -> Session:
        return self._session(self._call("POST", "token", {"refresh_token": refresh_token}, query={"grant_type": "refresh_token"}))

    def signup(self, email: str, password: str, data: dict | None = None) -> dict:
        return self._call("POST", "signup", {"email": email, "password": password, "data": data or {}})

    def recover(self, email: str, redirect_to: str | None = None) -> dict:
        return self._call("POST", "recover", {"email": email}, query={"redirect_to": redirect_to} if redirect_to else None)

    def logout(self, access_token: str, scope: str = "local") -> None:
        self._call("POST", "logout", None, bearer=access_token, query={"scope": scope})

    def update_password(self, access_token: str, new_password: str) -> dict:
        return self._call("PUT", "user", {"password": new_password}, bearer=access_token)

    # ------------------------------------------------------------------ administration (service role, server only)
    def admin_create_user(self, email: str, password: str, full_name: str, confirm: bool = True) -> dict:
        return self._call("POST", "admin/users", {"email": email, "password": password, "email_confirm": confirm, "user_metadata": {"full_name": full_name}}, service=True)

    def admin_update_user(self, user_id: str, body: dict) -> dict:
        return self._call("PUT", f"admin/users/{urllib.parse.quote(user_id, safe='')}", body, service=True)

    def admin_ban(self, user_id: str, banned: bool) -> dict:
        return self.admin_update_user(user_id, {"ban_duration": "876000h" if banned else "none"})

    def admin_delete_user(self, user_id: str) -> None:
        self._call("DELETE", f"admin/users/{urllib.parse.quote(user_id, safe='')}", None, service=True)

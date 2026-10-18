"""Account lifecycle endpoints (see app/auth_flows.py for the security properties). Only available when COSMO_AUTH=supabase; the local development login stays in routers/patient.py."""
from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import auth_flows, supabase_auth

router = APIRouter(prefix="/api/auth")


def _supabase_only() -> None:
    if not supabase_auth.configured():
        raise HTTPException(404, "Not available.")


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


class RegisterBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    full_name: str = Field(max_length=200)


class ResetRequestBody(BaseModel):
    email: str = Field(max_length=254)
    redirect_to: str | None = Field(default=None, max_length=300)


class ResetConfirmBody(BaseModel):
    recovery_token: str = Field(max_length=4096)
    new_password: str = Field(max_length=256)


@router.get("/config")
def config():
    """Public, non-sensitive: tells the sign-in screen which form to show."""
    sb = supabase_auth.configured()
    return {"mode": "supabase" if sb else "local", "registration": bool(sb and auth_flows.registration_enabled()), "password_min": 12}


@router.post("/refresh")
def refresh(request: Request, response: Response):
    _supabase_only()
    auth_flows.require_csrf(request)                                 # a forged request must not be able to clear the real cookie
    try:
        return auth_flows.refresh(request, response)
    except HTTPException as e:                                       # headers set on the injected response are lost when raising, so the cookie clearing is carried explicitly
        out = JSONResponse({"detail": e.detail}, status_code=e.status_code, headers=e.headers)
        if e.status_code in (401, 403) and request.cookies.get(auth_flows.COOKIE):
            auth_flows.clear_refresh_cookie(out)
        return out


@router.post("/logout")
def logout(request: Request, response: Response, authorization: str = Header(default="")):
    if not supabase_auth.configured():
        return {"ok": True}                                           # local tokens are stateless; the client simply forgets them
    return auth_flows.logout(request, response, authorization)


@router.post("/register", status_code=202)
def register(body: RegisterBody, request: Request):
    _supabase_only()
    return auth_flows.register(body.email, body.password, body.full_name, _ip(request))


@router.post("/password-reset/request", status_code=202)
def reset_request(body: ResetRequestBody, request: Request):
    _supabase_only()
    return auth_flows.reset_request(body.email, _ip(request), body.redirect_to)


@router.post("/password-reset/confirm")
def reset_confirm(body: ResetConfirmBody):
    _supabase_only()
    return auth_flows.reset_confirm(body.recovery_token, body.new_password)

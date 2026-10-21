"""Clinic profile and appointment requests (app/booking). The patient side asks; the clinic side answers. Nothing here confirms a time by itself."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import consent, db
from ..booking import service
from ..security import current_user, require

router = APIRouter(prefix="/api/v1")
doctor = require("doctor", "admin")


def _wrap(fn):
    try:
        return fn()
    except service.BookingError as e:
        raise HTTPException(e.status, str(e)) from None


class ProfileIn(BaseModel):
    clinic_name: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=300)
    hours: str = Field(default="", max_length=300)
    booking_url: str = Field(default="", max_length=300)
    whatsapp: str = Field(default="", max_length=24)
    notes: str = Field(default="", max_length=500)


class RequestIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    preferred: str = Field(default="", max_length=200)
    service_id: str | None = Field(default=None, max_length=64)
    contact_method: str = Field(default="app", max_length=12)
    contact_detail: str = Field(default="", max_length=200)
    analysis_id: str | None = Field(default=None, max_length=64)
    share_summary: bool = False
    urgency: str = Field(default="routine", max_length=12)


class StaffUpdate(BaseModel):
    status: str = Field(max_length=16)
    confirmed_for: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=300)


@router.get("/clinic/profile")
def clinic_profile(user=Depends(current_user)):
    """What the clinic has said about itself (only what it filled in), the services it lists, and the honest state of availability."""
    return service.public_profile()


@router.put("/clinic/profile")
def set_clinic_profile(body: ProfileIn, user=Depends(doctor)):
    return _wrap(lambda: {"profile": service.set_profile(body.model_dump(), user["id"])})


@router.post("/appointments/requests")
def create_request(body: RequestIn, user=Depends(consent.patient_consented)):
    return _wrap(lambda: service.create_request(user, body.model_dump()))


@router.get("/appointments/requests")
def my_requests(user=Depends(require("patient"))):
    return {"requests": service.my_requests(user["id"])}


@router.post("/appointments/requests/{rid}/cancel")
def cancel_request(rid: str, user=Depends(require("patient"))):
    return _wrap(lambda: service.cancel_request(user["id"], rid))


@router.get("/clinic/appointments")
def queue(status: str | None = None, user=Depends(doctor)):
    if status and status not in service.STATUSES:
        raise HTTPException(422, "Unknown status.")
    return {"requests": service.staff_queue(status)}


@router.get("/clinic/appointments/{rid}/summary")
def summary(rid: str, user=Depends(doctor)):
    return _wrap(lambda: service.staff_summary(user["id"], rid))


@router.post("/clinic/appointments/{rid}")
def update(rid: str, body: StaffUpdate, user=Depends(doctor)):
    return _wrap(lambda: service.staff_update(user["id"], rid, body.status, confirmed_for=body.confirmed_for, note=body.note))

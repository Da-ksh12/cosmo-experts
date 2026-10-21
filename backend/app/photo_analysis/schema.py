"""Photo analysis v1: the typed, versioned contract between the backend and the app for ONE analysed photo.

Everything in a PhotoAnalysis comes from something the backend actually measured or resolved; nothing is filled in to look complete:
* observations about the PHOTO (focus, light, size, framing, covering, compression) carry the measured value and the threshold it was compared with;
* observations that point at a place on the photo carry real geometry measured by the backend (the face area found by the detector, a covered or glared region found by the quality check);
* an observation about the SKIN (kind "skin_feature") can only exist if an APPROVED model produced it (see builder.py); no such model exists today, so none is ever produced, and the
  payload says so under `capabilities`;
* a confidence is present only when a calibrated score exists. The rule-based checks have none and say so by omitting it.

COORDINATES. Every coordinate is a fraction (0 to 1) of the width or height of the stored photo AS DISPLAYED: the photo after its EXIF orientation has been applied (`transform` repeats that
size and the orientation). Origin top-left, x to the right, y downwards. The server never crops, rotates or resizes the stored photo, so these fractions stay valid for the file at
`image.url` at any display size, zoom or rotation (a viewer rotating the photo rotates the geometry with it).

The model is strict (unknown fields are refused) and every geometry is validated: finite numbers between 0 and 1, boxes with a positive size inside the photo, polygons of 3 to 64 points.
Nothing here is a storage path or a credential: `image.url` is an API path that needs the patient's own sign-in."""
from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

API_VERSION = "photo-analysis/1"
EPS = 1e-6

Status = Literal["queued", "processing", "completed", "uncertain", "rejected", "failed", "cancelled"]
Tone = Literal["ok", "attention", "info"]
ObsKind = Literal["photo_quality", "framing", "face_region", "skin_feature"]
Intent = Literal["measurements", "observation", "better_photo", "what_next", "summary", "compare", "capabilities", "care"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _unit(v: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not (0.0 <= v <= 1.0):
        raise ValueError("a coordinate must be a finite number between 0 and 1")
    return float(v)


# ---------------------------------------------------------------- geometry
class Point(Strict):
    type: Literal["point"] = "point"
    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def _xy(cls, v: float) -> float:
        return _unit(v)


class Box(Strict):
    type: Literal["box"] = "box"
    x: float
    y: float
    w: float
    h: float

    @field_validator("x", "y", "w", "h")
    @classmethod
    def _xywh(cls, v: float) -> float:
        return _unit(v)

    @model_validator(mode="after")
    def _inside(self) -> "Box":
        if self.w <= 0 or self.h <= 0 or self.x + self.w > 1 + EPS or self.y + self.h > 1 + EPS:
            raise ValueError("a box must have a positive size and lie inside the photo")
        return self


class Polygon(Strict):
    type: Literal["polygon"] = "polygon"
    points: list[tuple[float, float]] = Field(min_length=3, max_length=64)

    @field_validator("points")
    @classmethod
    def _pts(cls, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(_unit(x), _unit(y)) for x, y in pts]


Geometry = Annotated[Union[Point, Box, Polygon], Field(discriminator="type")]


def box_from_pixels(box_px: list[float] | tuple[float, ...], width: int, height: int) -> Box | None:
    """A pixel box [x, y, w, h] of the photo as displayed -> a validated normalised Box, or None when it is empty or outside the photo (never repaired into something plausible)."""
    try:
        x, y, w, h = (float(v) for v in box_px)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width + 0.5 or y + h > height + 0.5:
        return None
    x2, y2 = min(x + w, width), min(y + h, height)
    try:
        return Box(x=x / width, y=y / height, w=(x2 - x) / width, h=(y2 - y) / height)
    except ValueError:
        return None


def box_from_unit(box: list[float] | tuple[float, ...]) -> Box | None:
    """An already normalised [x, y, w, h] -> a validated Box, or None when it does not describe a region inside the photo."""
    try:
        x, y, w, h = (float(v) for v in box)
        return Box(x=x, y=y, w=min(w, 1 - x), h=min(h, 1 - y))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- parts
class CoordinateSystem(Strict):
    type: Literal["normalized"] = "normalized"
    origin: Literal["top-left"] = "top-left"
    x_axis: Literal["right"] = "right"
    y_axis: Literal["down"] = "down"
    reference: Literal["image_as_displayed"] = "image_as_displayed"
    note: str = ("Fractions (0 to 1) of the width and height of the stored photo as displayed, after its EXIF orientation is applied. "
                 "The server never crops, rotates or resizes the stored photo.")


class Transform(Strict):
    width: int = Field(ge=1, le=100000)             # pixels of the stored photo as displayed
    height: int = Field(ge=1, le=100000)
    exif_orientation: int | None = Field(default=None, ge=1, le=8)   # carried by the file; ALREADY applied to width, height and every coordinate
    server_modified: Literal[False] = False


class ImageRef(Strict):
    id: str | None = None
    kept: bool = False                                # rejected photos are not stored (data minimisation); a deleted photo is no longer kept
    url: str | None = None                            # an API path ("/api/images/<id>/file"): needs the patient's own sign-in, never a storage address
    thumb_url: str | None = None
    width: int | None = None
    height: int | None = None
    region: str
    region_label: str
    taken_at: float | None = None


class Evidence(Strict):
    text: str = Field(max_length=300)
    metric: str | None = None
    value: float | int | str | None = None
    threshold: str | None = None


class Confidence(Strict):
    value: float = Field(ge=0, le=1)
    calibrated: bool
    basis: str


class Provenance(Strict):
    capability: str
    version: str
    kind: Literal["rule_based", "model"]
    approval: str
    validated: bool
    note: str


class Observation(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,48}$")
    kind: ObsKind
    group: Literal["observe", "area"]
    label: str = Field(min_length=1, max_length=80)
    tone: Tone
    summary: str = Field(max_length=500)
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=6)
    confidence: Confidence | None = None
    geometry: Geometry | None = None
    provenance: int = Field(ge=0)
    validated: bool = False


class NextStep(Strict):
    id: str
    kind: Literal["retake", "add_photo", "clinician", "summary", "reports", "info"]
    label: str
    detail: str = ""
    route: str | None = None                           # an in-app route, e.g. "/photo?region=face"


class Question(Strict):
    id: str
    text: str = Field(max_length=200)
    intent: Intent
    observation_id: str | None = None


class CapabilityNote(Strict):
    task: str
    name: str
    available: bool
    status: str
    note: str


class Outcome(Strict):
    route: Literal["rejected", "document_pipeline", "image_models", "clinician_review"]
    verdict: Literal["accepted", "uncertain", "rejected"] | None = None
    reason: str | None = None                          # the photo check's own reason key (for example "too_dark"), when the photo was not accepted
    headline: str
    message: str
    clinician_review: bool = True


class ClinicianReview(Strict):
    status: Literal["pending", "reviewed", "not_applicable"]
    message: str


class Sections(Strict):
    observe: list[str] = Field(default_factory=list)      # observation ids: what can be observed about the photo
    areas: list[str] = Field(default_factory=list)        # observation ids that carry geometry: areas of interest on the photo
    meaning: list[str] = Field(default_factory=list)      # cautious statements about what the observations may mean
    cannot: list[str] = Field(default_factory=list)       # what the photo and this check cannot establish
    next_steps: list[NextStep] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)


class PhotoAnalysis(Strict):
    api_version: Literal["photo-analysis/1"] = API_VERSION
    id: str                                            # the analysis (session) id: also the id of the job that produced it
    conversation_id: str | None = None
    message_id: str | None = None
    index: int = 0                                     # position among the photos sent together
    status: Status
    stage: str | None = None                           # what the server is doing now, only while it is processing
    error: str | None = None
    created_at: float
    finished_at: float | None = None
    image: ImageRef | None = None
    coordinate_system: CoordinateSystem = Field(default_factory=CoordinateSystem)
    transform: Transform | None = None
    outcome: Outcome | None = None
    provenance: list[Provenance] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    sections: Sections = Field(default_factory=Sections)
    capabilities: list[CapabilityNote] = Field(default_factory=list)
    clinician_review: ClinicianReview | None = None
    disclaimers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "PhotoAnalysis":
        ids = [o.id for o in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("observation ids must be unique")
        known = set(ids)
        for name in ("observe", "areas"):
            if not set(getattr(self.sections, name)) <= known:
                raise ValueError(f"sections.{name} names an observation that does not exist")
        for o in self.observations:
            if o.provenance >= len(self.provenance):
                raise ValueError("an observation names a provenance entry that does not exist")
            if o.kind == "skin_feature" and not (self.provenance[o.provenance].kind == "model" and self.provenance[o.provenance].approval == "approved_for_defined_use"):
                raise ValueError("a skin_feature observation requires an approved model as its provenance")
        if not {q.observation_id for q in self.sections.questions if q.observation_id} <= known:
            raise ValueError("a question names an observation that does not exist")
        if self.image and self.image.width and self.transform and (self.image.width, self.image.height) != (self.transform.width, self.transform.height):
            raise ValueError("image size and transform size disagree")
        return self

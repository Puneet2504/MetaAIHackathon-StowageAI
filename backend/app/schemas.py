"""Request and response models for the API."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TankLoadIn(BaseModel):
    tank_id: str
    volume_m3: float = Field(ge=0)
    density_t_m3: float = Field(gt=0.3, lt=2.0)
    grade: str | None = None


class ConditionIn(BaseModel):
    vessel_id: str = "demo_mr2"
    loads: list[TankLoadIn]
    include_constants: bool = True


class SequenceIn(BaseModel):
    vessel_id: str = "demo_mr2"
    stages: list[list[TankLoadIn]]


class Parcel(BaseModel):
    grade: str
    quantity_t: float | None = None
    quantity_m3: float | None = None
    quantity_bbl: float | None = None
    mass_basis: Literal["air", "vacuum"] | None = None   # what the tonnes mean; None = in air
    density_t_m3: float | None = None
    density_temp_c: float | None = None                  # temperature the density was measured at; None = 15 °C
    api: float | None = None
    temp_c: float | None = None                          # cargo temperature at loading
    load_port: str | None = None
    discharge_port: str | None = None


class ParcelUpdate(BaseModel):
    """A change to the cargo's conditions asked for when replanning ("cargo temperature 35 C")."""
    grade: str | None = None  # None = every parcel
    temp_c: float | None = None
    density_t_m3: float | None = None
    density_temp_c: float | None = None


class ParseOrderIn(BaseModel):
    text: str = Field(min_length=10, max_length=8000)


class ParseOrderOut(BaseModel):
    parcels: list[Parcel]
    notes: list[str] = []


class ExplainIn(BaseModel):
    result: dict
    question: str | None = None


class CleaningIn(BaseModel):
    cleaning: dict  # the plan's cleaning report (plan.cleaning)


class ExplainOut(BaseModel):
    explanation: str
    source: Literal["ai", "plain"] = "ai"


# ---------- user ship layouts ----------

TankKind = Literal["cargo", "slop", "ballast", "fuel_oil", "diesel_oil", "fresh_water"]


class LayoutTank(BaseModel):
    id: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_-]+$")
    name: str | None = Field(default=None, max_length=80)
    type: TankKind
    side: Literal["P", "S", "C"]
    xAft: float = Field(ge=0)
    xFwd: float = Field(gt=0)
    capacity100: float | None = Field(default=None, gt=0)
    coating: str | None = Field(default=None, max_length=40)
    lineId: str | None = Field(default=None, max_length=16)
    # Optional geometry overrides; the builder uses type defaults when left out.
    yIn: float | None = None
    yOut: float | None = None
    zBottom: float | None = Field(default=None, ge=0)
    zTop: float | None = Field(default=None, gt=0)
    shapeFactor: float | None = Field(default=None, gt=0.2, le=1.0)
    maxFillPct: float | None = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def _extent(self):
        if self.xFwd <= self.xAft:
            raise ValueError(f"{self.id}: xFwd must be forward of xAft")
        if self.zTop is not None and self.zBottom is not None and self.zTop <= self.zBottom:
            raise ValueError(f"{self.id}: zTop must be above zBottom")
        return self


class VesselLayout(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sizeClass: Literal["MR1", "MR2", "LR1", "LR2", "Handysize", "Other"] = "Other"
    lbp: float = Field(ge=40, le=400)
    breadth: float = Field(ge=8, le=70)
    depth: float = Field(ge=4, le=40)
    summerDraught: float = Field(ge=2, le=30)
    doubleBottom: float | None = Field(default=None, ge=0.5, le=5)
    doubleSide: float | None = Field(default=None, ge=0.5, le=5)
    loa: float | None = Field(default=None, ge=40, le=420)  # shown only; the builder uses lbp
    tanks: list[LayoutTank] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def _check(self):
        if self.loa is not None and self.loa < self.lbp:
            raise ValueError("LOA must be at least the LBP")
        if self.summerDraught >= self.depth:
            raise ValueError("Summer draught must be less than depth")
        ids = [t.id for t in self.tanks]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Duplicate tank ids: {', '.join(dupes)}")
        if not any(t.type == "cargo" for t in self.tanks):
            raise ValueError("At least one cargo tank is needed")
        for t in self.tanks:
            if t.xFwd > self.lbp:
                raise ValueError(f"{t.id}: xFwd {t.xFwd} m is beyond the LBP of {self.lbp} m")
            if t.zTop is not None and t.zTop > self.depth:
                raise ValueError(f"{t.id}: zTop is above the depth")
            for y in (t.yIn, t.yOut):
                if y is not None and abs(y) > self.breadth / 2:
                    raise ValueError(f"{t.id}: y offset is outside the half breadth")
        # Tanks of the same type on the same side must not overlap lengthwise.
        for a in self.tanks:
            for b in self.tanks:
                if (a.id < b.id and a.side == b.side and a.type == b.type and a.type in ("cargo", "slop")
                        and a.xAft < b.xFwd - 1e-6 and b.xAft < a.xFwd - 1e-6):
                    raise ValueError(f"{a.id} and {b.id} overlap")
        return self


class SignInIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)
    create: bool = False


class ReadDrawingIn(BaseModel):
    text: str | None = Field(default=None, max_length=8000)
    image: str | None = Field(default=None, max_length=7_000_000)  # data URL, ~5 MB of image

    @model_validator(mode="after")
    def _one(self):
        if not (self.text and self.text.strip()) and not self.image:
            raise ValueError("Paste some text or add an image")
        return self


class Segment(BaseModel):
    label: str
    box: list[float]  # x0, y0, x1, y1, normalised 0 to 1
    score: float | None = None


class ReadDrawingOut(BaseModel):
    layout: dict  # may contain nulls the user must fill in before saving
    notes: list[str] = []
    segments: list[Segment] = []
    checks: list[str] = []      # rule-based checks (the AI's own notes are in `notes`)
    fixes: list[dict] = []      # automatic corrections: {tank, field, old, new, reason}
    estimated: list[str] = []   # fields filled in by estimate, e.g. "lbp"


class LayoutIn(BaseModel):
    layout: VesselLayout


# ---------- stowage ----------

class ConstraintsIn(BaseModel):
    empty_tanks: list[str] = []
    fixed_grades: dict[str, str] = {}
    max_trim_m: float | None = Field(default=None, gt=0, le=10)
    fill: Literal["full", "even"] | None = None  # "even": spread each grade over all its tanks at the same fill %


class PlanIn(BaseModel):
    vessel_id: str = "demo_mr2"
    order_text: str | None = Field(default=None, max_length=8000)
    parcels: list[Parcel] | None = None
    instructions: str | None = Field(default=None, max_length=1000)
    constraints: ConstraintsIn = ConstraintsIn()
    memory_off: list[str] = []  # remembered items to leave out of this plan only

    @model_validator(mode="after")
    def _source(self):
        if not self.parcels and not (self.order_text and len(self.order_text.strip()) >= 10):
            raise ValueError("Paste a voyage order or give parcels")
        return self


class SpaceSuggestion(BaseModel):
    """Extra cargo the AI suggests a charterer could book on a ship's current voyage."""
    port: str
    discharge_port: str
    grade: str
    tanks: list[str] = Field(min_length=1)
    quantity_m3: float = Field(gt=0)
    quantity_t: float = Field(gt=0)
    cleaning: str
    reason: str
    checks: list[str] = []


class OpenSpaceOut(BaseModel):
    summary: str
    suggestions: list[SpaceSuggestion] = []


class AddToVoyageIn(BaseModel):
    """An open-space suggestion to add to the ship's current voyage."""
    suggestion: SpaceSuggestion
    order_line: str | None = Field(default=None, max_length=300)


class VoyageCheckIn(BaseModel):
    """A hand-edited condition to check (or approve) against the order's ports."""
    vessel_id: str
    order_text: str | None = Field(default=None, max_length=8000)
    parcels: list[Parcel]
    loads: list[TankLoadIn]


class ApproveIn(BaseModel):
    vessel_id: str
    order_text: str | None = Field(default=None, max_length=8000)
    parcels: list[Parcel]
    constraints: ConstraintsIn = ConstraintsIn()
    plan: dict
    explanation: str | None = Field(default=None, max_length=4000)


MemoryKind = Literal["constraint_empty", "constraint_fixed", "max_trim", "fill_mode", "grade_alias", "note", "explain_style"]


class MemoryOp(BaseModel):
    op: Literal["set", "forget"]
    scope: Literal["vessel", "global"] = "global"
    kind: MemoryKind
    key: str | None = Field(default=None, max_length=60)
    value: str | float | None = None
    text: str = Field(default="", max_length=300)


class InstructionOut(BaseModel):
    constraints: ConstraintsIn
    notes: list[str] = []
    memory: list[MemoryOp] = []
    parcel_updates: list[ParcelUpdate] = []


class MemoryNoteIn(BaseModel):
    text: str = Field(min_length=2, max_length=300)
    vessel_id: str | None = None


class MemoryPatchIn(BaseModel):
    text: str | None = Field(default=None, min_length=2, max_length=300)
    value: str | float | None = None


class DrawingReadOut(BaseModel):
    layout: dict  # checked strictly only when the user saves it
    notes: list[str] = []       # from the model, minus those a fix has made stale
    checks: list[str] = []      # from the rule-based checks
    fixes: list[dict] = []
    estimated: list[str] = []

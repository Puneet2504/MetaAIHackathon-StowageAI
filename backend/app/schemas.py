"""Request and response models for the API."""
from pydantic import BaseModel, Field


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
    density_t_m3: float | None = None
    load_port: str | None = None
    discharge_port: str | None = None


class ParseOrderIn(BaseModel):
    text: str = Field(min_length=10, max_length=8000)


class ParseOrderOut(BaseModel):
    parcels: list[Parcel]
    notes: list[str] = []


class ExplainIn(BaseModel):
    result: dict
    question: str | None = None


class ExplainOut(BaseModel):
    explanation: str

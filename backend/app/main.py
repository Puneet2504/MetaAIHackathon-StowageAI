"""FastAPI app. Start from the backend folder:  uvicorn app.main:app --reload"""
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from . import config, vessels
from .engine.stability import StabilityError, TankLoad, calculate, calculate_sequence
from .llm import client, prompts
from .schemas import (ConditionIn, ExplainIn, ExplainOut, ParseOrderIn, ParseOrderOut,
                      SequenceIn)

app = FastAPI(title="Product Tanker Planner", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[config.FRONTEND_ORIGIN],
                   allow_methods=["*"], allow_headers=["*"])


def _vessel(vessel_id: str) -> dict:
    v = vessels.get(vessel_id)
    if v is None:
        raise HTTPException(404, f"Vessel '{vessel_id}' not found")
    return v


def _loads(items) -> list[TankLoad]:
    return [TankLoad(i.tank_id, i.volume_m3, i.density_t_m3, i.grade) for i in items]


@app.get("/api/health")
def health():
    return {"status": "ok", "modelConfigured": config.model_configured()}


@app.get("/api/vessels")
def list_vessels():
    return [{"id": v["id"], "name": v["particulars"]["name"], "sizeClass": v["particulars"]["sizeClass"],
             "origin": v["source"]["origin"]} for v in vessels.load_all().values()]


@app.get("/api/vessels/{vessel_id}")
def get_vessel(vessel_id: str):
    return _vessel(vessel_id)


@app.post("/api/stability/check")
def check(body: ConditionIn):
    try:
        return calculate(_vessel(body.vessel_id), _loads(body.loads), body.include_constants).to_dict()
    except StabilityError as e:
        raise HTTPException(422, str(e))


@app.post("/api/stability/sequence")
def sequence(body: SequenceIn):
    try:
        results = calculate_sequence(_vessel(body.vessel_id), [_loads(s) for s in body.stages])
    except StabilityError as e:
        raise HTTPException(422, str(e))
    return {"stages": [r.to_dict() for r in results],
            "allStagesPassed": all(r.all_passed for r in results)}


@app.post("/api/ai/parse-order", response_model=ParseOrderOut)
def parse_order(body: ParseOrderIn):
    try:
        data = client.chat_json(prompts.PARSE_ORDER_SYSTEM, body.text)
        return ParseOrderOut.model_validate(data)
    except client.ModelNotConfigured as e:
        raise HTTPException(503, str(e))
    except (client.ModelError, ValidationError) as e:
        raise HTTPException(502, f"Could not read the order: {e}")


@app.post("/api/ai/explain", response_model=ExplainOut)
def explain(body: ExplainIn):
    user = json.dumps(body.result)
    if body.question:
        user += f"\n\nPlanner's question: {body.question}"
    try:
        return ExplainOut(explanation=client.chat_text(prompts.EXPLAIN_SYSTEM, user))
    except client.ModelNotConfigured as e:
        raise HTTPException(503, str(e))
    except client.ModelError as e:
        raise HTTPException(502, str(e))

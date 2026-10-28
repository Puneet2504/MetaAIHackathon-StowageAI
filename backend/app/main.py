"""FastAPI app. Start from the backend folder:  uvicorn app.main:app --reload"""
import base64
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config, memory, users, vessels
from .engine.stability import StabilityError, TankLoad, calculate, calculate_sequence
from .llm import client, plain, prompts, segment, tasks, usage
from .llm.digest import stability_digest
from .schemas import (ApproveIn, ConditionIn, ExplainIn, ExplainOut, LayoutIn, MemoryNoteIn, MemoryPatchIn,
                      ParseOrderIn, ParseOrderOut, PlanIn, ReadDrawingIn, ReadDrawingOut, SequenceIn, SignInIn)
from .stowage import pipeline
from .users import current_user
from .vessel_builder import build_vessel, layout_from_vessel


@asynccontextmanager
async def lifespan(_app: FastAPI):
    users.ensure_demo_user()
    yield


app = FastAPI(title="Product Tanker Planner", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[config.FRONTEND_ORIGIN],
                   allow_methods=["*"], allow_headers=["*"])

IMAGE_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _vessel(vessel_id: str, user: str | None = None) -> dict:
    v = vessels.get(vessel_id, user)
    if v is None:
        raise HTTPException(404, f"Vessel '{vessel_id}' not found")
    return v


def _loads(items) -> list[TankLoad]:
    return [TankLoad(i.tank_id, i.volume_m3, i.density_t_m3, i.grade) for i in items]


def _summary(v: dict, user: str) -> dict:
    return {"id": v["id"], "name": v["particulars"]["name"], "sizeClass": v["particulars"]["sizeClass"],
            "origin": v["source"]["origin"], "owned": vessels.is_owned(v["id"], user),
            "cargoTanks": sum(1 for t in v["tanks"] if t["type"] == "cargo")}


def _build(body: LayoutIn, vessel_id: str) -> dict:
    v = build_vessel(body.layout, vessel_id)
    v["layout"] = body.layout.model_dump(exclude_none=True)
    return v


@app.get("/api/health")
def health():
    return {"status": "ok", "modelConfigured": config.model_configured(),
            "segmentConfigured": config.segment_configured(), "demoEmail": config.DEMO_USER_EMAIL or None}


@app.post("/api/auth/signin")
def sign_in(body: SignInIn):
    """Sign in, or register when the email is new and the person confirmed it (create=true)."""
    try:
        return users.sign_in(body.email, body.password, body.create)
    except users.AuthError as e:
        raise HTTPException(e.status, str(e))


@app.get("/api/auth/me")
def me(user: str = Depends(current_user)):
    email = users.email_of(user)
    if email is None:
        raise HTTPException(401, "Your session has ended. Sign in again.")
    return {"email": email}


@app.get("/api/vessels")
def list_vessels(user: str = Depends(current_user)):
    return [_summary(v, user) for v in vessels.list_for(user)]


@app.get("/api/vessels/{vessel_id}")
def get_vessel(vessel_id: str, user: str = Depends(current_user)):
    return _vessel(vessel_id, user)


@app.get("/api/vessels/{vessel_id}/layout")
def get_layout(vessel_id: str, user: str = Depends(current_user)):
    return layout_from_vessel(_vessel(vessel_id, user))


@app.post("/api/vessels/preview")
def preview_vessel(body: LayoutIn, user: str = Depends(current_user)):
    return _build(body, "preview")


@app.post("/api/vessels", status_code=201)
def create_vessel(body: LayoutIn, user: str = Depends(current_user)):
    v = _build(body, vessels.new_id(body.layout.name))
    vessels.save(user, v)
    return v


@app.put("/api/vessels/{vessel_id}")
def update_vessel(vessel_id: str, body: LayoutIn, user: str = Depends(current_user)):
    if not vessels.is_owned(vessel_id, user):
        raise HTTPException(403 if vessels.get(vessel_id) else 404, "You can only change your own ships")
    v = _build(body, vessel_id)
    vessels.save(user, v)
    return v


@app.delete("/api/vessels/{vessel_id}", status_code=204)
def delete_vessel(vessel_id: str, user: str = Depends(current_user)):
    if not vessels.delete(user, vessel_id):
        raise HTTPException(403 if vessels.get(vessel_id) else 404, "You can only delete your own ships")


@app.post("/api/stability/check")
def check(body: ConditionIn, user: str = Depends(current_user)):
    try:
        return calculate(_vessel(body.vessel_id, user), _loads(body.loads), body.include_constants).to_dict()
    except StabilityError as e:
        raise HTTPException(422, str(e))


@app.post("/api/stability/sequence")
def sequence(body: SequenceIn, user: str = Depends(current_user)):
    try:
        results = calculate_sequence(_vessel(body.vessel_id, user), [_loads(s) for s in body.stages])
    except StabilityError as e:
        raise HTTPException(422, str(e))
    return {"stages": [r.to_dict() for r in results],
            "allStagesPassed": all(r.all_passed for r in results)}


@app.post("/api/stowage/plan")
def plan_voyage(body: PlanIn, user: str = Depends(current_user)):
    """Order in, checked plan out: AI reads, the solver allocates, the engine checks every candidate."""
    vessel = _vessel(body.vessel_id, user)
    try:
        with usage.track(user):
            return pipeline.plan_voyage(vessel, body.order_text, body.parcels, body.instructions, body.constraints,
                                        user=user, memory_off=body.memory_off)
    except client.ModelNotConfigured as e:
        raise HTTPException(503, str(e))
    except client.ModelError as e:
        raise HTTPException(502, str(e))


@app.post("/api/stowage/plans", status_code=201)
def approve_plan(body: ApproveIn, user: str = Depends(current_user)):
    vessel = _vessel(body.vessel_id, user)
    return pipeline.approve(user, body.model_dump(), vessel)


@app.get("/api/stowage/plans")
def list_plans(vessel_id: str | None = None, user: str = Depends(current_user)):
    return pipeline.list_approved(user, vessel_id)


# ---------- voyages (approved plans) ----------

@app.get("/api/voyages")
def list_voyages(vessel_id: str | None = None, user: str = Depends(current_user)):
    return pipeline.list_voyages(user, vessel_id)


@app.get("/api/voyages/{voyage_id}")
def get_voyage(voyage_id: str, user: str = Depends(current_user)):
    v = pipeline.get_voyage(user, voyage_id)
    if v is None:
        raise HTTPException(404, "Voyage not found")
    v["vessel"] = vessels.get(v["vessel_id"], user)  # None if the ship was deleted since
    return v


@app.delete("/api/voyages/{voyage_id}", status_code=204)
def delete_voyage(voyage_id: str, user: str = Depends(current_user)):
    if not pipeline.delete_voyage(user, voyage_id):
        raise HTTPException(404, "Voyage not found")


# ---------- memory ----------

@app.get("/api/memory")
def list_memory(user: str = Depends(current_user)):
    names = {v["id"]: v["particulars"]["name"] for v in vessels.list_for(user)}
    return [i | {"scope_name": "All ships" if i["scope"] == "global" else names.get(i["scope"], i["scope"])}
            for i in memory.load(user)]


@app.post("/api/memory", status_code=201)
def add_memory(body: MemoryNoteIn, user: str = Depends(current_user)):
    if body.vessel_id:
        _vessel(body.vessel_id, user)
    return memory.add_note(user, body.text, body.vessel_id)


@app.patch("/api/memory/{item_id}")
def patch_memory(item_id: str, body: MemoryPatchIn, user: str = Depends(current_user)):
    try:
        item = memory.patch(user, item_id, body.text, body.value)
    except (TypeError, ValueError):
        raise HTTPException(422, "That value does not fit this memory item")
    if item is None:
        raise HTTPException(404, "Memory item not found")
    return item


@app.delete("/api/memory/{item_id}", status_code=204)
def delete_memory(item_id: str, user: str = Depends(current_user)):
    if not memory.delete(user, item_id):
        raise HTTPException(404, "Memory item not found")


@app.delete("/api/memory", status_code=204)
def clear_memory(user: str = Depends(current_user)):
    memory.delete(user)


@app.get("/api/usage")
def get_usage(user: str = Depends(current_user)):
    return usage.summary(user)


# ---------- AI helpers ----------

@app.post("/api/ai/parse-order", response_model=ParseOrderOut)
def parse_order(body: ParseOrderIn, user: str = Depends(current_user)):
    try:
        with usage.track(user):
            return tasks.read_order(body.text, memory_lines=memory.context_lines(user, None, ("grade_alias", "note")))
    except client.ModelNotConfigured as e:
        raise HTTPException(503, str(e))
    except client.ModelError as e:
        raise HTTPException(502, f"Could not read the order: {e}")


@app.post("/api/ai/read-drawing", response_model=ReadDrawingOut)
def read_drawing(body: ReadDrawingIn, user: str = Depends(current_user)):
    if body.image:
        m = IMAGE_RE.match(body.image)
        if not m:
            raise HTTPException(422, "Image must be a PNG, JPEG or WebP data URL")
        if len(base64.b64decode(m.group(2), validate=False)) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Image is larger than 5 MB")
    try:
        if not config.model_configured():
            raise client.ModelNotConfigured(
                "No model configured. Set MODEL_BASE_URL, MODEL_API_KEY and READER_MODEL_NAME in backend/.env")
        with usage.track(user):
            segments = segment.segment(body.image) if body.image else []
            out = tasks.read_drawing(body.text, body.image, segments)
        return ReadDrawingOut(layout=out.layout, notes=out.notes, segments=segments, checks=out.checks, fixes=out.fixes,
                              estimated=out.estimated)
    except client.ModelNotConfigured as e:
        raise HTTPException(503, str(e))
    except client.ModelError as e:
        raise HTTPException(502, f"Could not read the drawing: {e}")


@app.post("/api/ai/explain", response_model=ExplainOut)
def explain(body: ExplainIn, user: str = Depends(current_user)):
    """Plain-language explanation. Falls back to the built-in one when the model is unavailable."""
    d = stability_digest(body.result)
    if config.model_configured():
        try:
            with usage.track(user):
                text = tasks.explain(prompts.EXPLAIN_SYSTEM, d, memory.context_lines(user, None, ("explain_style",)),
                                     body.question)
            return ExplainOut(explanation=text, source="ai")
        except client.ModelError:
            pass
    return ExplainOut(explanation=plain.explain_stability(d), source="plain")

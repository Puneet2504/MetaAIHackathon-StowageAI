"""Order to plan in one call: AI reads the order and instructions, the solver and engine plan, AI explains.

Remembered rules (memory.py) are applied by code before solving, so they cost no tokens.
Approved plans are kept per user and listed as voyages.
"""
import json
import re
import secrets
from datetime import datetime, timezone

from .. import config, memory, vessels
from ..llm import client, plain, prompts, tasks
from ..llm.digest import plan_digest
from ..schemas import ConstraintsIn
from .solver import Constraints, solve


def _merge(session: ConstraintsIn, remembered: dict) -> ConstraintsIn:
    """This plan's constraints win; remembered ones fill in around them."""
    fixed = {**remembered["fixed_grades"], **session.fixed_grades}
    empty = sorted((set(session.empty_tanks) | set(remembered["empty_tanks"])) - set(session.fixed_grades))
    trim = session.max_trim_m if session.max_trim_m is not None else remembered["max_trim_m"]
    return ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim)


def _explain(digest: dict, lines: list[str]) -> tuple[str, str]:
    """AI explanation in plain words, or the built-in one when the model is unavailable."""
    if config.model_configured():
        try:
            return tasks.explain(prompts.EXPLAIN_PLAN_SYSTEM, digest, lines), "ai"
        except client.ModelError:
            pass
    return plain.explain_plan(digest), "plain"


def plan_voyage(vessel: dict, order_text: str | None, parcels, instructions: str | None,
                constraints: ConstraintsIn, user: str | None = None, memory_off: list[str] = ()) -> dict:
    notes: list[str] = []
    def lines(*kinds):
        return memory.context_lines(user, vessel["id"], kinds) if user else []

    if not parcels:  # raises ModelNotConfigured without a model
        out = tasks.read_order(order_text, memory_lines=lines("grade_alias", "note"))
        parcels, notes = out.parcels, list(out.notes)

    remembered_now, forgotten_now = [], []
    if instructions and instructions.strip():
        cargo = [t["id"] for t in vessel["tanks"] if t["type"] == "cargo"]
        out = tasks.read_instruction(instructions.strip(), cargo, sorted({p.grade for p in parcels}),
                                     constraints.model_dump(), memory_lines=lines("grade_alias", "note"))
        ids = {t["id"] for t in vessel["tanks"]}
        constraints = ConstraintsIn(empty_tanks=[t for t in out.constraints.empty_tanks if t in ids],
                                    fixed_grades={t: g for t, g in out.constraints.fixed_grades.items() if t in ids},
                                    max_trim_m=out.constraints.max_trim_m)
        notes += out.notes
        if user and out.memory:
            remembered_now, forgotten_now = memory.apply_ops(user, vessel["id"], out.memory)

    from_memory, used = ({"empty_tanks": [], "fixed_grades": {}, "max_trim_m": None}, [])
    if user:
        from_memory, used = memory.constraints_for(user, vessel, set(memory_off))
        released = [t for t in from_memory["empty_tanks"] if instructions and re.search(
            rf"\b(use|release)\b.*\b{re.escape(t[3:-1])}\b", instructions, re.I) and t not in constraints.empty_tanks]
        if released:
            notes.append(f"{', '.join(released)} stay empty because of a remembered rule. "
                         f"Say 'forget the tank {released[0][3:-1]} rule' to remove it, or switch it off below.")
        memory.mark_used(user, [i["id"] for i in used])
    merged = _merge(constraints, from_memory)

    plan = solve(vessel, parcels, Constraints(list(merged.empty_tanks), dict(merged.fixed_grades), merged.max_trim_m),
                 aliases=memory.aliases(user) if user else None)
    digest = plan_digest(plan)
    explanation, source = _explain(digest, lines("explain_style"))
    return {"parcels": [p.model_dump() for p in parcels], "parcel_notes": notes,
            "constraints": constraints.model_dump(), "applied_constraints": merged.model_dump(),
            "memory_used": used, "remembered": remembered_now, "forgotten": forgotten_now,
            "plan": plan, "explanation": explanation, "explanation_source": source}


# ---------- approved plans, kept per user and shown as voyages ----------

def _plan_dir(user: str):
    return config.USER_DIR / user / "plans"


def voyage_summary(record: dict) -> dict:
    """Route, grades and tonnes for a voyage card, from the parcels and the allocation."""
    parcels = record.get("parcels") or []
    plan = record.get("plan") or {}
    load_ports = list(dict.fromkeys(p["load_port"] for p in parcels if p.get("load_port")))
    dis_ports = list(dict.fromkeys(p["discharge_port"] for p in parcels if p.get("discharge_port")))
    grades = [{"grade": a["grade"], "grade_id": a["grade_id"],
               "tonnes": round(a["allocated_m3"] * a["density_t_m3"])} for a in plan.get("allocation", [])]
    r = plan.get("result") or {}
    return {"load_ports": load_ports, "discharge_ports": dis_ports, "grades": grades,
            "total_t": sum(g["tonnes"] for g in grades), "gm_m": r.get("gm_fluid_m"), "trim_m": r.get("trim_m"),
            "passed": bool(r.get("all_passed")), "status": plan.get("status")}


def approve(user: str, record: dict, vessel: dict | None = None) -> dict:
    vessel = vessel or vessels.get(record["vessel_id"], user) or {}
    record = {"id": secrets.token_hex(4), "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "approved_by": user, "vessel_name": (vessel.get("particulars") or {}).get("name"),
              "size_class": (vessel.get("particulars") or {}).get("sizeClass"), **record}
    if not record.get("explanation") and record.get("plan"):
        record["explanation"] = plain.explain_plan(plan_digest(record["plan"]))
    record["voyage"] = voyage_summary(record)
    vessels.write_json(_plan_dir(user) / f"{record['id']}.json", record)
    return record


def _records(user: str) -> list[dict]:
    out = []
    for path in _plan_dir(user).glob("*.json"):
        r = json.loads(path.read_text(encoding="utf-8"))
        if "voyage" not in r:  # approved before voyages existed
            r["voyage"] = voyage_summary(r)
        if not r.get("explanation") and r.get("plan"):
            r["explanation"] = plain.explain_plan(plan_digest(r["plan"]))
        if not r.get("vessel_name"):
            v = vessels.get(r.get("vessel_id", ""), user) or {}
            r["vessel_name"] = (v.get("particulars") or {}).get("name", r.get("vessel_id"))
            r["size_class"] = (v.get("particulars") or {}).get("sizeClass")
        out.append(r)
    return sorted(out, key=lambda r: r["approved_at"], reverse=True)


def list_voyages(user: str, vessel_id: str | None = None) -> list[dict]:
    return [{k: r.get(k) for k in ("id", "vessel_id", "vessel_name", "size_class", "approved_at", "order_text", "voyage")}
            for r in _records(user) if vessel_id is None or r.get("vessel_id") == vessel_id]


def get_voyage(user: str, voyage_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{8}", voyage_id):
        return None
    return next((r for r in _records(user) if r["id"] == voyage_id), None)


def delete_voyage(user: str, voyage_id: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{8}", voyage_id):
        return False
    path = _plan_dir(user) / f"{voyage_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def list_approved(user: str, vessel_id: str | None = None) -> list[dict]:
    """Older shape used by the planner's 'Approved plans' list."""
    return [{"id": v["id"], "vessel_id": v["vessel_id"], "approved_at": v["approved_at"], "order_text": v["order_text"],
             "status": v["voyage"]["status"], "grades": [g["grade"] for g in v["voyage"]["grades"]]}
            for v in list_voyages(user, vessel_id)]

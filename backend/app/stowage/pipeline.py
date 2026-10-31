"""Order to plan in one call: AI reads the order and instructions, the solver and engine plan, AI explains.

Remembered rules (memory.py) are applied by code before solving, so they cost no tokens.
A port where one grade is discharged and another loaded is planned as a later stage that reuses the
emptied tanks; every change of grade in a tank (from the ship's last voyage, or at such a port) is
reported with the cleaning it needs (cleaning.py).
Approved plans are kept per user and listed as voyages.
"""
import json
import re
import secrets
from datetime import datetime, timezone

from .. import config, memory, vessels
from ..llm import client, plain, prompts, tasks
from ..llm.digest import plan_digest
from ..engine.stability import StabilityError, TankLoad, calculate
from ..schemas import ConstraintsIn
from . import cleaning, voyage_check
from .grades import GRADES, match_grade
from .solver import Constraints, solve


def _merge(session: ConstraintsIn, remembered: dict) -> ConstraintsIn:
    """This plan's constraints win; remembered ones fill in around them."""
    fixed = {**remembered["fixed_grades"], **session.fixed_grades}
    empty = sorted((set(session.empty_tanks) | set(remembered["empty_tanks"])) - set(session.fixed_grades))
    trim = session.max_trim_m if session.max_trim_m is not None else remembered["max_trim_m"]
    fill = session.fill or remembered.get("fill")
    return ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim, fill=fill)


def _apply_updates(parcels, updates) -> tuple[list, list[str]]:
    """Cargo temperature or density given when replanning, applied to the parcels it names (or all)."""
    notes = []
    for u in updates:
        hit = [p for p in parcels if u.grade is None or p.grade.lower() == u.grade.lower()]
        change = {k: v for k, v in (("temp_c", u.temp_c), ("density_t_m3", u.density_t_m3),
                                     ("density_temp_c", u.density_temp_c)) if v is not None}
        if u.density_t_m3 is not None:
            change.setdefault("density_temp_c", None)
            change["api"] = None
        if not hit or not change:
            continue
        parcels = [p.model_copy(update=change) if p in hit else p for p in parcels]
        what = (f"temperature {u.temp_c:g} °C" if u.temp_c is not None else "") + \
               (f"{' and ' if u.temp_c is not None else ''}density {u.density_t_m3:g}" if u.density_t_m3 is not None else "")
        notes.append(f"Using cargo {what} for {', '.join(p.grade for p in hit)}.")
    return parcels, notes


def _explain(digest: dict, lines: list[str]) -> tuple[str, str]:
    """AI explanation in plain words, or the built-in one when the model is unavailable."""
    if config.model_configured():
        try:
            text = tasks.explain(prompts.EXPLAIN_PLAN_SYSTEM, digest, lines)
            # The model may leave assumed conditions out to keep to two bullets; they always matter, so add them.
            if digest.get("assumed") and not re.search(r"assum|cargo document|typical value", text, re.I):
                text = text.rstrip() + "\n- " + plain.assumed_note(digest)
            return text, "ai"
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
                                     constraints.model_dump(), memory_lines=lines("grade_alias", "note"),
                                     aliases=memory.aliases(user) if user else None)
        ids = {t["id"] for t in vessel["tanks"]}
        constraints = ConstraintsIn(empty_tanks=[t for t in out.constraints.empty_tanks if t in ids],
                                    fixed_grades={t: g for t, g in out.constraints.fixed_grades.items() if t in ids},
                                    max_trim_m=out.constraints.max_trim_m, fill=out.constraints.fill)
        notes += out.notes
        parcels, changed = _apply_updates(parcels, out.parcel_updates)
        notes += changed
        if user and out.memory:
            remembered_now, forgotten_now = memory.apply_ops(user, vessel["id"], out.memory)

    from_memory, used = ({"empty_tanks": [], "fixed_grades": {}, "max_trim_m": None, "fill": None}, [])
    if user:
        from_memory, used = memory.constraints_for(user, vessel, set(memory_off))
        released = [t for t in from_memory["empty_tanks"] if instructions and re.search(
            rf"\b(use|release)\b.*\b{re.escape(t[3:-1])}\b", instructions, re.I) and t not in constraints.empty_tanks]
        if released:
            notes.append(f"{', '.join(released)} stay empty because of a remembered rule. "
                         f"Say 'forget the tank {released[0][3:-1]} rule' to remove it, or switch it off below.")
        memory.mark_used(user, [i["id"] for i in used])
    merged = _merge(constraints, from_memory)

    plan = _plan_stages(vessel, parcels, merged, memory.aliases(user) if user else None,
                        *(last_cargo(user, vessel["id"]) if user else ({}, None)))
    digest = plan_digest(plan)
    explanation, source = _explain(digest, lines("explain_style"))
    return {"parcels": [p.model_dump() for p in parcels], "parcel_notes": notes,
            "constraints": constraints.model_dump(), "applied_constraints": merged.model_dump(),
            "memory_used": used, "remembered": remembered_now, "forgotten": forgotten_now,
            "plan": plan, "explanation": explanation, "explanation_source": source}


# ---------- ports where one grade is discharged and another loaded ----------

def _same_port(a: str | None, b: str | None) -> bool:
    return bool(a and b and a.strip().lower() == b.strip().lower())


def _constraints_for(merged: ConstraintsIn, parcels) -> Constraints:
    """Solver constraints for one stage: tank-grade rules only for grades loaded in that stage."""
    ids = {g.id for p in parcels if (g := match_grade(p.grade))} | {p.grade.lower() for p in parcels}
    fixed = {t: g for t, g in merged.fixed_grades.items()
             if g.lower() in ids or ((m := match_grade(g)) is not None and m.id in ids)}
    return Constraints(list(merged.empty_tanks), fixed, merged.max_trim_m, even_fill=merged.fill == "even")


def _grade_name(gid: str | None, names: dict[str, str]) -> str:
    if not gid:
        return "empty"
    return names.get(gid) or next((g.name for g in GRADES if g.id == gid), gid)


def _changes(allocation: list[dict], prev: dict[str, str], source: dict[str, str], names: dict[str, str]) -> list[dict]:
    """Every tank that gets a different grade from its last cargo, with the cleaning it needs."""
    rows = []
    for a in allocation:
        for tid in a["tanks"]:
            before = prev.get(tid)
            if not before or before == a["grade_id"]:
                continue
            rows.append({"tank": tid, "prev": before, "prev_name": _grade_name(before, names), "next": a["grade_id"],
                         "next_name": a["grade"], "source": source.get(tid, "last voyage"),
                         **cleaning.requirement(before, a["grade_id"])})
    return sorted(rows, key=lambda r: (-r["level"], r["tank"]))


LOOKAHEAD_OPTIONS = 24  # first-port plans tried against the later ports' cargo


def _unevenness(plan: dict) -> float:
    """Largest spread of fill levels within one grade's tanks, over the whole voyage (percentage points)."""
    gaps = []
    for p in [plan] + plan.get("stages", []):
        by_tank = {l["tank_id"]: l for l in p["loads"]}
        for a in p["allocation"]:
            pct = [100 * by_tank[t]["volume_m3"] / cap for t in a["tanks"] if t in by_tank and (cap := _caps.get(t))]
            if pct:
                gaps.append(max(pct) - min(pct))
    return round(max(gaps, default=0.0), 1)


_caps: dict[str, float] = {}


def _plan_stages(vessel: dict, parcels, merged: ConstraintsIn, aliases, last: dict[str, str],
                 last_info: dict | None) -> dict:
    """Solve the cargo loaded first, then each port where a discharged grade's tanks are reloaded.

    With such a port, the first port's tanks are chosen with its cargo in mind: several passing first-port
    plans are each followed through every later port, and the one needing the least cleaning (cost, then
    time at the port) is kept. Even fill then spreads over the tanks used rather than every tank.
    """
    load_ports = list(dict.fromkeys(p.load_port for p in parcels if p.load_port))
    dis_ports = voyage_check.discharge_rotation(parcels)  # a reload port comes before the ports its cargo goes to
    reload = [d for d in dis_ports if any(_same_port(d, lp) for lp in load_ports)]
    later = [i for i, p in enumerate(parcels) if any(_same_port(p.load_port, d) for d in reload)]
    first = [i for i in range(len(parcels)) if i not in later]
    if not first or not later:  # nothing to stage: everything is on board together
        first, reload = list(range(len(parcels))), []
    first_port = next((parcels[i].load_port for i in first if parcels[i].load_port), None)
    _caps.clear()
    _caps.update({t["id"]: t["capacity100"] for t in vessel["tanks"]})

    def run(idx, on_board, prev, lookahead=False, partial=False, alternatives=False):
        sub = [parcels[i] for i in idx]
        cons = _constraints_for(merged, sub)
        cons.even_fill_partial = lookahead or partial
        plan = solve(vessel, sub, cons, aliases=aliases, on_board=on_board, prev_grades=prev,
                     alternatives=LOOKAHEAD_OPTIONS if lookahead or alternatives else 0)
        for p in [plan] + plan.get("alternatives", []):
            for a in p["allocation"]:
                a["parcel"] = idx[a["parcel"]]  # back to the order's parcel numbers
        return plan

    def simulate(plan: dict) -> dict:
        """Follow a first-port plan through every reload port; adds stages and the cleaning report."""
        names = {a["grade_id"]: a["grade"] for a in plan["allocation"]}
        src = f"last voyage ({last_info['date']})" if last_info else "last voyage"
        ports = []
        rows = _changes(plan["allocation"], last, {}, names)
        if rows:
            ports.append({"port": first_port or "first load port", "when": "before loading", "source": src,
                          "changes": rows, "checks": cleaning.port_checks(rows), "estimate": cleaning.estimate(rows)})

        # Cargo on board as (allocation, its tank loads); each reload port takes some off and puts new cargo in.
        loads_by_tank = {l["tank_id"]: l for l in plan["loads"]}
        aboard = [(a, [loads_by_tank[t] for t in a["tanks"] if t in loads_by_tank]) for a in plan["allocation"]]
        prev = dict(last)
        stages = []
        for port in reload:
            pos = next(i for i, d in enumerate(dis_ports) if _same_port(d, port))
            gone_ports = dis_ports[: pos + 1]
            source, keep, gone = {}, [], []
            for a, loads in aboard:
                if any(_same_port(parcels[a["parcel"]].discharge_port, g) for g in gone_ports):
                    gone.append(a["grade"])
                    for t in a["tanks"]:
                        prev[t], source[t] = a["grade_id"], f"discharged at {port}"
                else:
                    keep.append((a, loads))
            idx = [i for i in later if _same_port(parcels[i].load_port, port)]
            on_board = [TankLoad(l["tank_id"], l["volume_m3"], l["density_t_m3"], l.get("grade")) for _, ls in keep for l in ls]
            stage = run(idx, on_board, prev, partial=True)  # less cleaning wins over spreading into every tank
            names.update({a["grade_id"]: a["grade"] for a in stage["allocation"]})
            rows = _changes(stage["allocation"], prev, source, names)
            stages.append({"port": port, "discharged": sorted(set(gone)),
                           **{k: stage[k] for k in ("status", "message", "allocation", "loads", "result", "warnings")}})
            if rows:
                ports.append({"port": port, "when": "after discharge, before loading", "source": f"discharged at {port}",
                              "changes": rows, "checks": cleaning.port_checks(rows), "estimate": cleaning.estimate(rows)})
            new_loads = {l["tank_id"]: l for l in stage["loads"]}
            aboard = keep + [(a, [new_loads[t] for t in a["tanks"] if t in new_loads]) for a in stage["allocation"]]
            for a in stage["allocation"]:
                for t in a["tanks"]:
                    prev[t] = a["grade_id"]

        plan.pop("alternatives", None)
        plan["stages"] = stages
        plan["cleaning"] = {"ports": ports, "last_voyage": last_info,
                            "tanks_to_clean": sum(1 for p in ports for c in p["changes"] if c["level"] > 0),
                            "total": {"hours": round(sum(p["estimate"]["hours"] for p in ports), 1),
                                      "cost_usd": sum(p["estimate"]["cost_usd"] for p in ports)},
                            "saving": None}
        return plan

    def checked(plan: dict) -> dict:
        """The plan with its voyage check: pipelines and the ship's condition after every port."""
        plan["voyage_check"] = voyage_check.check(vessel, parcels, voyage_check.cargo_from_plan(plan))
        return plan

    def with_ballast(plan: dict) -> dict:
        """The chosen plan's voyage check again, with a ballast preview for every port whose trim is off."""
        plan["voyage_check"] = voyage_check.check(vessel, parcels, voyage_check.cargo_from_plan(plan), ballast=True)
        return plan

    def blocked(plan: dict) -> int:
        return sum(1 for i in plan["voyage_check"]["issues"] if i["severity"] == "block")

    def rank(plan: dict) -> tuple:
        results = [plan.get("result")] + [s.get("result") for s in plan["stages"]]
        left = sum(a["unallocated_m3"] for p in [plan] + plan["stages"] for a in p["allocation"])
        # Large trim (the engine's warning, over 1.5% of the length) is worse than some cleaning.
        steep = sum(1 for r in results if r and any(w.startswith("Large trim") for w in r["warnings"]))
        total = plan["cleaning"]["total"]
        # A plan that traps cargo or is unsafe after some port goes behind every plan that passes the voyage check.
        return (blocked(plan), not all(r and r["all_passed"] for r in results), round(left), steep, total["cost_usd"],
                total["hours"], _unevenness(plan) if merged.fill == "even" else 0)

    if not reload:
        # The solver's best, unless an alternative passes the voyage check, or has fewer warnings after later ports
        # (the departure condition is already the solver's own choice; min keeps its order on ties).
        top = run(first, [], last, alternatives=True)
        options = [checked(simulate(o)) for o in [top] + top.pop("alternatives", [])]

        def later_warnings(o: dict) -> int:
            start = o["voyage_check"]["ports"][0]["port"] if o["voyage_check"]["ports"] else None
            return sum(1 for i in o["voyage_check"]["issues"] if i["severity"] == "warn" and i["port"] != start)

        return with_ballast(min(options, key=lambda o: (blocked(o), round(sum(a["unallocated_m3"] for a in o["allocation"])),
                                                        later_warnings(o))))

    # The first port planned on its own (as before), then the same port planned with the later cargo in mind.
    baseline = checked(simulate(run(first, [], last)))
    ahead = run(first, [], last, lookahead=True)
    options = [checked(simulate(p)) for p in [ahead] + ahead.pop("alternatives", [])]
    best = min([*options, baseline], key=rank)  # min keeps the earliest of equals: the solver's own order
    b, c = baseline["cleaning"]["total"], best["cleaning"]["total"]
    if rank(best)[:2] == rank(baseline)[:2] and (b["cost_usd"] > c["cost_usd"] or b["hours"] > c["hours"]):
        best["cleaning"]["saving"] = {"hours": round(b["hours"] - c["hours"], 1), "cost_usd": b["cost_usd"] - c["cost_usd"],
                                      "tanks": baseline["cleaning"]["tanks_to_clean"] - best["cleaning"]["tanks_to_clean"],
                                      "port": reload[0], "compared_to": f"planning {first_port or 'the first port'} on its own"}
    return with_ballast(best)

# ---------- hand-edited conditions ----------

def check_condition(vessel: dict, parcels, loads: list[dict], aliases=None) -> dict:
    """Check a hand-edited tank condition against the order's ports, and build the plan it would approve as.

    Returns the voyage check (with a swap suggestion when something is blocked), the per-parcel allocation
    and the stability result on departure. Orders that reload at a later port are planned by the solver only.
    """
    from .grades import grade_for
    from .quantities import convert
    from .solver import service_loads

    load_ports = {str(p.load_port).lower() for p in parcels if p.load_port}
    if any(p.discharge_port and p.discharge_port.lower() in load_ports for p in parcels):
        report = {"ok": False, "ports": [], "suggestion": None, "issues": [{
            "code": "RELOAD_NOT_SUPPORTED", "severity": "block", "port": "", "lines": [], "tanks": [],
            "message": "This order loads again at a port where cargo is discharged; hand edits can't be checked for it. "
                       "Replan with the AI planner instead."}]}
        return {"check": report, "allocation": [], "result": None, "plan": None}

    cargo, notes = voyage_check.cargo_from_loads(vessel, parcels, loads, aliases)
    report = voyage_check.check(vessel, parcels, cargo, ballast=True)
    report["issues"] = notes + report["issues"]
    pipes = voyage_check.Pipes(vessel)
    allocation = []
    for i, p in enumerate(parcels):
        mine = [c for c in cargo if c.parcel == i]
        lib, _ = grade_for(p.grade, None, aliases)
        conv = convert(p, lib.id, lib.density) or {}
        vol = sum(c.volume_m3 for c in mine)
        density = sum(c.volume_m3 * c.density for c in mine) / vol if vol else lib.density
        requested = conv.get("volume_m3", vol)
        allocation.append({"parcel": i, "grade": p.grade, "grade_id": lib.id, "density_t_m3": round(density, 4),
                           "requested_m3": round(requested, 1), "allocated_m3": round(vol, 1),
                           "unallocated_m3": round(max(0.0, requested - vol), 1), "tonnes": round(vol * density, 1),
                           "conversion": conv or None, "tanks": [c.tank for c in mine],
                           "lines": sorted({pipes.line_of[c.tank] for c in mine if c.tank in pipes.line_of})})
    plan_loads = [{"tank_id": c.tank, "volume_m3": c.volume_m3, "density_t_m3": c.density, "grade": c.grade_id}
                  for c in cargo] + [vars(s) for s in service_loads(vessel)]
    try:
        result = calculate(vessel, [TankLoad(l["tank_id"], l["volume_m3"], l["density_t_m3"], l["grade"])
                                    for l in plan_loads]).to_dict()
    except StabilityError as e:
        result = None
        report["issues"].append({"code": "PORT_CONDITION", "severity": "block", "port": "", "lines": [], "tanks": [],
                                 "message": str(e)})
        report["ok"] = False
    short = any(a["unallocated_m3"] > 0.5 for a in allocation)
    passed = bool(result and result["all_passed"])
    plan = {"status": "fails_criteria" if not passed else "partial" if short else "planned",
            "message": "Adjusted by hand.", "manual": True, "loads": plan_loads, "allocation": allocation,
            "result": result, "stages": [], "candidates_checked": 0, "candidates_total": 0,
            "warnings": [i["message"] for i in report["issues"] if i["severity"] == "warn"], "voyage_check": report}
    return {"check": report, "allocation": allocation, "result": result, "plan": plan}


# ---------- adding cargo to an approved voyage ----------

def rebuild_conditions(vessel: dict, parcels, plan: dict) -> None:
    """Recompute the tank loads and stability of every condition (departure and after each reload port) from the
    voyage's cargo, using the same port timeline as the voyage check, so the two always agree."""
    from .solver import service_loads

    service = [vars(s) for s in service_loads(vessel)]
    steps = voyage_check.port_timeline(parcels, voyage_check.cargo_from_plan(plan))

    def condition(aboard) -> tuple[list[dict], dict | None]:
        loads = [{"tank_id": c.tank, "volume_m3": c.volume_m3, "density_t_m3": c.density, "grade": c.grade_id}
                 for c in aboard] + service
        try:
            return loads, calculate(vessel, [TankLoad(l["tank_id"], l["volume_m3"], l["density_t_m3"], l["grade"])
                                             for l in loads]).to_dict()
        except StabilityError:
            return loads, None

    plan["loads"], plan["result"] = condition(steps[0]["aboard"])
    for st in plan.get("stages") or []:
        step = next(s for s in steps if s["stage"] and _same_port(s["port"], st["port"]))
        st["loads"], st["result"] = condition(step["aboard"])
        st["discharged"] = sorted({c.grade for c in step["leaving"]})
        passed = bool(st["result"] and st["result"]["all_passed"])
        st["status"], st["message"] = ("planned", "Plan passes all stability criteria.") if passed else \
            ("fails_criteria", "The condition after this port fails its stability checks.")


def amend_voyage(user: str, vessel: dict, record: dict, suggestion: dict, order_line: str | None) -> dict:
    """Add a verified open-space suggestion to the voyage itself: existing cargo stays where it is, the new
    cargo goes into the suggested tanks at its port, and every condition, check and summary is redone."""
    from . import open_space
    from .grades import grade_for

    facts = open_space.voyage_facts(vessel, record)
    verdict = open_space.verify(vessel, record, facts, suggestion)
    if not verdict["verified"]:
        raise ValueError(verdict["why"])

    plan = record["plan"]
    parcels = list(record.get("parcels") or [])
    lib, _ = grade_for(suggestion["grade"], None)
    density = facts["grades"].get(suggestion["grade"]) or lib.density
    port, dis = suggestion["port"], suggestion["discharge_port"]
    parcels.append({"grade": suggestion["grade"], "quantity_t": None, "quantity_m3": suggestion["quantity_m3"],
                    "density_t_m3": density, "load_port": port, "discharge_port": dis})
    tanks = {t["id"]: t for t in vessel["tanks"]}
    total = sum(tanks[t]["capacity100"] for t in suggestion["tanks"])
    new_loads = [{"tank_id": t, "volume_m3": round(suggestion["quantity_m3"] * tanks[t]["capacity100"] / total, 1),
                  "density_t_m3": density, "grade": lib.id} for t in suggestion["tanks"]]
    pipes = voyage_check.Pipes(vessel)
    alloc = {"parcel": len(parcels) - 1, "grade": suggestion["grade"], "grade_id": lib.id, "density_t_m3": density,
             "requested_m3": suggestion["quantity_m3"], "allocated_m3": suggestion["quantity_m3"], "unallocated_m3": 0.0,
             "tonnes": round(suggestion["quantity_m3"] * density, 1), "conversion": None, "tanks": suggestion["tanks"],
             "lines": sorted({pipes.line_of[t] for t in suggestion["tanks"] if t in pipes.line_of})}

    first_port = facts["ports"][0]["port"]
    if _same_port(port, first_port):  # loads with the rest of the cargo at the first port
        plan["allocation"].append(alloc)
        plan["loads"] = plan["loads"] + new_loads
    else:  # loads at a later port: that port's stage, created in calling order if it had none
        stages = plan.setdefault("stages", [])
        stage = next((s for s in stages if _same_port(s["port"], port)), None)
        if stage is None:
            stage = {"port": port, "discharged": [], "status": "planned", "message": "", "allocation": [], "loads": [],
                     "result": None, "warnings": []}
            stages.append(stage)
            order = [d for d in voyage_check.discharge_rotation(parcels)]
            stages.sort(key=lambda s: next(i for i, d in enumerate(order) if _same_port(d, s["port"])))
        stage["allocation"].append(alloc)
        stage["loads"] = stage["loads"] + new_loads

    rebuild_conditions(vessel, parcels, plan)
    plan["voyage_check"] = voyage_check.check(vessel, parcels, voyage_check.cargo_from_plan(plan), ballast=True)

    # Cleaning for the new tanks (their last cargo on this voyage); the existing rows stay as they were.
    port_facts = next(p for p in facts["ports"] if _same_port(p["port"], port))
    last = {t: e["last"] for e in port_facts["empty"] for t in open_space._expand(e["tanks"])}
    rows = []
    for t in suggestion["tanks"]:
        prev = grade_for(last[t], None)[0].id if last.get(t) else None
        if prev and prev != lib.id:
            rows.append({"tank": t, "prev": prev, "prev_name": last[t], "next": lib.id, "next_name": suggestion["grade"],
                         "source": f"discharged at {port}" if not _same_port(port, first_port) else "last voyage",
                         **cleaning.requirement(prev, lib.id)})
    report = plan.setdefault("cleaning", {"ports": [], "last_voyage": None, "tanks_to_clean": 0, "total": {"hours": 0, "cost_usd": 0}})
    if rows:
        entry = next((p for p in report["ports"] if _same_port(p["port"], port)), None)
        if entry is None:
            entry = {"port": port, "when": "before loading", "source": rows[0]["source"], "changes": [], "checks": []}
            report["ports"].append(entry)
        entry["changes"] += rows
        entry["checks"] = cleaning.port_checks(entry["changes"])
        entry["estimate"] = cleaning.estimate(entry["changes"])
        report["tanks_to_clean"] = sum(1 for p in report["ports"] for c in p["changes"] if c["level"] > 0)
        report["total"] = {"hours": round(sum(p.get("estimate", {}).get("hours", 0) for p in report["ports"]), 1),
                           "cost_usd": sum(p.get("estimate", {}).get("cost_usd", 0) for p in report["ports"])}

    line = order_line or f"Load {port}: {suggestion['quantity_t']:,.0f} mt {suggestion['grade']} for {dis}."
    plan.setdefault("amendments", []).append({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": user, "line": line, "port": port,
        "tanks": suggestion["tanks"], "grade": suggestion["grade"], "quantity_t": suggestion["quantity_t"]})
    record = {**record, "parcels": parcels, "plan": plan,
              "order_text": f"{(record.get('order_text') or '').strip()} {line}".strip(),
              "explanation": plain.explain_plan(plan_digest(plan))}
    record["voyage"] = voyage_summary(record)
    stored = {k: v for k, v in record.items() if k != "vessel"}
    vessels.write_json(_plan_dir(user) / f"{record['id']}.json", stored)
    return record


def blocking_issue(vessel: dict, parcels, plan: dict) -> str | None:
    """The first blocking voyage-check issue of a plan about to be approved, or None (checked again on the server)."""
    report = voyage_check.check(vessel, parcels, voyage_check.cargo_from_plan(plan), suggest=False)
    return next((i["message"] for i in report["issues"] if i["severity"] == "block"), None)


def last_cargo(user: str, vessel_id: str) -> tuple[dict[str, str], dict | None]:
    """Each cargo tank's last grade after the ship's latest approved voyage, and which voyage that was."""
    rec = next((r for r in _records(user) if r.get("vessel_id") == vessel_id), None)
    if not rec:
        return {}, None
    plan = rec.get("plan") or {}
    grades = {t: a["grade_id"] for a in plan.get("allocation", []) for t in a["tanks"]}
    for st in plan.get("stages") or []:  # tanks reloaded at a later port end with that grade
        grades.update({t: a["grade_id"] for a in st.get("allocation", []) for t in a["tanks"]})
    ports = (rec.get("voyage") or {}).get("discharge_ports") or []
    return grades, {"id": rec["id"], "date": rec["approved_at"][:10], "last_port": ports[-1] if ports else None}


# ---------- approved plans, kept per user and shown as voyages ----------

def _plan_dir(user: str):
    return config.USER_DIR / user / "plans"


def voyage_summary(record: dict) -> dict:
    """Route, grades and tonnes for a voyage card, from the parcels and the allocation."""
    parcels = record.get("parcels") or []
    plan = record.get("plan") or {}
    load_ports = list(dict.fromkeys(p["load_port"] for p in parcels if p.get("load_port")))
    dis_ports = voyage_check.discharge_rotation(parcels)
    # One entry per grade, even when it is loaded as several parcels or at a later port.
    by_grade: dict[str, dict] = {}
    for a in [*plan.get("allocation", []), *(a for st in plan.get("stages") or [] for a in st.get("allocation", []))]:
        t = a["tonnes"] if a.get("tonnes") is not None else a["allocated_m3"] * a["density_t_m3"]
        g = by_grade.setdefault(a["grade_id"], {"grade": a["grade"], "grade_id": a["grade_id"], "tonnes": 0.0})
        g["tonnes"] += t
    grades = [{**g, "tonnes": round(g["tonnes"])} for g in by_grade.values()]
    r = plan.get("result") or {}
    passed = bool(r.get("all_passed")) and all((st.get("result") or {}).get("all_passed") for st in plan.get("stages") or [])
    return {"load_ports": load_ports, "discharge_ports": dis_ports, "grades": grades,
            "total_t": sum(g["tonnes"] for g in grades), "gm_m": r.get("gm_fluid_m"), "trim_m": r.get("trim_m"),
            "passed": passed, "status": plan.get("status"),
            "tanks_to_clean": (plan.get("cleaning") or {}).get("tanks_to_clean", 0),
            "cleaning_hours": ((plan.get("cleaning") or {}).get("total") or {}).get("hours", 0)}


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
        out.append((r, path.stat().st_mtime_ns))
    # Newest first; two approvals in the same second are told apart by when their file was written.
    return [r for r, _ in sorted(out, key=lambda x: (x[0]["approved_at"], x[1]), reverse=True)]


def current_voyage(user: str, vessel_id: str) -> dict | None:
    """A ship's current voyage: its latest approved one."""
    return next((r for r in _records(user) if r.get("vessel_id") == vessel_id), None)


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

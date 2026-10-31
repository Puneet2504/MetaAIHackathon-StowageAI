"""Voyage check: follow a cargo plan port by port through the ship's pipelines. No AI here.

Cargo loaded for a later port must never be trapped by cargo loaded or kept elsewhere: each cargo line
(and its pump) carries one grade at a time, crossovers between grades stay sealed, every discharge port
can pump its cargo without touching another grade, and the ship stays safe (stability and hull
strength) after every port. Blocking issues stop approval; a search over tank moves and swaps
proposes the shortest sequence that clears them. The AI only explains the result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..engine.stability import StabilityError, TankLoad, calculate
from .grades import grade_for
from .solver import service_loads

MAX_SWAP_STEPS = 3


@dataclass(frozen=True)
class Cargo:
    """Cargo in one tank: which parcel it belongs to, and when it is loaded (0 = first ports, n = n-th reload port)."""
    tank: str
    grade_id: str
    grade: str
    volume_m3: float
    density: float
    parcel: int | None
    stage: int = 0


def _get(p, key):
    return p.get(key) if isinstance(p, dict) else getattr(p, key, None)


def _same(a, b) -> bool:
    return bool(a and b and str(a).strip().lower() == str(b).strip().lower())


def discharge_rotation(parcels) -> list[str]:
    """Discharge ports in calling order: as they first appear in the order, except that a port where cargo is
    also loaded comes before every port that cargo goes to (it can't be discharged before it is loaded)."""
    ports = list(dict.fromkeys(_get(p, "discharge_port") for p in parcels if _get(p, "discharge_port")))
    for _ in range(len(ports)):
        moved = False
        for r in list(ports):
            targets = [_get(p, "discharge_port") for p in parcels if _same(_get(p, "load_port"), r) and _get(p, "discharge_port")]
            first = min((i for i, d in enumerate(ports) if any(_same(d, t) for t in targets)), default=None)
            here = ports.index(r)
            if first is not None and first < here:
                ports.insert(first, ports.pop(here))
                moved = True
        if not moved:
            break
    return ports


def _pair(tank: str) -> str:
    return re.sub(r"[PS]$", "", tank)


def _pairs_text(tanks) -> str:
    """COT3P, COT3S -> 'COT3 P/S'."""
    out, seen = [], set(tanks)
    for t in sorted(seen):
        if t[-1:] in "PS" and _pair(t) + ("S" if t[-1] == "P" else "P") in seen:
            if t[-1] == "P":
                out.append(f"{_pair(t)} P/S")
        else:
            out.append(t)
    return ", ".join(out)


class Pipes:
    """The ship's cargo lines, pumps and crossovers (from cargoSystem, or each tank's lineId)."""

    def __init__(self, vessel: dict):
        cs = vessel.get("cargoSystem") or {}
        self.line_of: dict[str, str] = {}
        self.pump_of: dict[str, str] = {}
        for line in cs.get("lines") or []:
            for t in line.get("tankIds", []):
                self.line_of[t] = line["id"]
            if line.get("pumpId"):
                self.pump_of[line["id"]] = line["pumpId"]
        for t in vessel["tanks"]:
            if t["id"] not in self.line_of and t.get("lineId"):
                self.line_of[t["id"]] = t["lineId"]
        self.crossovers = cs.get("crossovers") or []


# ---------- rules ----------

def _segregation(pipes: Pipes, aboard: list[Cargo], port: str) -> list[dict]:
    """Lines, pumps and crossovers carrying more than one grade at once."""
    issues = []
    grades_on: dict[str, dict[str, list[str]]] = {}
    for c in aboard:
        line = pipes.line_of.get(c.tank)
        if line:
            grades_on.setdefault(line, {}).setdefault(c.grade_id, []).append(c.tank)
    names = {c.grade_id: c.grade for c in aboard}
    for line, by_grade in grades_on.items():
        if len(by_grade) > 1:
            parts = [f"{names[g]} ({_pairs_text(t)})" for g, t in by_grade.items()]
            issues.append({"code": "LINE_SHARED", "severity": "block", "port": port, "lines": [line],
                           "tanks": sorted(t for ts in by_grade.values() for t in ts),
                           "message": f"Line {line} carries {' and '.join(parts)}: one grade would contaminate the other."})
    by_pump: dict[str, dict[str, str]] = {}
    for line, by_grade in grades_on.items():
        if len(by_grade) == 1 and (pump := pipes.pump_of.get(line)):
            by_pump.setdefault(pump, {})[line] = next(iter(by_grade))
    for pump, lines in by_pump.items():
        if len(set(lines.values())) > 1:
            issues.append({"code": "PUMP_SHARED", "severity": "block", "port": port, "lines": sorted(lines), "tanks": [],
                           "message": f"Pump {pump} serves lines {', '.join(sorted(lines))} carrying different grades."})
    for x in pipes.crossovers:
        a, b = x.get("lineIds", [None, None])[:2]
        ga, gb = grades_on.get(a, {}), grades_on.get(b, {})
        if len(ga) == 1 and len(gb) == 1 and set(ga) != set(gb):
            sealed = x.get("valveCount", 1) >= 2
            issues.append({"code": "CROSSOVER", "severity": "note" if sealed else "block", "port": port, "lines": [a, b],
                           "tanks": [],
                           "message": (f"Keep crossover {x['id']} between {a} and {b} shut and sealed (two valves)."
                                       if sealed else
                                       f"Crossover {x['id']} joins {a} and {b} with a single valve: not enough to keep "
                                       f"{names[next(iter(ga))]} and {names[next(iter(gb))]} apart.")})
    return issues


def port_timeline(parcels, cargo: list[Cargo]) -> list[dict]:
    """Every port in calling order, with the cargo leaving there and the cargo on board when the ship sails.

    The first entry is the first load port (kind "load"); then each discharge port, where cargo for that port
    leaves and, at a reload port, the cargo loaded there (its stage) comes on board. Shared by the voyage check
    and by anything that rebuilds a voyage's conditions, so they always agree.
    """
    load_ports = list(dict.fromkeys(_get(p, "load_port") for p in parcels if _get(p, "load_port")))
    dis_ports = discharge_rotation(parcels)
    reload = [d for d in dis_ports if any(_same(d, lp) for lp in load_ports)]
    first_port = next((lp for lp in load_ports if not any(_same(lp, r) for r in reload)), None) or "the first load port"

    def leaves_at(c: Cargo, port: str) -> bool:
        return c.parcel is not None and c.parcel < len(parcels) and _same(_get(parcels[c.parcel], "discharge_port"), port)

    aboard = [c for c in cargo if c.stage == 0]
    out = [{"port": first_port, "kind": "load", "stage": 0, "leaving": [], "staying": list(aboard), "aboard": list(aboard)}]
    for port in dis_ports:
        leaving = [c for c in aboard if leaves_at(c, port)]
        staying = [c for c in aboard if not leaves_at(c, port)]
        stage = next((i + 1 for i, r in enumerate(reload) if _same(r, port)), None)
        aboard = staying + ([c for c in cargo if c.stage == stage] if stage else [])
        out.append({"port": port, "kind": "reload" if stage else "discharge", "stage": stage, "leaving": leaving,
                    "staying": staying, "aboard": list(aboard)})
    return out


def _condition(vessel: dict, aboard: list[Cargo], port: str, kind: str, with_ballast: bool = False) -> tuple[dict, list[dict]]:
    """Stability and hull strength on leaving a port with this cargo on board (and a ballast preview if asked)."""
    loads = [TankLoad(c.tank, c.volume_m3, c.density, c.grade_id) for c in aboard] + service_loads(vessel)
    row = {"port": port, "kind": kind, "aboard": sorted({c.grade for c in aboard})}
    try:
        r = calculate(vessel, loads)
    except StabilityError as e:
        row.update(passed=False, failed=[str(e)])
        return row, [{"code": "PORT_CONDITION", "severity": "block", "port": port, "lines": [], "tanks": [],
                      "message": f"Leaving {port}: {e}"}]
    failed = [c.description for c in r.criteria if not c.passed]
    row.update(passed=r.all_passed, gm_m=r.gm_fluid_m, trim_m=r.trim_m, max_sf_pct=r.max_sf_pct,
               max_bm_pct=r.max_bm_pct, failed=failed)
    issues = []
    if not r.all_passed:
        why = "; ".join(failed) or "the ship is unstable"
        issues.append({"code": "PORT_CONDITION", "severity": "block", "port": port, "lines": [], "tanks": [],
                       "message": f"Leaving {port} the ship fails: {why}."})
    issues += [{"code": "PORT_CONDITION", "severity": "warn", "port": port, "lines": [], "tanks": [],
                "message": f"Leaving {port}: {w}."} for w in r.warnings if w.startswith("Large trim")]
    if with_ballast:
        from .ballast import preview  # a preview only: never changes the condition or its verdict
        row["ballast"] = preview(vessel, loads)
    return row, issues


def check(vessel: dict, parcels, cargo: list[Cargo], suggest: bool = True, ballast: bool = False) -> dict:
    """Follow the cargo through every port of the order. `cargo` covers all stages (Cargo.stage).
    With `ballast`, each port whose trim is outside the normal band gets a ballast preview."""
    pipes = Pipes(vessel)
    issues, ports = [], []
    for step in port_timeline(parcels, cargo):
        port, aboard = step["port"], step["aboard"]
        # Cargo leaving here must not share a line with a different grade that stays: it would be trapped or spoiled.
        for line in sorted({pipes.line_of.get(c.tank) for c in step["leaving"]} - {None}):
            out = [c for c in step["leaving"] if pipes.line_of.get(c.tank) == line]
            kept = [c for c in step["staying"] if pipes.line_of.get(c.tank) == line and c.grade_id not in {o.grade_id for o in out}]
            if kept:
                issues.append({"code": "DISCHARGE_BLOCKED", "severity": "block", "port": port, "lines": [line],
                               "tanks": sorted({c.tank for c in out + kept}),
                               "message": f"At {port}, {out[0].grade} in {_pairs_text(c.tank for c in out)} can't be pumped "
                                          f"out through line {line}: {kept[0].grade} stays on board in "
                                          f"{_pairs_text(c.tank for c in kept)} on the same line."})
        if step["kind"] in ("load", "reload"):
            issues += _segregation(pipes, aboard, port)
        if aboard:
            row, found = _condition(vessel, aboard, port, step["kind"], ballast)
            ports.append(row); issues += found
        else:  # all cargo is off: the ship leaves in ballast, which isn't planned here
            ports.append({"port": port, "kind": "discharge", "aboard": [], "passed": None, "failed": []})

    # Same issue at several ports: keep the first port it appears at.
    seen, unique = set(), []
    for i in issues:
        key = (i["code"], i["severity"], tuple(i["lines"]), i["message"].split(":")[-1] if i["code"] != "PORT_CONDITION" else i["port"])
        if key not in seen:
            seen.add(key); unique.append(i)
    report = {"ok": not any(i["severity"] == "block" for i in unique), "ports": ports, "issues": unique, "suggestion": None}
    if suggest and not report["ok"] and not any(c.stage for c in cargo):
        report["suggestion"] = _suggest(vessel, parcels, cargo, report)
    return report


# ---------- swap suggestions ----------

def _score(report: dict) -> tuple:
    block = sum(1 for i in report["issues"] if i["severity"] == "block")
    warn = sum(1 for i in report["issues"] if i["severity"] == "warn")
    worst = max([max(p.get("max_sf_pct") or 0, p.get("max_bm_pct") or 0) for p in report["ports"]] or [0])
    trim = max([abs(p.get("trim_m") or 0) for p in report["ports"]] or [0])
    return (block, warn, round(worst), round(trim, 1))


def _fit(vessel_tanks: dict, cargo_in: list[Cargo], target: list[str]) -> list[Cargo] | None:
    """Put a pair's cargo into the target tanks, same volume, split by capacity; None if it doesn't fit."""
    total = sum(c.volume_m3 for c in cargo_in)
    caps = {t: vessel_tanks[t]["capacity100"] * vessel_tanks[t].get("maxFillPct", 98) / 100 for t in target}
    if total > sum(caps.values()) + 0.5:
        return None
    share = sum(caps.values())
    c0 = cargo_in[0]
    return [replace(c0, tank=t, volume_m3=round(total * cap / share, 1)) for t, cap in caps.items()]


def _suggest(vessel: dict, parcels, cargo: list[Cargo], report: dict) -> dict | None:
    tanks = {t["id"]: t for t in vessel["tanks"]}
    units: dict[str, list[str]] = {}
    for t in vessel["tanks"]:
        if t["type"] == "cargo":
            units.setdefault(_pair(t["id"]), []).append(t["id"])

    def by_unit(state: list[Cargo]) -> dict[str, list[Cargo]]:
        out: dict[str, list[Cargo]] = {}
        for c in state:
            out.setdefault(_pair(c.tank), []).append(c)
        return out

    def candidates(state: list[Cargo]):
        held = by_unit(state)
        full = [u for u in units if u in held and len({c.grade_id for c in held[u]}) == 1]
        empty = [u for u in units if u not in held]
        for u in full:
            for v in empty:
                moved = _fit(tanks, held[u], units[v])
                if moved:
                    rest = [c for c in state if _pair(c.tank) != u]
                    yield {"kind": "move", "grade": held[u][0].grade, "from": _pairs_text(units[u]),
                           "to": _pairs_text(units[v])}, rest + moved
        for i, u in enumerate(full):
            for v in full[i + 1:]:
                if held[u][0].grade_id == held[v][0].grade_id:
                    continue
                a, b = _fit(tanks, held[u], units[v]), _fit(tanks, held[v], units[u])
                if a and b:
                    rest = [c for c in state if _pair(c.tank) not in (u, v)]
                    yield {"kind": "swap", "grade": f"{held[u][0].grade} ↔ {held[v][0].grade}",
                           "from": _pairs_text(units[u]), "to": _pairs_text(units[v])}, rest + a + b

    state, moves, best = list(cargo), [], _score(report)
    for _ in range(MAX_SWAP_STEPS):
        step = None
        for move, new in candidates(state):
            r = check(vessel, parcels, new, suggest=False)
            s = _score(r)
            if s < best and (step is None or s < step[0]):
                step = (s, move, new, r)
        if step is None:
            break
        best, move, state, after = step
        moves.append(move)
        if after["ok"]:
            fixed = sorted({i["code"] for i in report["issues"] if i["severity"] == "block"})
            return {"moves": moves, "resolves": fixed, "loads": [_load_out(c) for c in state],
                    "after": {"ok": True, "ports": [{k: p.get(k) for k in ("port", "passed", "max_sf_pct", "max_bm_pct", "trim_m")}
                                                    for p in after["ports"]]}}
    return None


def _load_out(c: Cargo) -> dict:
    return {"tank_id": c.tank, "volume_m3": c.volume_m3, "density_t_m3": c.density, "grade": c.grade_id}


# ---------- building the cargo list ----------

def cargo_from_plan(plan: dict) -> list[Cargo]:
    """Cargo per tank from a solver plan: its allocation (which parcel) and loads (how much)."""
    out = []
    for stage, p in enumerate([plan] + list(plan.get("stages") or [])):
        loads = {l["tank_id"]: l for l in p.get("loads", [])}
        for a in p.get("allocation", []):
            for t in a["tanks"]:
                if t in loads and loads[t]["volume_m3"] > 0:
                    out.append(Cargo(t, a["grade_id"], a["grade"], loads[t]["volume_m3"], loads[t]["density_t_m3"],
                                     a["parcel"], stage))
    return out


def cargo_from_loads(vessel: dict, parcels, loads: list[dict], aliases=None) -> tuple[list[Cargo], list[dict]]:
    """Cargo per tank from a hand-edited condition: each grade is tied to the order's parcel of that grade."""
    cargo_ids = {t["id"] for t in vessel["tanks"] if t["type"] in ("cargo", "slop")}
    parcel_ids = [grade_for(_get(p, "grade"), None, aliases)[0].id for p in parcels]
    notes, out, warned = [], [], set()
    for l in loads:
        if l["tank_id"] not in cargo_ids or not l.get("volume_m3"):
            continue
        gid = l.get("grade") or ""
        hits = [i for i, pid in enumerate(parcel_ids) if pid == gid]
        name = _get(parcels[hits[0]], "grade") if hits else gid
        if len(hits) > 1 and gid not in warned:
            warned.add(gid)
            notes.append({"code": "GRADE_AMBIGUOUS", "severity": "warn", "port": "", "lines": [], "tanks": [],
                          "message": f"{name} goes to several ports in the order; its tanks are taken to be for the first one."})
        if not hits and gid not in warned:
            warned.add(gid)
            notes.append({"code": "GRADE_NOT_IN_ORDER", "severity": "warn", "port": "", "lines": [], "tanks": [],
                          "message": f"{gid} is not in the order, so it is taken to stay on board."})
        out.append(Cargo(l["tank_id"], gid, name, l["volume_m3"], l["density_t_m3"], hits[0] if hits else None))
    return out, notes

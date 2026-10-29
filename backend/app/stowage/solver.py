"""Deterministic stowage: place parcels in cargo tanks, then let the stability engine choose.

No AI here. The solver builds candidate allocations that respect the ship's cargo
lines (one grade per line), fill limits and the planner's constraints, runs every
candidate through engine.stability.calculate, and keeps the best one.

Simplifications for the demo:
  - tanks are filled in port/starboard pairs, so the ship stays upright
  - each grade fills whole pairs to their max fill and leaves at most one pair slack, unless
    even fill is asked for: then every tank of the grade's lines gets the same fill %
    (the normal plan is kept as a fallback if the even one fails stability)
  - slop tanks are not used for cargo; ballast is not adjusted
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from ..engine.stability import StabilityError, TankLoad, calculate, interp
from . import cleaning, quantities
from .grades import Grade, grade_for, match_grade

# Bunkers and fresh water used for every plan (only for tanks the ship has).
SERVICE_LOADS = [("FOP", 300, 0.95, "Fuel oil"), ("FOS", 300, 0.95, "Fuel oil"), ("FW", 200, 1.0, "Fresh water")]

MAX_ENGINE_RUNS = 1500      # candidates checked by the engine, best estimates first
MAX_ENUMERATED = 200_000    # guard against very large ships with many lines


@dataclass
class Constraints:
    empty_tanks: list[str] = field(default_factory=list)
    fixed_grades: dict[str, str] = field(default_factory=dict)  # tank id -> grade id
    max_trim_m: float | None = None
    even_fill: bool = False  # spread each grade evenly over all tanks on its lines
    even_fill_partial: bool = False  # even fill may leave tanks free (kept for a later port's cargo)


@dataclass
class Unit:
    """Tanks filled together: a port/starboard pair, or a single tank."""
    key: str
    tanks: list[dict]
    group: str  # cargo line id; one grade per group

    @property
    def cap(self) -> float:
        return sum(t["capacity100"] * t.get("maxFillPct", 100) / 100 for t in self.tanks)

    @property
    def lcg(self) -> float:
        return sum(t["soundingTable"][-1]["lcg"] for t in self.tanks) / len(self.tanks)


@dataclass
class ParcelReq:
    index: int
    grade: Grade        # density here is at the cargo's temperature, so volume x density is its true mass
    volume_m3: float    # room the cargo takes in the tanks, at its own temperature
    label: str
    conversion: dict = field(default_factory=dict)


def _units(vessel: dict, c: Constraints) -> list[Unit]:
    cargo = [t for t in vessel["tanks"] if t["type"] == "cargo" and t["id"] not in c.empty_tanks]
    has_lines = any(t.get("lineId") for t in cargo)
    pairs: dict[str, list[dict]] = {}
    for t in cargo:
        key = re.sub(r"[PS]$", "", t["id"]) if t["side"] in ("P", "S") else t["id"]
        pairs.setdefault(key, []).append(t)
    units = []
    for key, tanks in pairs.items():
        group = tanks[0].get("lineId") if has_lines and tanks[0].get("lineId") else key
        units.append(Unit(key, tanks, group))
    return units


def _requests(parcels, warnings: list[str], aliases: dict[str, str] | None = None) -> list[ParcelReq]:
    reqs = []
    for i, p in enumerate(parcels):
        lib, warn = grade_for(p.grade, None, aliases)
        # Tonnes, barrels or m³ to the volume in the tanks, using the order's temperature, density and basis.
        conv = quantities.convert(p, lib.id, lib.density)
        if not conv:
            warnings.append(f"Parcel {i + 1} ({p.grade}) has no quantity, so it was not planned")
            continue
        if warn and conv["density_source"] != "typical for the grade":
            warn = None  # the order gave a density or API gravity after all
        if warn:
            warnings.append(warn)
        grade = Grade(lib.id, lib.name, conv["density_at_temp"], lib.known)
        reqs.append(ParcelReq(i, grade, conv["volume_m3"], p.grade, conv))
    return reqs


def _options(units: list[Unit], volume: float, required: list[Unit], even: bool = False):
    """Ways to put `volume` into `units`: (fills {unit key: m3}, unallocated m3)."""
    total = sum(u.cap for u in units)
    if total <= volume + 1e-6:
        return [({u.key: u.cap for u in units}, volume - total)]
    # Even fill: every unit in proportion to its capacity, so all tanks end at the same fill %.
    spread = [({u.key: u.cap * volume / total for u in units}, 0.0)] if even and units else []
    return spread + _full_options(units, volume, required)


def _full_options(units: list[Unit], volume: float, required: list[Unit]):
    """Whole units to max fill, with one unit taking the remainder."""
    req_keys = {u.key for u in required}
    rest = [u for u in units if u.key not in req_keys]
    for k in range(len(required), len(units) + 1):
        found = []
        for extra in itertools.combinations(rest, k - len(required)):
            subset = list(required) + list(extra)
            if sum(u.cap for u in subset) < volume - 1e-6:
                continue
            for r in subset:  # r takes the remainder, the others are full
                others = sum(u.cap for u in subset if u is not r)
                part = volume - others
                if 0 < part <= r.cap + 1e-6:
                    fills = {u.key: u.cap for u in subset if u is not r}
                    fills[r.key] = part
                    found.append((fills, 0.0))
        if found:
            return found
    return [({u.key: u.cap for u in units}, 0.0)]


def _loads_for(units: dict[str, Unit], fills: dict[str, float], grade: Grade) -> list[TankLoad]:
    loads = []
    for key, vol in fills.items():
        u = units[key]
        cap = sum(t["capacity100"] for t in u.tanks)
        for t in u.tanks:  # split in proportion to capacity: equal fill % in a pair
            loads.append(TankLoad(t["id"], round(vol * t["capacity100"] / cap, 1), grade.density, grade.id))
    return loads


def service_loads(vessel: dict) -> list[TankLoad]:
    caps = {t["id"]: t["capacity100"] for t in vessel["tanks"]}
    return [TankLoad(tid, min(vol, caps[tid] * 0.98), dens, grade)
            for tid, vol, dens, grade in SERVICE_LOADS if tid in caps]


def _result(status: str, message: str, warnings: list[str], **extra) -> dict:
    return {"status": status, "message": message, "loads": [], "allocation": [], "result": None,
            "candidates_checked": 0, "candidates_total": 0, "warnings": warnings, **extra}


def solve(vessel: dict, parcels, constraints: Constraints | None = None,
          aliases: dict[str, str] | None = None, on_board: list[TankLoad] | None = None,
          prev_grades: dict[str, str] | None = None, alternatives: int = 0) -> dict:
    """Place the parcels. `on_board`: cargo already in the ship that stays (its tanks and lines are taken);
    `prev_grades`: each tank's last cargo (grade id), so tanks needing less cleaning are preferred;
    `alternatives`: also return up to this many other passing plans (different tanks), best first."""
    c = constraints or Constraints()
    on_board = on_board or []
    prev_grades = prev_grades or {}
    warnings: list[str] = []
    reqs = _requests(parcels, warnings, aliases)
    if not reqs:
        return _result("nothing_to_plan", "No parcel with a quantity to plan.", warnings)

    tank_ids = {t["id"] for t in vessel["tanks"]}
    for tid in c.empty_tanks:
        if tid not in tank_ids:
            warnings.append(f"Tank {tid} does not exist on this ship; ignored")
    units = _units(vessel, c)
    # Cargo staying on board keeps its tanks, and its cargo lines can't carry another grade.
    occupied = {l.tank_id for l in on_board}
    taken_groups = {u.group for u in units if any(t["id"] in occupied for t in u.tanks)}
    units = [u for u in units if u.group not in taken_groups]
    by_key = {u.key: u for u in units}
    groups = sorted({u.group for u in units})
    max_seg = (vessel.get("cargoSystem", {}).get("maxSegregations") or len(groups) + len(taken_groups)) - len(taken_groups)
    limit = max(0, min(max_seg, len(groups)))
    if len(reqs) > limit:
        return _result("cannot_segregate",
                       f"{len(reqs)} grades but this ship can keep only {limit} apart "
                       f"({len(groups)} cargo lines available). Combine or drop a parcel.", warnings)

    # Tanks the planner fixed to a grade: their line must carry that grade.
    fixed_group: dict[str, set[int]] = {}
    required: dict[int, list[Unit]] = {r.index: [] for r in reqs}
    for tid, gid in c.fixed_grades.items():
        unit = next((u for u in units if any(t["id"] == tid for t in u.tanks)), None)
        wanted = match_grade(gid)  # the AI may give the grade as written in the order
        matches = {r.index for r in reqs
                   if r.grade.id == gid.upper() or r.label.lower() == gid.lower() or (wanted and wanted.id == r.grade.id)}
        if unit is None or not matches:
            warnings.append(f"Could not apply '{tid} carries {gid}': unknown tank or grade not in the order")
            continue
        fixed_group.setdefault(unit.group, set()).update(matches)
        if len(matches) == 1:
            required[next(iter(matches))].append(unit)

    # Cheap trim estimate used to rank candidates before the engine checks them.
    base = [(ls["weight"], ls["lcg"]) for ls in [vessel["lightship"]]] + \
           [(k["weight"], k["lcg"]) for k in vessel.get("constants", [])]
    tanks = {t["id"]: t for t in vessel["tanks"]}
    base += [(s.volume_m3 * s.density_t_m3, tanks[s.tank_id]["soundingTable"][-1]["lcg"])
             for s in service_loads(vessel) + on_board]
    hyd = vessel["hydrostatics"]

    # With even fill asked for, prefer plans that use every cargo tank at the same level.
    def unevenness(combo) -> tuple[int, float]:
        if not c.even_fill:
            return (0, 0.0)
        used = [k for fills, _ in combo for k in fills]
        pct = [100 * vol / by_key[k].cap * by_key[k].tanks[0].get("maxFillPct", 100) / 100
               for fills, _ in combo for k, vol in fills.items()]
        unused = 0 if c.even_fill_partial else len(units) - len(set(used))
        return (unused, round(max(pct) - min(pct), 1) if pct else 0.0)

    # Sum of cleaning levels over the tanks used: diesel back into diesel tanks beats a wash.
    def cleaning_cost(combo) -> int:
        if not prev_grades:
            return 0
        return sum(cleaning.cost(prev_grades.get(t["id"]), r.grade.id)
                   for (fills, _), r in zip(combo, reqs) for k in fills for t in by_key[k].tanks)

    def trim_estimate(parts) -> float:
        items = base + parts
        w = sum(i[0] for i in items)
        lcg = sum(i[0] * i[1] for i in items) / w
        lcb = interp(w, [r["displacement"] for r in hyd], [r["lcb"] for r in hyd])
        return abs(lcb - lcg)

    candidates = []
    total = 0
    none_slot = len(reqs)
    for assign in itertools.product(range(len(reqs) + 1), repeat=len(groups)):
        used = set(assign) - {none_slot}
        if len(used) < len(reqs):
            continue
        g_of = dict(zip(groups, assign))
        if any(g_of[g] == none_slot or reqs[g_of[g]].index not in allowed for g, allowed in fixed_group.items()):
            continue
        per_parcel = []
        for slot, r in enumerate(reqs):
            mine = [u for u in units if g_of[u.group] == slot]
            req_units = [u for u in required[r.index] if u in mine]
            per_parcel.append(_options(mine, r.volume_m3, req_units, c.even_fill))
        for combo in itertools.product(*per_parcel):
            total += 1
            if total > MAX_ENUMERATED:
                break
            unalloc = sum(o[1] for o in combo)
            parts = [(vol * r.grade.density, by_key[k].lcg) for (fills, _), r in zip(combo, reqs) for k, vol in fills.items()]
            uneven, clean = unevenness(combo), cleaning_cost(combo)
            # Less cleaning wins over spreading evenly when tanks may be left free for a later port.
            first, second = (clean, uneven) if c.even_fill_partial else (uneven, clean)
            candidates.append((round(unalloc), first, second, trim_estimate(parts), g_of, combo, uneven))
        if total > MAX_ENUMERATED:
            warnings.append("Very many possible allocations; only part of them were considered")
            break

    if not candidates:
        return _result("cannot_segregate", "No allocation fits the cargo lines and constraints.", warnings)

    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    scored = []
    checked = 0
    for unalloc, first, second, _, g_of, combo, uneven in candidates[:MAX_ENGINE_RUNS]:
        loads = [l for (fills, _), r in zip(combo, reqs) for l in _loads_for(by_key, fills, r.grade)]
        try:
            res = calculate(vessel, loads + on_board + service_loads(vessel))
        except StabilityError:
            continue
        checked += 1
        trim_ok = c.max_trim_m is None or abs(res.trim_m) <= c.max_trim_m
        slack = sum(1 for l in loads if l.volume_m3 < tanks[l.tank_id]["capacity100"] * tanks[l.tank_id]["maxFillPct"] / 100 - 0.5)
        score = (0 if res.all_passed and trim_ok else 1, unalloc, first, second, abs(res.trim_m),
                 res.free_surface_correction_m, slack)
        scored.append((score, loads, res, g_of, combo, trim_ok, uneven))

    if not scored:
        return _result("fails_criteria", "Every candidate was rejected by the stability engine.", warnings,
                       candidates_total=total)

    def result_for(entry) -> dict:
        score, loads, res, g_of, combo, trim_ok, uneven = entry
        notes = list(warnings)
        allocation = []
        for slot, ((fills, unalloc), r) in enumerate(zip(combo, reqs)):
            allocation.append({
                "parcel": r.index, "grade": r.label, "grade_id": r.grade.id, "density_t_m3": r.grade.density,
                "requested_m3": round(r.volume_m3, 1), "allocated_m3": round(r.volume_m3 - unalloc, 1),
                "unallocated_m3": round(unalloc, 1),
                # Tonnes placed, in the order's own basis (in air unless the order says in vacuum).
                "tonnes": round(r.conversion["tonnes"] * (r.volume_m3 - unalloc) / r.volume_m3, 1),
                "conversion": r.conversion,
                "lines": sorted(g for g, s in g_of.items() if s == slot),
                "tanks": [t["id"] for k in fills for t in by_key[k].tanks],
            })
            if unalloc > 0.5:
                notes.append(f"{r.label}: {unalloc:,.0f} m3 does not fit in the tanks available for it")
        if not trim_ok:
            notes.append(f"Trim {abs(res.trim_m):.2f} m is above the requested maximum of {c.max_trim_m} m")
        if c.even_fill:
            unused, gap = uneven
            if (unused or gap > 0.5) and c.even_fill_partial:
                notes.append("Tanks were filled to 98% rather than spread evenly, so tanks stay free for "
                             "another port's cargo and less cleaning is needed")
            elif unused or gap > 0.5:
                notes.append("Spreading the cargo evenly over all tanks failed the stability checks, "
                             "so tanks were filled to 98% instead")
            else:
                fills = sorted({round(100 * l.volume_m3 / tanks[l.tank_id]["capacity100"]) for l in loads})
                level = f"{fills[0]}%" if len(fills) == 1 else f"{fills[0]}-{fills[-1]}%"
                where = "all cargo tanks" if len({l.tank_id for l in loads}) == sum(len(u.tanks) for u in units) \
                    else "the tanks it uses (others kept free for a later port)"
                notes.append(f"Cargo spread evenly over {where}: each about {level} full")
        passed = score[0] == 0
        short = sum(a["unallocated_m3"] for a in allocation) > 0.5
        status = "fails_criteria" if not passed else "partial" if short else "planned"
        message = ("No candidate passed every check; this is the best one found." if not passed
                   else "Plan passes all stability criteria, but not all cargo fits." if short
                   else "Plan passes all stability criteria.")
        return {"status": status, "message": message,
                "loads": [vars(l) for l in loads + on_board + service_loads(vessel)],
                "allocation": allocation, "result": res.to_dict(),
                "candidates_checked": checked, "candidates_total": total, "warnings": notes + res.warnings}

    scored.sort(key=lambda e: e[0])
    out = result_for(scored[0])
    if alternatives:
        # Other passing plans that put the cargo in different tanks, for a caller that looks further ahead.
        seen = {tuple(tuple(sorted(f)) for f, _ in scored[0][4])}
        alts = []
        for entry in scored[1:]:
            key = tuple(tuple(sorted(f)) for f, _ in entry[4])
            if entry[0][0] != 0 or key in seen:
                continue
            seen.add(key)
            alts.append(result_for(entry))
            if len(alts) >= alternatives:
                break
        out["alternatives"] = alts
    return out

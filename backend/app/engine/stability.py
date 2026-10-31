"""Deterministic intact stability calculation for one loading condition.

Pure Python (no third-party packages) so it is easy to test and audit.
The AI layer never changes these numbers; it only explains them.

Sign conventions (same as the vessel model):
  x from aft perpendicular, + forward
  y from centreline, + starboard
  z from baseline, + up
  trim > 0 means trim by the stern; list > 0 means list to starboard.
  still-water bending moment > 0 means hogging (ends heavier than the middle), < 0 sagging.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict


# ---------- helpers ----------

def interp(x: float, xs: list[float], ys: list[float]) -> float:
    """Linear interpolation, clamped at the table ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + f * (ys[i] - ys[i - 1])
    return ys[-1]


def area_under(angles_deg: list[float], gz: list[float], a0: float, a1: float, step: float = 0.5) -> float:
    """Area under the GZ curve between two angles, in metre-radians (trapezoidal rule)."""
    total, a = 0.0, a0
    while a < a1 - 1e-9:
        b = min(a + step, a1)
        g0, g1 = interp(a, angles_deg, gz), interp(b, angles_deg, gz)
        total += (g0 + g1) / 2 * math.radians(b - a)
        a = b
    return total


# ---------- input / output types ----------

@dataclass
class TankLoad:
    tank_id: str
    volume_m3: float
    density_t_m3: float
    grade: str | None = None


@dataclass
class CriterionResult:
    id: str
    description: str
    required: float
    actual: float
    unit: str
    passed: bool
    limit: str = "min"  # "min": actual must reach required; "max": actual must not exceed it


@dataclass
class StabilityResult:
    displacement_t: float
    deadweight_t: float
    kg_m: float
    lcg_m: float
    tcg_m: float
    free_surface_correction_m: float
    gm_solid_m: float
    gm_fluid_m: float
    draught_mean_m: float
    draught_fwd_m: float
    draught_aft_m: float
    trim_m: float
    list_deg: float | None  # None when GM <= 0 (list cannot be computed)
    gz_curve: dict
    criteria: list[CriterionResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    all_passed: bool = False
    # Longitudinal strength: peak still-water shear force and bending moment as % of the sea limits.
    max_sf_pct: float | None = None
    max_bm_pct: float | None = None
    strength: list[dict] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- the calculation ----------

class StabilityError(ValueError):
    pass


def _tank_item(tank: dict, load: TankLoad, warnings: list[str]) -> tuple[float, float, float, float, float]:
    """Return weight, lcg, vcg, tcg and free-surface moment (t.m) for one tank."""
    table = tank["soundingTable"]
    vols = [r["volume"] for r in table]
    cap = tank["capacity100"]
    if load.volume_m3 < 0:
        raise StabilityError(f"{tank['id']}: volume cannot be negative")
    if load.volume_m3 > cap + 1e-6:
        raise StabilityError(f"{tank['id']}: {load.volume_m3:.0f} m3 exceeds capacity {cap:.0f} m3")
    fill_pct = 100 * load.volume_m3 / cap if cap else 0
    if fill_pct > tank.get("maxFillPct", 100) + 1e-6:
        warnings.append(f"{tank['id']} filled to {fill_pct:.1f}%, above its {tank['maxFillPct']}% limit")

    weight = load.volume_m3 * load.density_t_m3
    lcg = interp(load.volume_m3, vols, [r["lcg"] for r in table])
    vcg = interp(load.volume_m3, vols, [r["vcg"] for r in table])
    tcg = interp(load.volume_m3, vols, [r["tcg"] for r in table])
    slack = 0.5 < fill_pct < 99.5
    fsm = 0.0
    if slack:
        inertia = max(r["freeSurfaceInertia"] for r in table)
        fsm = inertia * load.density_t_m3
    return weight, lcg, vcg, tcg, fsm


# ---------- longitudinal strength ----------

STRENGTH_STEP_M = 1.0
_STRENGTH_BASE: dict[tuple, dict] = {}  # per ship: the grid, fixed weights and buoyancy shape (reused by every check)


def _buoyancy_shape(t: float) -> float:
    """Relative buoyancy per metre at t = x/L: full along the parallel middle body, finer at the ends."""
    if t < 0 or t > 1:
        return 0.0
    if t < 0.15:
        return 0.3 + 0.7 * t / 0.15
    if t > 0.80:
        return 0.3 + 0.7 * (1 - t) / 0.20
    return 1.0


def _spread(weight: list[float], lo: float, dx: float, w: float, a: float, f: float) -> None:
    """Add weight w evenly over [a, f] on the grid."""
    n = len(weight)
    if f - a < dx:  # short item: put it in its cell
        weight[min(n - 1, max(0, int((0.5 * (a + f) - lo) / dx)))] += w
        return
    for i in range(max(0, int((a - lo) / dx)), min(n, math.ceil((f - lo) / dx))):
        x0 = lo + i * dx
        weight[i] += w * max(0.0, min(f, x0 + dx) - max(a, x0)) / (f - a)


def _strength_base(vessel: dict, fixed: list[tuple[float, float, float]]) -> dict:
    points = vessel["strength"]["readingPoints"]
    L = vessel["particulars"]["lbp"]
    key = (vessel.get("id"), L, tuple(fixed), tuple((t["id"], t["xAft"], t["xFwd"]) for t in vessel["tanks"]),
           tuple((p["x"], p["sf"]["seaMax"], p["bm"]["seaHogMax"], p["bm"]["seaSagMax"]) for p in points))
    base = _STRENGTH_BASE.get(key)
    if base:
        return base
    lo = min([0.0] + [b[1] for b in fixed] + [t["xAft"] for t in vessel["tanks"]])
    hi = max([L] + [b[2] for b in fixed] + [t["xFwd"] for t in vessel["tanks"]])
    n = max(1, math.ceil((hi - lo) / STRENGTH_STEP_M))
    dx = (hi - lo) / n
    xs = [lo + (i + 0.5) * dx for i in range(n)]
    weight = [0.0] * n
    for w, a, f in fixed:
        _spread(weight, lo, dx, w, a, f)
    m = L / 2
    shape = [_buoyancy_shape(x / L) * dx for x in xs]
    base = {"lo": lo, "dx": dx, "n": n, "xs": xs, "weight": weight, "shape": shape, "m": m,
            "s0": sum(shape), "s1": sum(sh * (x - m) for sh, x in zip(shape, xs)),
            "sx0": sum(sh * x for sh, x in zip(shape, xs)), "sx1": sum(sh * x * (x - m) for sh, x in zip(shape, xs)),
            "points": points}
    if len(_STRENGTH_BASE) > 64:
        _STRENGTH_BASE.clear()
    _STRENGTH_BASE[key] = base
    return base


def _longitudinal_strength(vessel: dict, fixed: list[tuple[float, float, float]],
                           tank_blocks: list[tuple[float, float, float]]) -> list[dict] | None:
    """Still-water shear force (t) and bending moment (t.m) at the ship's strength reading points.

    Blocks are (weight t, x aft, x fwd), each spread evenly over its length: `fixed` (lightship blocks and
    constants) and `tank_blocks` (loaded tanks). Buoyancy follows a simple hull-form shape plus a linear term,
    so that it equals the total weight and acts at the weights' centre (equilibrium); SF and BM then close
    to zero at the ends.
    """
    if not (vessel.get("strength") or {}).get("readingPoints") or not fixed:
        return None
    b = _strength_base(vessel, fixed)
    lo, dx, n, xs, m = b["lo"], b["dx"], b["n"], b["xs"], b["m"]
    weight = list(b["weight"])
    for w, a, f in tank_blocks:
        _spread(weight, lo, dx, w, a, f)

    W = sum(weight)
    lcg = sum(w * x for w, x in zip(weight, xs)) / W
    # alpha*s0 + beta*s1 = W ; alpha*sx0 + beta*sx1 = W*lcg
    det = b["s0"] * b["sx1"] - b["s1"] * b["sx0"]
    alpha = (W * b["sx1"] - b["s1"] * W * lcg) / det
    beta = (b["s0"] * W * lcg - b["sx0"] * W) / det
    buoy = [max(0.0, sh * (alpha + beta * (x - m))) for sh, x in zip(b["shape"], xs)]
    scale = W / sum(buoy)  # only differs from 1 if an extreme trim clamped an end

    # Shear force and bending moment at the cell edges, from the aft end.
    sf, bm = [0.0] * (n + 1), [0.0] * (n + 1)
    for i in range(n):
        sf[i + 1] = sf[i] + weight[i] - buoy[i] * scale
        bm[i + 1] = bm[i] + 0.5 * (sf[i] + sf[i + 1]) * dx

    rows = []
    for p in b["points"]:
        k = min(n - 1, max(0, int((p["x"] - lo) / dx)))
        t = (p["x"] - lo - k * dx) / dx
        f, mom = sf[k] + t * (sf[k + 1] - sf[k]), bm[k] + t * (bm[k + 1] - bm[k])
        hog = mom >= 0
        limit = p["bm"]["seaHogMax"] if hog else p["bm"]["seaSagMax"]
        rows.append({"frame": p.get("frame") or f"{p['x']:g} m", "x": p["x"], "sf_t": round(f, 1), "bm_tm": round(mom, 0),
                     "mode": "hog" if hog else "sag",
                     "sf_pct": round(100 * abs(f) / p["sf"]["seaMax"], 1), "bm_pct": round(100 * abs(mom) / limit, 1)})
    return rows


def calculate(vessel: dict, loads: list[TankLoad], include_constants: bool = True) -> StabilityResult:
    tanks = {t["id"]: t for t in vessel["tanks"]}
    warnings: list[str] = []

    ls = vessel["lightship"]
    items = [(ls["weight"], ls["lcg"], ls["vcg"], ls["tcg"])]
    # The same weights along the length, for shear force and bending moment.
    fixed = [(b["weight"], b["xAft"], b["xFwd"]) for b in ls.get("distribution") or []] \
        or [(ls["weight"], ls["lcg"] - 2, ls["lcg"] + 2)]
    tank_blocks: list[tuple[float, float, float]] = []
    if include_constants:
        items += [(c["weight"], c["lcg"], c["vcg"], c["tcg"]) for c in vessel.get("constants", [])]
        fixed += [(c["weight"], c["lcg"] - 2, c["lcg"] + 2) for c in vessel.get("constants", [])]

    total_fsm = 0.0
    for load in loads:
        if load.tank_id not in tanks:
            raise StabilityError(f"Unknown tank '{load.tank_id}'")
        if load.volume_m3 == 0:
            continue
        w, x, z, y, fsm = _tank_item(tanks[load.tank_id], load, warnings)
        items.append((w, x, z, y))
        tank_blocks.append((w, tanks[load.tank_id]["xAft"], tanks[load.tank_id]["xFwd"]))
        total_fsm += fsm

    disp = sum(i[0] for i in items)
    lcg = sum(i[0] * i[1] for i in items) / disp
    kg = sum(i[0] * i[2] for i in items) / disp
    tcg = sum(i[0] * i[3] for i in items) / disp

    hyd = vessel["hydrostatics"]
    disps = [r["displacement"] for r in hyd]
    if disp > disps[-1]:
        warnings.append("Displacement is above the summer draught: the ship is overloaded")
    if disp < disps[0]:
        raise StabilityError("Displacement is below the hydrostatic table range")

    def h(col: str) -> float:
        return interp(disp, disps, [r[col] for r in hyd])

    T, lcb, lcf, kmt, mctc = h("draught"), h("lcb"), h("lcf"), h("kmt"), h("mctc")
    lbp = vessel["particulars"]["lbp"]

    fsc = total_fsm / disp
    gm_solid = kmt - kg
    gm_fluid = gm_solid - fsc
    kg_fluid = kg + fsc

    # Trim: positive by the stern when G is aft of B
    trim = disp * (lcb - lcg) / (100 * mctc)
    t_aft = T + trim * (lcf / lbp)
    t_fwd = t_aft - trim

    if t_fwd < 0 or t_aft < 0:
        warnings.append("Calculated draught is negative: the trim is unrealistic for this condition")
    if abs(trim) > 0.015 * lbp:
        warnings.append(f"Large trim of {trim:.2f} m: check the loading sequence")

    # List from transverse G offset (small-angle; flags if GM is not positive)
    if gm_fluid > 0:
        list_deg = math.degrees(math.atan(tcg / gm_fluid))
        if abs(list_deg) > 10:
            warnings.append(f"List of {abs(list_deg):.1f} degrees is unsafe; values this large are only indicative "
                            "because the small-angle method no longer holds")
    else:
        list_deg = None
        warnings.append("GM is zero or negative: the ship is unstable (angle of loll)")

    # GZ curve from cross curves: GZ = KN - KG_fluid * sin(theta) - TCG * cos(theta)
    # (a starboard TCG reduces GZ when heeling to starboard; this is the worse side)
    cc = vessel["crossCurves"]
    angles = cc["heelAnglesDeg"]
    cc_disps = [r["displacement"] for r in cc["rows"]]
    kn = [interp(disp, cc_disps, [r["kn"][i] for r in cc["rows"]]) for i in range(len(angles))]
    gz = [round(kn[i] - kg_fluid * math.sin(math.radians(a)) - abs(tcg) * math.cos(math.radians(a)), 4)
          for i, a in enumerate(angles)]

    # Criteria, with required values taken from the vessel data
    fine = [a / 2 for a in range(0, int(angles[-1] * 2) + 1)]
    gz_fine = [interp(a, angles, gz) for a in fine]
    gz_max = max(gz_fine)
    angle_gz_max = fine[gz_fine.index(gz_max)]
    actual = {
        "GM0_MIN": gm_fluid,
        "AREA_0_30": area_under(angles, gz, 0, 30),
        "AREA_0_40": area_under(angles, gz, 0, 40),
        "AREA_30_40": area_under(angles, gz, 30, 40),
        "GZ_AT_30": max(g for a, g in zip(fine, gz_fine) if a >= 30),
        "ANGLE_GZ_MAX": angle_gz_max,
    }
    criteria = []
    for c in vessel.get("stabilityCriteria", []):
        if c["id"] not in actual:
            warnings.append(f"Criterion {c['id']} is not implemented yet")
            continue
        val = actual[c["id"]]
        criteria.append(CriterionResult(c["id"], c["description"], c["requiredValue"],
                                        round(val, 4), c["unit"], val >= c["requiredValue"]))

    strength = _longitudinal_strength(vessel, fixed, tank_blocks)
    max_sf = max_bm = None
    if strength:
        max_sf = max(r["sf_pct"] for r in strength)
        max_bm = max(r["bm_pct"] for r in strength)
        peak = max(max_sf, max_bm)
        criteria.append(CriterionResult("STRENGTH_LIMIT", "Still-water shear force and bending moment within sea limits",
                                        100.0, peak, "%", peak <= 100.0, limit="max"))
        if peak > 100.0:
            worst = max(strength, key=lambda r: max(r["sf_pct"], r["bm_pct"]))
            what = (f"bending moment {worst['bm_pct']:.0f}% ({worst['mode']}ging)" if worst["bm_pct"] >= worst["sf_pct"]
                    else f"shear force {worst['sf_pct']:.0f}%")
            warnings.append(f"Hull overstressed at {worst['frame']}: {what} of the sea limit")

    result = StabilityResult(
        displacement_t=round(disp, 1),
        deadweight_t=round(disp - ls["weight"], 1),
        kg_m=round(kg, 3), lcg_m=round(lcg, 3), tcg_m=round(tcg, 4),
        free_surface_correction_m=round(fsc, 3),
        gm_solid_m=round(gm_solid, 3), gm_fluid_m=round(gm_fluid, 3),
        draught_mean_m=round(T, 3), draught_fwd_m=round(t_fwd, 3), draught_aft_m=round(t_aft, 3),
        trim_m=round(trim, 3),
        list_deg=round(list_deg, 2) if list_deg is not None else None,
        gz_curve={"anglesDeg": angles, "gzM": gz},
        criteria=criteria,
        warnings=warnings,
        max_sf_pct=max_sf,
        max_bm_pct=max_bm,
        strength=strength,
    )
    result.all_passed = bool(criteria) and all(c.passed for c in criteria) and gm_fluid > 0
    return result


def calculate_sequence(vessel: dict, stages: list[list[TankLoad]]) -> list[StabilityResult]:
    """Check every intermediate stage of a loading or discharge sequence."""
    return [calculate(vessel, s) for s in stages]

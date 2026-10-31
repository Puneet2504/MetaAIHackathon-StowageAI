"""Build a full SYNTHETIC vessel model from a tank layout.

The layout (dimensions, tank extents, sides) can come from a person or from the
AI reader. Everything else, hydrostatics, cross curves and sounding tables, comes
from simplified box-hull formulas here, never from the model. The results are
estimates for a demo and must not be used for an actual loading decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import LayoutTank, VesselLayout

RHO_SW = 1.025

# Values from IMO 2008 IS Code Part A 2.2. Verify against the vessel's own booklet.
IMO_CRITERIA = [
    {"id": "GM0_MIN", "description": "Initial GM (corrected for free surface)", "requiredValue": 0.15, "unit": "m"},
    {"id": "AREA_0_30", "description": "Area under GZ curve 0 to 30 degrees", "requiredValue": 0.055, "unit": "m.rad"},
    {"id": "AREA_0_40", "description": "Area under GZ curve 0 to 40 degrees", "requiredValue": 0.09, "unit": "m.rad"},
    {"id": "AREA_30_40", "description": "Area under GZ curve 30 to 40 degrees", "requiredValue": 0.03, "unit": "m.rad"},
    {"id": "GZ_AT_30", "description": "GZ at 30 degrees or more", "requiredValue": 0.20, "unit": "m"},
    {"id": "ANGLE_GZ_MAX", "description": "Angle of maximum GZ", "requiredValue": 25, "unit": "deg"}]

# Default shape factor (tank volume / bounding box volume) per tank type.
DEFAULT_SHAPE = {"cargo": 0.97, "slop": 0.95, "ballast": 0.80, "fuel_oil": 0.95,
                 "diesel_oil": 0.95, "fresh_water": 0.95}


@dataclass
class Hull:
    lbp: float
    breadth: float
    depth: float
    t_max: float
    db: float = 2.0  # double bottom height
    ds: float = 2.0  # double side width

    @property
    def inner(self) -> float:
        return self.breadth / 2 - self.ds


def hull_at(h: Hull, T: float) -> dict:
    """Very simplified hydrostatics for a full-bodied tanker hull."""
    L, B = h.lbp, h.breadth
    cb = 0.72 + 0.10 * (T / h.t_max)
    cw = 0.86 + 0.04 * (T / h.t_max)
    vol = L * B * T * cb
    disp = vol * RHO_SW
    kb = T * (0.50 + 0.03 * (T / h.t_max))
    i_t = 0.075 * L * B ** 3 * cw  # transverse waterplane inertia (approx.)
    i_l = 0.050 * B * L ** 3 * cw  # longitudinal waterplane inertia (approx.)
    bm_t = i_t / vol
    bm_l = i_l / vol
    lcb = L * (0.525 - 0.010 * (T / h.t_max))
    lcf = L * (0.500 - 0.015 * (T / h.t_max))
    mctc = disp * bm_l / (100 * L)
    tpc = RHO_SW * cw * L * B / 100
    return dict(draught=round(T, 2), displacement=round(disp, 1), lcb=round(lcb, 3),
                lcf=round(lcf, 3), kmt=round(kb + bm_t, 3), mctc=round(mctc, 1),
                tpc=round(tpc, 2), _bm=bm_t)


def kn_values(h: Hull, row: dict, angles: list) -> list:
    """Wall-sided formula until the deck edge immerses or the bilge emerges, then a smooth decay."""
    T = row["draught"]
    km, bm = row["kmt"], row["_bm"]
    deck_edge = min(math.atan(2 * (h.depth - T) / h.breadth), math.atan(2 * T / h.breadth))
    out = []
    for a in angles:
        th = math.radians(a)
        t_eff = min(th, deck_edge)
        kn = math.sin(th) * km + 0.5 * bm * math.sin(t_eff) * math.tan(t_eff) ** 2
        if th > deck_edge:
            kn -= 1.6 * (th - deck_edge) ** 2  # loss of waterplane after deck edge
        out.append(round(kn, 3))
    return out


def sounding_table(x_aft, x_fwd, y_in, y_out, z_bot, z_top, shape=0.95, steps=10):
    length, breadth, height = x_fwd - x_aft, abs(y_out - y_in), z_top - z_bot
    tcg = (y_in + y_out) / 2
    lcg = (x_aft + x_fwd) / 2
    rows = []
    for i in range(steps + 1):
        h = height * i / steps
        vol = length * breadth * h * shape
        fsi = 0.0 if i in (0, steps) else length * breadth ** 3 / 12 * shape
        rows.append(dict(ullage=round(height - h, 3), volume=round(vol, 1),
                         lcg=round(lcg, 3), vcg=round(z_bot + h / 2, 3),
                         tcg=round(tcg, 3), freeSurfaceInertia=round(fsi, 1)))
    return rows, round(length * breadth * height * shape, 1)


def default_bounds(h: Hull, t: LayoutTank) -> tuple[float, float, float, float]:
    """Inner/outer y (as distances from centreline) and bottom/top z for a tank type."""
    B, D = h.breadth, h.depth
    centre = t.side == "C"
    if t.type in ("cargo", "slop"):
        return (-h.inner, h.inner, h.db, D) if centre else (0.0, h.inner, h.db, D)
    if t.type == "ballast":
        return (-B / 5, B / 5, 0.0, D * 0.6) if centre else (h.inner, B / 2, 0.0, D)
    if t.type == "fresh_water":
        return (-B * 0.15, B * 0.15, D * 0.42, D * 0.73) if centre else (B * 0.19, B * 0.34, D * 0.42, D * 0.73)
    # fuel_oil, diesel_oil
    return (-B * 0.1, B * 0.1, h.db, D * 0.73) if centre else (B * 0.19, B * 0.37, h.db, D * 0.73)


def build_tank(h: Hull, t: LayoutTank) -> dict:
    y_in, y_out, z_bot, z_top = default_bounds(h, t)
    y_in = t.yIn if t.yIn is not None else y_in
    y_out = t.yOut if t.yOut is not None else y_out
    z_bot = t.zBottom if t.zBottom is not None else z_bot
    z_top = t.zTop if t.zTop is not None else z_top
    if t.side == "P":
        y_in, y_out = -y_in, -y_out
    box = (t.xFwd - t.xAft) * abs(y_out - y_in) * (z_top - z_bot)
    if t.shapeFactor is not None:
        shape = t.shapeFactor
    elif t.capacity100 and box > 0:
        shape = min(1.0, t.capacity100 / box)  # match the stated capacity
    else:
        shape = DEFAULT_SHAPE[t.type]
    rows, cap = sounding_table(t.xAft, t.xFwd, y_in, y_out, z_bot, z_top, shape)
    max_fill = t.maxFillPct if t.maxFillPct is not None else (98 if t.type in ("cargo", "slop", "fuel_oil", "diesel_oil") else 100)
    out = dict(id=t.id, name=t.name or t.id, type=t.type, side=t.side, xAft=t.xAft, xFwd=t.xFwd,
               capacity100=cap, maxFillPct=max_fill, soundingTable=rows)
    if t.coating:
        out["coating"] = t.coating
    if t.lineId:
        out["lineId"] = t.lineId
    return out


def _default_service_tanks(h: Hull, taken: set[str]) -> list[LayoutTank]:
    """Bunkers and fresh water aft, so a condition can always include them."""
    L = h.lbp
    extra = [
        LayoutTank(id="FOP", name="Fuel oil port", type="fuel_oil", side="P", xAft=round(L * 0.102, 1), xFwd=round(L * 0.148, 1)),
        LayoutTank(id="FOS", name="Fuel oil starboard", type="fuel_oil", side="S", xAft=round(L * 0.102, 1), xFwd=round(L * 0.148, 1)),
        LayoutTank(id="DO", name="Diesel oil", type="diesel_oil", side="C", xAft=round(L * 0.080, 1), xFwd=round(L * 0.102, 1)),
        LayoutTank(id="FW", name="Fresh water", type="fresh_water", side="C", xAft=round(L * 0.011, 1), xFwd=round(L * 0.045, 1)),
    ]
    return [t for t in extra if t.id not in taken]


def _cargo_system(tanks: list[dict]) -> dict:
    lines: dict[str, list[str]] = {}
    for t in tanks:
        if t.get("lineId"):
            lines.setdefault(t["lineId"], []).append(t["id"])
    ids = sorted(lines)
    return {"pumps": [{"id": f"CP{i + 1}", "ratedM3h": 550} for i in range(len(ids))],
            "lines": [{"id": lid, "pumpId": f"CP{i + 1}", "tankIds": lines[lid]} for i, lid in enumerate(ids)],
            "crossovers": [],
            "maxSegregations": max(1, len(ids))}


def estimate_lightship(h: Hull) -> dict:
    """Rough lightship scaled from the demo MR2 (10 400 t for 176 x 32.2 x 19.1 m)."""
    L = h.lbp
    w = round(0.096 * L * h.breadth * h.depth, 0)
    return {"weight": w, "lcg": round(0.457 * L, 2), "vcg": round(0.586 * h.depth, 2), "tcg": 0.0,
            "distribution": [
                {"xAft": 0, "xFwd": round(0.17 * L, 1), "weight": round(w * 0.327, 0)},
                {"xAft": round(0.17 * L, 1), "xFwd": round(0.864 * L, 1), "weight": round(w * 0.567, 0)},
                {"xAft": round(0.864 * L, 1), "xFwd": L, "weight": round(w * 0.106, 0)}]}


# Still-water strength limits along the length, as a share of the midship value (demo factors, not class rules).
STRENGTH_STATIONS = [0.15, 0.30, 0.45, 0.50, 0.65, 0.80, 0.85]
BM_FACTOR = [0.40, 0.85, 1.00, 1.00, 0.95, 0.60, 0.45]
SF_FACTOR = [0.85, 1.00, 0.80, 0.75, 0.85, 1.00, 0.85]


def strength_limits(lbp: float, displacement_full: float) -> dict:
    """Synthetic sea and harbour shear force (t) and bending moment (t.m) limits at seven stations.

    Scaled from the full-load displacement and length: bending sea limit Δ·L/45 at midships, shear
    Δ/9 where it peaks (about a quarter length from each end). Harbour limits are 25% higher, and the
    sagging limit is 90% of the hogging one. Typical of a product tanker, for demos only.
    """
    bm_mid, sf_peak = displacement_full * lbp / 45, displacement_full / 9
    points = []
    for s, fb, fs in zip(STRENGTH_STATIONS, BM_FACTOR, SF_FACTOR):
        hog, sf = bm_mid * fb, sf_peak * fs
        points.append({"frame": f"{s:.2f}L", "x": round(s * lbp, 1),
                       "sf": {"seaMax": round(sf, -1), "harbourMax": round(1.25 * sf, -1)},
                       "bm": {"seaHogMax": round(hog, -2), "seaSagMax": round(0.9 * hog, -2),
                              "harbourHogMax": round(1.25 * hog, -2), "harbourSagMax": round(1.125 * hog, -2)}})
    return {"readingPoints": points}


def strength_for(vessel: dict) -> dict:
    """Strength limits for a ship saved without them, from its own length and hydrostatics."""
    full = max((r["displacement"] for r in vessel.get("hydrostatics", [])), default=0)
    return strength_limits(vessel["particulars"]["lbp"], full) if full else {"readingPoints": []}


def build_vessel(layout: VesselLayout, vessel_id: str, *, lightship: dict | None = None,
                 cargo_system: dict | None = None, constants: list | None = None,
                 source: dict | None = None, add_service_tanks: bool = True) -> dict:
    h = Hull(layout.lbp, layout.breadth, layout.depth, layout.summerDraught,
             layout.doubleBottom if layout.doubleBottom is not None else 2.0,
             layout.doubleSide if layout.doubleSide is not None else 2.0)
    lightship = lightship or estimate_lightship(h)

    # Start the table low enough that the lightship condition is inside it.
    start = 2.0
    while start > 0.5 and hull_at(h, start)["displacement"] > 0.9 * lightship["weight"]:
        start -= 0.5
    drafts = [round(start + 0.5 * i, 2) for i in range(int((h.t_max - start) / 0.5) + 1)]
    if drafts[-1] != h.t_max:
        drafts.append(h.t_max)
    hyd = [hull_at(h, T) for T in drafts]
    angles = [0, 10, 20, 30, 40, 50, 60]
    cross = dict(heelAnglesDeg=angles,
                 rows=[dict(displacement=r["displacement"], kn=kn_values(h, r, angles)) for r in hyd])
    for r in hyd:
        r.pop("_bm")

    layout_tanks = list(layout.tanks)
    if add_service_tanks and not any(t.type in ("fuel_oil", "fresh_water") for t in layout_tanks):
        layout_tanks += _default_service_tanks(h, {t.id for t in layout_tanks})
    tanks = [build_tank(h, t) for t in layout_tanks]

    return {
        "id": vessel_id,
        "schemaVersion": "1.0",
        "source": source or {"origin": "user_drawing",
                             "notes": "Built from a user tank layout. Hydrostatics, lightship and "
                                      "sounding tables are synthetic estimates, not from a booklet."},
        "particulars": {"name": layout.name, "vesselType": "product_tanker", "sizeClass": layout.sizeClass,
                        "lbp": h.lbp, "breadth": h.breadth, "depth": h.depth, "summerDraught": h.t_max,
                        "deadweight": round(hyd[-1]["displacement"] - lightship["weight"], 0),
                        "serviceSpeedKn": 14.0, "seawaterDensity": RHO_SW},
        "lightship": lightship,
        "hydrostatics": hyd,
        "crossCurves": cross,
        "tanks": tanks,
        "cargoSystem": cargo_system or _cargo_system(tanks),
        "strength": strength_limits(h.lbp, hyd[-1]["displacement"]),
        "draughtMarks": {"forwardX": h.lbp, "midshipX": h.lbp / 2, "aftX": 0.0},
        "constants": constants if constants is not None else [
            {"name": "Crew, stores and provisions", "weight": 300,
             "lcg": round(0.114 * h.lbp, 1), "vcg": round(0.785 * h.depth, 1), "tcg": 0.0}],
        "stabilityCriteria": IMO_CRITERIA,
    }


LAYOUT_TANK_KEYS = ("id", "name", "type", "side", "xAft", "xFwd", "capacity100", "coating", "lineId")


def layout_from_vessel(vessel: dict) -> dict:
    """The layout a ship was built from, or a plain one read back from its tanks."""
    if "layout" in vessel:
        return vessel["layout"]
    p = vessel["particulars"]
    kinds = set(DEFAULT_SHAPE)
    return {"name": p["name"], "sizeClass": p["sizeClass"], "lbp": p["lbp"], "breadth": p["breadth"],
            "depth": p["depth"], "summerDraught": p["summerDraught"],
            "tanks": [{k: t[k] for k in LAYOUT_TANK_KEYS if k in t}
                      for t in vessel["tanks"] if t["type"] in kinds]}

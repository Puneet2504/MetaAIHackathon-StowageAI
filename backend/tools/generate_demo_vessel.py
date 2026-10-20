"""Generate a SYNTHETIC MR2 product tanker model for the demo.

Every number here comes from simplified box-hull formulas, not from a real ship.
Never use this data for an actual loading decision.

Run:  python tools/generate_demo_vessel.py
Writes: data/vessels/demo_mr2.json
"""
import json
import math
from pathlib import Path

RHO_SW = 1.025
LBP, B, D = 176.0, 32.2, 19.1
T_MAX = 12.2
DB, DS = 2.0, 2.0  # double bottom height, double side width


def hull_at(T: float) -> dict:
    """Very simplified hydrostatics for a full-bodied tanker hull."""
    cb = 0.72 + 0.10 * (T / T_MAX)
    cw = 0.86 + 0.04 * (T / T_MAX)
    vol = LBP * B * T * cb
    disp = vol * RHO_SW
    kb = T * (0.50 + 0.03 * (T / T_MAX))
    i_t = 0.075 * LBP * B ** 3 * cw  # transverse waterplane inertia (approx.)
    i_l = 0.050 * B * LBP ** 3 * cw  # longitudinal waterplane inertia (approx.)
    bm_t = i_t / vol
    bm_l = i_l / vol
    lcb = LBP * (0.525 - 0.010 * (T / T_MAX))
    lcf = LBP * (0.500 - 0.015 * (T / T_MAX))
    mctc = disp * bm_l / (100 * LBP)
    tpc = RHO_SW * cw * LBP * B / 100
    return dict(draught=round(T, 2), displacement=round(disp, 1), lcb=round(lcb, 3),
                lcf=round(lcf, 3), kmt=round(kb + bm_t, 3), mctc=round(mctc, 1),
                tpc=round(tpc, 2), _bm=bm_t)


def kn_values(row: dict, angles: list) -> list:
    """Wall-sided formula until the deck edge immerses or the bilge emerges, then a smooth decay."""
    T = row["draught"]
    km, bm = row["kmt"], row["_bm"]
    deck_edge = min(math.atan(2 * (D - T) / B), math.atan(2 * T / B))
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


def tank(tid, name, ttype, side, x_aft, x_fwd, y_in, y_out, z_bot, z_top,
         shape=0.95, max_fill=98, coating=None, line=None):
    sign = 1 if side == "S" else -1
    rows, cap = sounding_table(x_aft, x_fwd, sign * y_in, sign * y_out, z_bot, z_top, shape)
    t = dict(id=tid, name=name, type=ttype, side=side, xAft=x_aft, xFwd=x_fwd,
             capacity100=cap, maxFillPct=max_fill, soundingTable=rows)
    if coating:
        t["coating"] = coating
    if line:
        t["lineId"] = line
    return t


def build() -> dict:
    drafts = [round(2.0 + 0.5 * i, 2) for i in range(int((T_MAX - 2.0) / 0.5) + 1)]
    if drafts[-1] != T_MAX:
        drafts.append(T_MAX)
    hyd = [hull_at(T) for T in drafts]
    angles = [0, 10, 20, 30, 40, 50, 60]
    cross = dict(heelAnglesDeg=angles,
                 rows=[dict(displacement=r["displacement"], kn=kn_values(r, angles)) for r in hyd])
    for r in hyd:
        r.pop("_bm")

    tanks = []
    inner = (B / 2) - DS
    # 6 pairs of cargo tanks between x = 33 and x = 153, centreline bulkhead
    bounds = [33 + 20 * i for i in range(7)]
    for n in range(6):
        no = 6 - n  # COT1 is forward
        shape = 0.90 if no in (1, 6) else 0.97
        for side in ("P", "S"):
            tanks.append(tank(f"COT{no}{side}", f"Cargo tank {no} {'port' if side == 'P' else 'starboard'}",
                              "cargo", side, bounds[n], bounds[n + 1], 0.0, inner, DB, D,
                              shape=shape, coating="epoxy", line=f"L{(no + 1) // 2}"))
    for side in ("P", "S"):
        tanks.append(tank(f"SLOP{side}", f"Slop tank {'port' if side == 'P' else 'starboard'}",
                          "slop", side, 29, 33, 0.0, inner, DB, D, coating="epoxy", line="L4"))
    # ballast: double hull wing + bottom, modelled as one box per side per cargo tank pair
    for n in range(6):
        no = 6 - n
        for side in ("P", "S"):
            tanks.append(tank(f"WBT{no}{side}", f"Water ballast {no} {'port' if side == 'P' else 'starboard'}",
                              "ballast", side, bounds[n], bounds[n + 1], inner, B / 2, 0.0, D,
                              shape=0.80, max_fill=100))
    tanks.append(tank("FPT", "Forepeak tank", "ballast", "C", 160, 172, -6, 6, 0.0, 12,
                      shape=0.55, max_fill=100))
    for side in ("P", "S"):
        tanks.append(tank(f"FO{side}", f"Fuel oil {'port' if side == 'P' else 'starboard'}",
                          "fuel_oil", side, 18, 26, 6.0, 12.0, DB, 14))
    tanks.append(tank("DO", "Diesel oil", "diesel_oil", "C", 14, 18, -3, 3, DB, 10))
    tanks.append(tank("FW", "Fresh water", "fresh_water", "C", 2, 8, -5, 5, 8, 14, max_fill=100))

    return {
        "id": "demo_mr2",
        "schemaVersion": "1.0",
        "source": {"origin": "synthetic",
                   "notes": "Fictional MR2 generated by tools/generate_demo_vessel.py. Not a real ship."},
        "particulars": {"name": "MT Demo Fjord", "vesselType": "product_tanker", "sizeClass": "MR2",
                        "lbp": LBP, "breadth": B, "depth": D, "summerDraught": T_MAX,
                        "deadweight": round(hyd[-1]["displacement"] - 10400 - 0, 0),
                        "serviceSpeedKn": 14.0, "seawaterDensity": RHO_SW},
        "lightship": {"weight": 10400, "lcg": 80.5, "vcg": 11.2, "tcg": 0.0,
                      "distribution": [
                          {"xAft": 0, "xFwd": 30, "weight": 3400},
                          {"xAft": 30, "xFwd": 152, "weight": 5900},
                          {"xAft": 152, "xFwd": 176, "weight": 1100}]},
        "hydrostatics": hyd,
        "crossCurves": cross,
        "tanks": tanks,
        "cargoSystem": {
            "pumps": [{"id": f"CP{i}", "ratedM3h": 550} for i in range(1, 5)],
            "lines": [
                {"id": "L1", "pumpId": "CP1", "tankIds": ["COT1P", "COT1S", "COT2P", "COT2S"]},
                {"id": "L2", "pumpId": "CP2", "tankIds": ["COT3P", "COT3S", "COT4P", "COT4S"]},
                {"id": "L3", "pumpId": "CP3", "tankIds": ["COT5P", "COT5S", "COT6P", "COT6S"]},
                {"id": "L4", "pumpId": "CP4", "tankIds": ["SLOPP", "SLOPS"]}],
            "crossovers": [
                {"id": "X12", "lineIds": ["L1", "L2"], "valveCount": 2},
                {"id": "X23", "lineIds": ["L2", "L3"], "valveCount": 2}],
            "maxSegregations": 3},
        "strength": {"readingPoints": []},  # TODO: add when shear/bending is implemented
        "draughtMarks": {"forwardX": LBP, "midshipX": LBP / 2, "aftX": 0.0},
        "constants": [{"name": "Crew, stores and provisions", "weight": 300,
                       "lcg": 20.0, "vcg": 15.0, "tcg": 0.0}],
        # Values from IMO 2008 IS Code Part A 2.2. Verify against the vessel's own booklet.
        "stabilityCriteria": [
            {"id": "GM0_MIN", "description": "Initial GM (corrected for free surface)", "requiredValue": 0.15, "unit": "m"},
            {"id": "AREA_0_30", "description": "Area under GZ curve 0 to 30 degrees", "requiredValue": 0.055, "unit": "m.rad"},
            {"id": "AREA_0_40", "description": "Area under GZ curve 0 to 40 degrees", "requiredValue": 0.09, "unit": "m.rad"},
            {"id": "AREA_30_40", "description": "Area under GZ curve 30 to 40 degrees", "requiredValue": 0.03, "unit": "m.rad"},
            {"id": "GZ_AT_30", "description": "GZ at 30 degrees or more", "requiredValue": 0.20, "unit": "m"},
            {"id": "ANGLE_GZ_MAX", "description": "Angle of maximum GZ", "requiredValue": 25, "unit": "deg"}],
    }


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "data" / "vessels" / "demo_mr2.json"
    out.write_text(json.dumps(build(), indent=2))
    print(f"Wrote {out}")

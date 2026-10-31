"""Ballast preview: how much water to take into which ballast tanks so the trim comes back to normal.

A preview only: it never changes a plan or its pass/fail result. Deterministic, using the stability engine.
Target: slightly by the stern (0.3-1.0 m), as tankers usually prefer: propeller and rudder well immersed and
tanks draining aft. Pairs are filled one at a time, the tanks that move the trim most first (aft tanks when
the ship is down by the head, forward ones when too far down by the stern), in 10% steps refined to land in
the band, never over the summer draught, a stability or strength limit, or past the band.
"""
from __future__ import annotations

import re

from ..engine.stability import StabilityError, TankLoad, calculate, interp

TRIM_MIN, TRIM_MAX = 0.3, 1.0  # m by the stern
STEPS = 10
REFINE = 7


def _ok(r, base_passed: bool) -> bool:
    if any("overloaded" in w for w in r.warnings):
        return False
    return r.all_passed or not base_passed  # never make a passing condition fail


def preview(vessel: dict, loads: list[TankLoad]) -> dict | None:
    """Ballast that brings the trim into 0.3-1.0 m by the stern, or the best achievable; None if already in band."""
    try:
        base = calculate(vessel, loads)
    except StabilityError:
        return None
    t0 = base.trim_m
    if TRIM_MIN <= t0 <= TRIM_MAX:
        return None
    need_stern = t0 < TRIM_MIN
    rho = (vessel.get("particulars") or {}).get("seawaterDensity", 1.025)
    hyd = vessel["hydrostatics"]
    lcf = interp(base.displacement_t, [r["displacement"] for r in hyd], [r["lcf"] for r in hyd])
    used = {l.tank_id for l in loads if l.volume_m3 > 0}

    groups: dict[str, list[dict]] = {}
    for t in vessel["tanks"]:
        if t["type"] == "ballast" and t["id"] not in used:
            groups.setdefault(re.sub(r"[PS]$", "", t["id"]) if t["side"] in ("P", "S") else t["id"], []).append(t)

    def lcg(ts):
        return sum(t["soundingTable"][-1]["lcg"] for t in ts) / len(ts)

    # Tanks on the side of the centre of flotation that moves the trim the right way, furthest first.
    pairs = sorted((ts for ts in groups.values() if (lcg(ts) < lcf) == need_stern), key=lambda ts: -abs(lcg(ts) - lcf))

    fill: dict[str, float] = {}

    def run(extra: dict[str, float]):
        water = [TankLoad(tid, vol, rho, "Ballast") for tid, vol in {**fill, **extra}.items() if vol > 0]
        return calculate(vessel, loads + water)

    def in_band(r) -> bool:
        return TRIM_MIN <= r.trim_m <= TRIM_MAX

    def past(r) -> bool:
        return r.trim_m > TRIM_MAX if need_stern else r.trim_m < TRIM_MIN

    best = base
    limited = False  # stopped by a limit (draught, stability, strength) rather than running out of tanks
    for ts in pairs:
        cap = {t["id"]: t["capacity100"] * t.get("maxFillPct", 98) / 100 for t in ts}
        good_f, good_r = 0.0, None
        for k in range(1, STEPS + 1):
            f = k / STEPS
            r = run({tid: c * f for tid, c in cap.items()})
            if not _ok(r, base.all_passed) or past(r):
                limited = limited or not _ok(r, base.all_passed)
                lo, hi = good_f, f  # land inside the band, or just short of the limit that stopped us
                for _ in range(REFINE):
                    mid = (lo + hi) / 2
                    rm = run({tid: c * mid for tid, c in cap.items()})
                    if _ok(rm, base.all_passed) and not past(rm):
                        lo, good_r = mid, rm
                    else:
                        hi = mid
                good_f = lo
                break
            good_f, good_r = f, r
            if in_band(r):
                break
        if good_r is not None and good_f > 0:
            fill.update({tid: c * good_f for tid, c in cap.items()})
            best = good_r
        if in_band(best):
            break

    if not fill:
        return {"tanks": [], "total_t": 0, "trim_before": round(t0, 2), "trim_after": round(t0, 2), "reached": False,
                "note": f"No ballast tank can move the trim {'aft' if need_stern else 'forward'} without breaking a limit."}
    tanks = {t["id"]: t for t in vessel["tanks"]}
    rows = [{"tank": tid, "m3": round(vol), "t": round(vol * rho), "pct": round(100 * vol / tanks[tid]["capacity100"])}
            for tid, vol in sorted(fill.items())]
    reached = in_band(best)
    side = lambda v: f"{abs(v):.2f} m by the {'stern' if v >= 0 else 'head'}"  # noqa: E731
    where = "aft" if need_stern else "forward"
    note = None if reached else (
        f"Best achievable is {side(best.trim_m)}: " + (
            f"more {where} ballast would break a draught, stability or strength limit." if limited
            else f"all {where} ballast tanks are full. Move cargo {'aft' if need_stern else 'forward'} to correct the rest."))
    return {"tanks": rows, "total_t": sum(r["t"] for r in rows), "trim_before": round(t0, 2), "trim_after": round(best.trim_m, 2),
            "draught_fwd_m": best.draught_fwd_m, "draught_aft_m": best.draught_aft_m, "gm_m": best.gm_fluid_m,
            "max_stress_pct": max(best.max_sf_pct or 0, best.max_bm_pct or 0) if best.max_sf_pct is not None else None,
            "passed": best.all_passed, "reached": reached, "note": note}

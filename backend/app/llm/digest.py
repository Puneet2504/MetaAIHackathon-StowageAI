"""Small summaries of engine and solver results, so the model only gets what it needs to explain them.

No GZ curve, sounding data or per-tank volumes: they cost tokens and the explanation doesn't use them.
"""
import json


def compact(data) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _r(v, digits: int = 2):
    return round(v, digits) if isinstance(v, (int, float)) else v


def stability_digest(r: dict) -> dict:
    crit = r.get("criteria", [])
    gm_min = next((c["required"] for c in crit if c["id"] == "GM0_MIN"), None)
    return {
        "ok": bool(r.get("all_passed")),
        "checks": len(crit),
        "fails": [{"id": c["id"], "name": c["description"], "actual": _r(c["actual"], 3), "min": c["required"],
                   "unit": c["unit"]} for c in crit if not c["passed"]],
        "disp_t": _r(r.get("displacement_t"), 0),
        "draft_fwd_m": _r(r.get("draught_fwd_m")),
        "draft_aft_m": _r(r.get("draught_aft_m")),
        "trim_m": _r(abs(r.get("trim_m") or 0)),
        "trim_by": "stern" if (r.get("trim_m") or 0) >= 0 else "head",
        "list_deg": _r(r.get("list_deg"), 1),
        "gm_m": _r(r.get("gm_fluid_m")),
        "gm_min_m": gm_min,
        "slosh_m": _r(r.get("free_surface_correction_m")),
        "warnings": r.get("warnings", []),
    }


def _tanks(ids: list[str]) -> str:
    """COT1P, COT1S, COT2P -> 'COT1 P/S, COT2P' (fewer tokens, easier to read)."""
    out, seen = [], set(ids)
    for t in ids:
        if t[-1:] in "PS" and t[:-1] + ("S" if t[-1] == "P" else "P") in seen:
            if t[-1] == "P":
                out.append(f"{t[:-1]} P/S")
        else:
            out.append(t)
    return ", ".join(out)


def plan_digest(plan: dict) -> dict:
    return {
        "status": plan.get("status"),
        "summary": plan.get("message"),
        "alloc": [{"grade": a["grade"], "tanks": _tanks(a["tanks"]),
                   **({"left_m3": round(a["unallocated_m3"])} if a["unallocated_m3"] > 0.5 else {})}
                  for a in plan.get("allocation", [])],
        "stability": stability_digest(plan["result"]) if plan.get("result") else None,
        "warnings": [w for w in plan.get("warnings", []) if w not in (plan.get("result") or {}).get("warnings", [])],
    }


def _fmt(v, unit: str, digits: int = 2) -> str | None:
    return None if v is None else f"{v:,.{digits}f} {unit}".strip()


def for_model(d: dict) -> dict:
    """The digest as the model sees it: plain key names, values already carrying their units."""
    if "stability" in d:  # a plan digest
        return {"status": d["status"], "summary": d["summary"],
                "cargo": [f"{a['grade']}: {a['tanks']}" + (f" ({a['left_m3']:,} m3 does not fit)" if a.get("left_m3") else "")
                          for a in d["alloc"]],
                "stability": for_model(d["stability"]) if d["stability"] else None,
                "warnings": d["warnings"]}
    return {
        "safe": d["ok"], "checks": d["checks"],
        "failed_checks": [f"{f['name']}: {f['actual']} {f['unit']} (minimum {f['min']} {f['unit']})" for f in d["fails"]],
        "total_weight": _fmt(d["disp_t"], "t", 0),
        "depth_in_water": f"{_fmt(d['draft_fwd_m'], 'm')} at the front, {_fmt(d['draft_aft_m'], 'm')} at the back",
        "trim": f"{_fmt(d['trim_m'], 'm')} deeper at the {d['trim_by']}",
        "list": "cannot stay upright" if d["list_deg"] is None else _fmt(abs(d["list_deg"]), "degrees", 1),
        "GM": f"{_fmt(d['gm_m'], 'm')} (minimum {_fmt(d['gm_min_m'], 'm')})",
        "slosh_loss": _fmt(d["slosh_m"], "m"),
        "warnings": d["warnings"],
    }

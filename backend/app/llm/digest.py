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
        # How each parcel's quantity became tank volume, and what was assumed, so explanations state the basis.
        "basis": [_basis(a) for a in plan.get("allocation", []) if a.get("conversion")],
        "assumed": [f"{a['grade']} {k}" for a in plan.get("allocation", []) for k in _assumed(a.get("conversion"))],
        # Later ports where emptied tanks are reloaded, and tanks that change grade; only when there are any.
        **({"stages": [f"At {s['port']}: {', '.join(a['grade'] for a in s['allocation'])} loaded into "
                       f"{_tanks([t for a in s['allocation'] for t in a['tanks']])}; "
                       f"{'passes' if (s.get('result') or {}).get('all_passed') else 'fails'} stability"
                       for s in plan.get("stages") or []]} if plan.get("stages") else {}),
        **({"cleaning": [f"{g['port']}: {_tanks(g['tanks'])} {g['change']} need {g['label'].lower()}"
                         for g in _cleaning_groups(plan.get("cleaning"))]
                        + [f"{p['port']} cleaning est. {p['estimate']['hours']:g} h, USD {p['estimate']['cost_usd']:,}"
                           for p in plan["cleaning"]["ports"] if p.get("estimate")]}
           if (plan.get("cleaning") or {}).get("ports") else {}),
        **({"cleaning_saving": saving_text(plan["cleaning"]["saving"])}
           if (plan.get("cleaning") or {}).get("saving") else {}),
    }


def _cleaning_groups(report: dict | None) -> list[dict]:
    """Tanks with the same change of grade at the same port, grouped (fewer tokens than one line per tank)."""
    groups: dict[tuple, dict] = {}
    for port in (report or {}).get("ports", []):
        for c in port["changes"]:
            key = (port["port"], c["prev"], c["next"])
            g = groups.setdefault(key, {"port": port["port"], "when": port["when"], "tanks": [],
                                        "change": f"{c['prev_name']} -> {c['next_name']}", "label": c["label"],
                                        "level": c["level"], "reason": c["reason"], "steps": c["steps"], "checks": c["checks"]})
            g["tanks"].append(c["tank"])
    return list(groups.values())


def saving_text(s: dict) -> str:
    """How much cleaning the look-ahead saved, for the model and the built-in explanation."""
    return (f"Tanks chosen with the {s['port']} loading in mind: about {s['hours']:g} h and USD {s['cost_usd']:,} "
            f"less cleaning than {s['compared_to']}")


def cleaning_digest(report: dict) -> dict:
    """What the model gets to write a port checklist: grouped tank changes, their fixed steps and checks."""
    return {
        "changes": [{"port": g["port"], "when": g["when"], "tanks": _tanks(g["tanks"]), "change": g["change"],
                     "cleaning": g["label"], "why": g["reason"], "steps": g["steps"], "checks": g["checks"]}
                    for g in _cleaning_groups(report)],
        "port_checks": {p["port"]: p["checks"] for p in report.get("ports", [])},
        "port_time": {p["port"]: f"about {p['estimate']['hours']:g} h of cleaning" for p in report.get("ports", [])
                      if p.get("estimate")},
        **({"saving": saving_text(report["saving"])} if report.get("saving") else {}),
    }


def _assumed(c: dict | None) -> list[str]:
    """Which conditions were not in the order (no numbers, so plain explanations can use them)."""
    if not c:
        return []
    out = [] if c["temp_source"] == "order" else [f"temperature ({c['temp_source']})"]
    return out + ([] if c["density_source"].startswith("order") else ["density"])


def _basis(a: dict) -> str:
    c = a["conversion"]
    t = "" if c["temp_source"] == "order" else f" ({c['temp_source']})"
    d = "" if c["density_source"].startswith("order") else " (typical)"
    return f"{a['grade']}: {c['ordered']}, {c['temp_c']:g} °C{t}, d15 {c['density15']:.3f}{d} -> {c['volume_m3']:,.0f} m3"


def _fmt(v, unit: str, digits: int = 2) -> str | None:
    return None if v is None else f"{v:,.{digits}f} {unit}".strip()


def for_model(d: dict) -> dict:
    """The digest as the model sees it: plain key names, values already carrying their units."""
    if "stability" in d:  # a plan digest
        return {"status": d["status"], "summary": d["summary"],
                "cargo": [f"{a['grade']}: {a['tanks']}" + (f" ({a['left_m3']:,} m3 does not fit)" if a.get("left_m3") else "")
                          for a in d["alloc"]],
                "stability": for_model(d["stability"]) if d["stability"] else None,
                "warnings": d["warnings"], "cargo_basis": d.get("basis", []), "assumed": d.get("assumed", []),
                **{k: d[k] for k in ("stages", "cleaning", "cleaning_saving") if d.get(k)}}
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

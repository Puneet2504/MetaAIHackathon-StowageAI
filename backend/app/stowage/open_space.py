"""Open space on a ship's current voyage: the facts a charterer's suggestion is made from, and a safety label.

Code here only gathers facts (what is on board and what is empty after each port, lines, last cargoes,
grades, cleaning rules) and, afterwards, labels each AI suggestion "verified" or "needs a planner's
check" with the voyage check. The AI chooses the grade, tanks and quantity and does the checks itself
(app/llm/tasks.open_space); code never picks, filters or changes a suggestion.
"""
from __future__ import annotations

from ..schemas import Parcel
from . import cleaning
from .grades import GRADES, grade_for
from .voyage_check import Cargo, Pipes, _get, _pair, _pairs_text, _same, cargo_from_plan, check, discharge_rotation


def _cargo_tanks(vessel: dict) -> dict[str, dict]:
    return {t["id"]: t for t in vessel["tanks"] if t["type"] == "cargo"}


def _timeline(parcels, cargo: list[Cargo]) -> list[tuple[str, list[str], list[Cargo], dict[str, str]]]:
    """(port, later ports, cargo on board after the port, last cargo of each emptied tank) for each offered port."""
    load_ports = list(dict.fromkeys(_get(p, "load_port") for p in parcels if _get(p, "load_port")))
    dis = discharge_rotation(parcels)
    reload = [d for d in dis if any(_same(d, lp) for lp in load_ports)]
    first = next((lp for lp in load_ports if not any(_same(lp, r) for r in reload)), None) or "first load port"

    def leaves_at(c: Cargo, port: str) -> bool:
        return c.parcel is not None and c.parcel < len(parcels) and _same(_get(parcels[c.parcel], "discharge_port"), port)

    aboard = [c for c in cargo if c.stage == 0]
    last: dict[str, str] = {}
    out = [(first, dis, list(aboard), dict(last))]
    for i, port in enumerate(dis[:-1]):  # cargo loaded at the last port has nowhere to go on this voyage
        for c in aboard:
            if leaves_at(c, port):
                last[c.tank] = c.grade
        aboard = [c for c in aboard if not leaves_at(c, port)]
        stage = next((k + 1 for k, r in enumerate(reload) if _same(r, port)), None)
        if stage:
            aboard += [c for c in cargo if c.stage == stage]
        out.append((port, dis[i + 1:], list(aboard), dict(last)))
    return out


def voyage_facts(vessel: dict, record: dict) -> dict:
    """Compact facts about the current voyage, for the AI to choose what else fits."""
    parcels = record.get("parcels") or []
    cargo = cargo_from_plan(record.get("plan") or {})
    pipes = Pipes(vessel)
    tanks = _cargo_tanks(vessel)
    pairs: dict[str, list[str]] = {}
    for t in tanks:
        pairs.setdefault(_pair(t), []).append(t)
    lines: dict[str, list[str]] = {}
    for t in tanks:
        if t in pipes.line_of:
            lines.setdefault(pipes.line_of[t], []).append(t)

    ports = []
    for port, later, aboard, last in _timeline(parcels, cargo):
        held = {c.tank for c in aboard}
        by_grade: dict[tuple[str, str], list[str]] = {}
        for c in aboard:
            by_grade.setdefault((c.grade, pipes.line_of.get(c.tank, "-")), []).append(c.tank)
        busy = {pipes.line_of.get(c.tank) for c in aboard}
        empty = []
        for key, ids in sorted(pairs.items()):
            if any(t in held for t in ids):
                continue
            prev = {last.get(t) for t in ids} - {None}
            empty.append({"tanks": _pairs_text(ids), "line": pipes.line_of.get(ids[0]),
                          "m3": round(sum(tanks[t]["capacity100"] * tanks[t].get("maxFillPct", 98) / 100 for t in ids)),
                          "last": next(iter(prev)) if prev else None})
        ports.append({"port": port, "later_ports": later,
                      "on_board": [f"{g}: {_pairs_text(ts)} (line {line})" for (g, line), ts in by_grade.items()],
                      "grades_on_board": sorted({c.grade for c in aboard}),
                      "empty": empty, "free_lines": sorted(l for l in lines if l not in busy)})

    on_board = {c.grade: c.density for c in cargo}
    return {
        "ship": (vessel.get("particulars") or {}).get("name"),
        "max_grades": (vessel.get("cargoSystem") or {}).get("maxSegregations") or len(lines),
        "lines": {l: _pairs_text(ts) for l, ts in sorted(lines.items())},
        "ports": ports,
        "grades": {**{g.name: g.density for g in GRADES}, **{g: round(d, 3) for g, d in on_board.items()}},
        "cleaning_rules": [f"{a}->{b}: {cleaning.LEVELS[lvl][1].lower()} ({why})" for (a, b), (lvl, why) in cleaning.RULES.items()]
                          + ["same grade or no last cargo: no cleaning", "anything else: water wash"],
    }


def tank_ids(vessel: dict) -> list[str]:
    return sorted(_cargo_tanks(vessel))


# ---------- the safety label ----------

def verify(vessel: dict, record: dict, facts: dict, s: dict) -> dict:
    """Label one AI suggestion: 'verified' if the voyage check passes with it added, else the first reason."""
    parcels = list(record.get("parcels") or [])
    port = next((p for p in facts["ports"] if _same(p["port"], s.get("port"))), None)

    def needs(why: str) -> dict:
        return {"verified": False, "label": "Needs a planner's check", "why": why}

    if port is None:
        return needs(f"{s.get('port')} is not a port where cargo can be added on this voyage.")
    if not any(_same(d, s.get("discharge_port")) for d in port["later_ports"]):
        return needs(f"{s.get('discharge_port')} is not a later port of this voyage.")
    tanks = _cargo_tanks(vessel)
    empty = {t for e in port["empty"] for t in _expand(e["tanks"])}
    if bad := [t for t in s["tanks"] if t not in empty]:
        return needs(f"{', '.join(bad)} {'is' if len(bad) == 1 else 'are'} not empty at {port['port']}.")
    caps = {t: tanks[t]["capacity100"] for t in s["tanks"]}
    room = sum(c * tanks[t].get("maxFillPct", 98) / 100 for t, c in caps.items())
    if s["quantity_m3"] > room + 1:
        return needs(f"{s['quantity_m3']:,.0f} m³ is more than those tanks hold at 98% ({room:,.0f} m³).")

    lib, _ = grade_for(s["grade"], None)
    density = facts["grades"].get(s["grade"]) or lib.density
    parcels.append(Parcel(grade=s["grade"], quantity_m3=s["quantity_m3"], density_t_m3=density,
                          load_port=port["port"], discharge_port=s["discharge_port"]))
    cargo = cargo_from_plan(record.get("plan") or {})
    total = sum(caps.values())
    cargo += [Cargo(t, lib.id, s["grade"], round(s["quantity_m3"] * c / total, 1), density, len(parcels) - 1)
              for t, c in caps.items()]
    report = check(vessel, parcels, _restage(parcels, cargo), suggest=False)
    if not report["ok"]:
        return needs(next(i["message"] for i in report["issues"] if i["severity"] == "block"))
    worst = max((max(p.get("max_sf_pct") or 0, p.get("max_bm_pct") or 0) for p in report["ports"]), default=0)
    return {"verified": True, "label": "Verified", "why": f"Voyage check passes at every port (hull stress up to {worst:.0f}%)."}


def _expand(pairs_text: str) -> list[str]:
    out = []
    for part in pairs_text.split(", "):
        out += [part[:-4] + "P", part[:-4] + "S"] if part.endswith(" P/S") else [part]
    return out


def _restage(parcels, cargo: list[Cargo]) -> list[Cargo]:
    """Stage numbers from the parcels' load ports, so cargo added at a discharge port boards there."""
    load_ports = list(dict.fromkeys(_get(p, "load_port") for p in parcels if _get(p, "load_port")))
    reload = [d for d in discharge_rotation(parcels) if any(_same(d, lp) for lp in load_ports)]
    out = []
    for c in cargo:
        lp = _get(parcels[c.parcel], "load_port") if c.parcel is not None else None
        stage = next((i + 1 for i, r in enumerate(reload) if _same(r, lp)), 0)
        out.append(Cargo(c.tank, c.grade_id, c.grade, c.volume_m3, c.density, c.parcel, stage))
    return out

"""Scoring for the model test set. No model calls here, so it can be unit tested.

Each scorer takes (expected, actual) and returns a list of problems; an empty list is a pass.
"""
from app.stowage.grades import match_grade


def _grade_key(name: str | None) -> str:
    g = match_grade(name or "")
    return g.id if g else (name or "").strip().lower()


def _same_num(a, b, tol: float = 1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def _same_text(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return str(a).strip().lower() == str(b).strip().lower()


def _invented(field: str, exp, act) -> str:
    return f"{field}: expected {exp}, got {act}" + (" (invented)" if exp is None and act is not None else "")


def _note_problems(expected: dict, actual: dict) -> list[str]:
    if expected.get("needs_note") and not (actual.get("notes") or actual.get("checks")):
        return ["expected a note about missing or unclear information"]
    return []


def score_order(expected: dict, actual: dict) -> list[str]:
    problems = []
    exp, act = expected["parcels"], list(actual.get("parcels") or [])
    if len(exp) != len(act):
        problems.append(f"expected {len(exp)} parcels, got {len(act)}")
    for e in exp:
        # Match on grade, then on discharge port when one grade goes to several ports.
        cands = [a for a in act if _grade_key(a.get("grade")) == _grade_key(e["grade"])]
        if len(cands) > 1:
            cands = [a for a in cands if _same_text(a.get("discharge_port"), e["discharge_port"])] or cands
        if not cands:
            problems.append(f"missing parcel {e['grade']}")
            continue
        a = cands[0]
        act.remove(a)
        for f in ("quantity_t", "quantity_m3", "density_t_m3"):
            if not _same_num(e[f], a.get(f)):
                problems.append(f"{e['grade']} " + _invented(f, e[f], a.get(f)))
        for f in ("load_port", "discharge_port"):
            if not _same_text(e[f], a.get(f)):
                problems.append(f"{e['grade']} " + _invented(f, e[f], a.get(f)))
        # Cargo conditions, checked only in cases that state them.
        for f in ("quantity_bbl", "temp_c", "density_temp_c", "api"):
            if f in e and not _same_num(e[f], a.get(f)):
                problems.append(f"{e['grade']} " + _invented(f, e[f], a.get(f)))
        if "mass_basis" in e and not _same_text(e["mass_basis"], a.get("mass_basis")):
            problems.append(f"{e['grade']} " + _invented("mass_basis", e["mass_basis"], a.get("mass_basis")))
    return problems + _note_problems(expected, actual)


def score_drawing(expected: dict, actual: dict) -> list[str]:
    problems = []
    exp, act = expected["layout"], actual.get("layout") or {}
    if exp.get("name") and not _same_text(exp["name"], act.get("name")):
        problems.append(f"name: expected {exp['name']}, got {act.get('name')}")
    for f in ("lbp", "breadth", "depth", "summerDraught"):
        if not _same_num(exp[f], act.get(f), 0.1):
            problems.append(_invented(f, exp[f], act.get(f)))
    act_tanks = {str(t.get("id", "")).upper(): t for t in act.get("tanks") or []}
    exp_ids = {t["id"] for t in exp["tanks"]}
    if missing := sorted(exp_ids - set(act_tanks)):
        problems.append(f"missing tanks: {', '.join(missing)}")
    if extra := sorted(set(act_tanks) - exp_ids):
        problems.append(f"unexpected tanks: {', '.join(extra)}")
    for e in exp["tanks"]:
        a = act_tanks.get(e["id"])
        if a is None:
            continue
        for f in ("type", "side"):
            if e[f] != a.get(f):
                problems.append(f"{e['id']} {f}: expected {e[f]}, got {a.get(f)}")
        for f in ("xAft", "xFwd"):
            if not _same_num(e[f], a.get(f), 0.5):
                problems.append(f"{e['id']} " + _invented(f, e[f], a.get(f)))
        if not _same_num(e["capacity100"], a.get("capacity100"), 0.5):
            problems.append(f"{e['id']} " + _invented("capacity100", e["capacity100"], a.get("capacity100")))
        if e.get("lineId") and not _same_text(e["lineId"], a.get("lineId")):
            problems.append(f"{e['id']} lineId: expected {e['lineId']}, got {a.get('lineId')}")
    return problems + _note_problems(expected, actual)


def score_instruction(expected: dict, actual: dict) -> list[str]:
    problems = []
    exp, act = expected["constraints"], actual.get("constraints") or {}
    if set(exp["empty_tanks"]) != set(act.get("empty_tanks") or []):
        problems.append(f"empty_tanks: expected {sorted(exp['empty_tanks'])}, got {sorted(act.get('empty_tanks') or [])}")
    act_fixed = {k: _grade_key(v) for k, v in (act.get("fixed_grades") or {}).items()}
    if {k: _grade_key(v) for k, v in exp["fixed_grades"].items()} != act_fixed:
        problems.append(f"fixed_grades: expected {exp['fixed_grades']}, got {act.get('fixed_grades')}")
    if not _same_num(exp["max_trim_m"], act.get("max_trim_m")):
        problems.append(f"max_trim_m: expected {exp['max_trim_m']}, got {act.get('max_trim_m')}")
    return problems + _note_problems(expected, actual)


def score_open_space(expected: dict, actual: dict, context: dict | None = None) -> list[str]:
    """Every suggestion must keep to the facts (empty tanks, 98% room, one grade per line, a later port),
    and the best one must be the expected 'perfect fit' grade."""
    problems = []
    sugg = actual.get("suggestions") or []
    if not sugg:
        return ["no suggestions"]
    if not _same_text(sugg[0].get("grade"), expected["first_grade"]) and \
            _grade_key(sugg[0].get("grade")) != _grade_key(expected["first_grade"]):
        problems.append(f"best grade: expected {expected['first_grade']}, got {sugg[0].get('grade')}")
    for s in sugg if context else []:
        port = next((p for p in context["ports"] if _same_text(p["port"], s.get("port"))), None)
        if port is None or not any(_same_text(d, s.get("discharge_port")) for d in port["later_ports"]):
            problems.append(f"{s.get('grade')}: port {s.get('port')} -> {s.get('discharge_port')} not in the voyage")
            continue
        empty = {}
        for e in port["empty"]:
            for part in e["tanks"].split(", "):
                for t in ([part[:-4] + "P", part[:-4] + "S"] if part.endswith(" P/S") else [part]):
                    empty[t] = e
        if bad := [t for t in s.get("tanks", []) if t not in empty]:
            problems.append(f"{s.get('grade')}: tanks not empty at {port['port']}: {bad}")
            continue
        room = sum({id(empty[t]): empty[t]["m3"] for t in s["tanks"]}.values())
        if s.get("quantity_m3", 0) > room + 1:
            problems.append(f"{s.get('grade')}: {s.get('quantity_m3')} m3 is more than the {room} m3 of room")
        busy = {line.split("(line ")[-1].rstrip(")"): line.split(":")[0] for line in port["on_board"]}
        for t in s["tanks"]:
            line = empty[t]["line"]
            if line in busy and _grade_key(busy[line]) != _grade_key(s.get("grade")):
                problems.append(f"{s.get('grade')}: line {line} carries {busy[line]}")
                break
    return problems


SCORERS = {"orders": score_order, "drawings": score_drawing, "instructions": score_instruction,
           "openspace": score_open_space}

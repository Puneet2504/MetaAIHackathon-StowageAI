"""Plain-language explanations built from a digest, with no model call.

Used when no model is configured or the call fails, and shown at once while the AI reply is on its way.
Same three parts the AI is asked for: Verdict, Why, What to do.
"""

# What each failed check means, in everyday words.
FAIL_WORDS = {
    "GM0_MIN": "it would not push itself back upright firmly enough after a roll",
    "AREA_0_30": "it would not resist being rolled over to 30 degrees strongly enough",
    "AREA_0_40": "it would not resist being rolled over to 40 degrees strongly enough",
    "AREA_30_40": "it loses too much of its fight-back between 30 and 40 degrees of roll",
    "GZ_AT_30": "at a 30 degree roll it would not push back hard enough",
    "ANGLE_GZ_MAX": "it reaches its strongest push-back too early in a roll",
    "STRENGTH_LIMIT": "the cargo would bend or shear the hull beyond its safe limit at sea",
}


def _num(v, unit: str = "", digits: int = 2) -> str:
    return f"{v:.{digits}f}{unit}" if isinstance(v, (int, float)) else "n/a"


def plain_stability(d: dict) -> list[str]:
    why = []
    if d["gm_m"] is not None:
        firm = "firmly" if d["ok"] or not any(f["id"] == "GM0_MIN" for f in d["fails"]) else "too weakly"
        why.append(f"It pushes itself back upright {firm} (GM, the 'self-righting' measure, is {_num(d['gm_m'], ' m')}; "
                   f"the minimum is {_num(d['gm_min_m'], ' m')}).")
    lean = d["list_deg"]
    if lean is None:
        why.append("It cannot stay upright on its own in this condition.")
    elif abs(lean) < 0.05:
        why.append(f"It sits level side to side, and {_num(d['trim_m'], ' m', 2)} lower at the {d['trim_by']} "
                   f"than at the other end (trim).")
    else:
        why.append(f"It leans {_num(abs(lean), '°', 1)} to one side (list), and sits {_num(d['trim_m'], ' m')} "
                   f"lower at the {d['trim_by']} (trim).")
    for f in d["fails"][:2]:
        bound = "maximum" if f.get("limit") == "max" else "minimum"
        why.append(f"Problem: {FAIL_WORDS.get(f['id'], f['name'].lower())} "
                   f"({_num(f['actual'], ' ' + f['unit'], 2)} against a {bound} of {f['min']} {f['unit']}).")
    if d.get("bm_pct") is not None and d["bm_pct"] >= 85 and not any(f["id"] == "STRENGTH_LIMIT" for f in d["fails"]):
        why.append(f"The hull is close to its safe bending limit ({d['bm_mode']}): the cargo is bunched "
                   f"{'in the middle' if d['bm_mode'] == 'sagging' else 'towards the ends'} of the ship.")
    return why[:3]


def _todo(d: dict) -> list[str]:
    if d["ok"] and not d["warnings"]:
        return ["Nothing to change. A planner should still check the plan before loading starts."]
    todo = []
    ids = {f["id"] for f in d["fails"]}
    if ids & {"GM0_MIN", "AREA_0_30", "AREA_0_40", "AREA_30_40", "GZ_AT_30", "ANGLE_GZ_MAX"}:
        todo.append("Fill tanks fully or leave them empty: half-full tanks let liquid slosh and make the ship less steady.")
    if d["list_deg"] is None or (d["list_deg"] and abs(d["list_deg"]) >= 0.5):
        todo.append("Balance the cargo between the left (port) and right (starboard) tanks.")
    if "STRENGTH_LIMIT" in ids:
        todo.append("Spread the cargo more evenly along the ship: heavy cargo bunched in the middle or at the ends "
                    "bends the hull.")
    if any("overloaded" in w for w in d["warnings"]):
        todo.append("Load less cargo: the ship would be deeper than its safe limit.")
    if any("trim" in w.lower() for w in d["warnings"]):
        todo.append("Move some cargo towards the other end of the ship to level it.")
    if not todo:
        todo.append("Check the warnings with a planner before loading.")
    return todo[:2]


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)


def explain_stability(d: dict) -> str:
    verdict = (f"Safe to sail as planned: the ship passes all {d['checks']} stability checks." if d["ok"]
               else f"Not safe as planned: the ship fails {len(d['fails']) or 'some'} of its {d['checks']} stability checks.")
    return f"Verdict: {verdict}\nWhy:\n{_bullets(plain_stability(d))}\nWhat to do:\n{_bullets(_todo(d))}"


def cleaning_checklist(d: dict) -> str:
    """The port checklist from the cleaning rules, used when the model is unavailable."""
    arrival, berth, sailing = [], [], []
    for c in d["changes"]:
        where = f"{c['port']}, {c['tanks']}"
        arrival.append(f"{where}: {c['cleaning'].lower()} for {c['change']} ({c['why']}).")
        if c["steps"]:
            berth.append(f"{where}: " + "; ".join(s[0].lower() + s[1:] for s in c["steps"]) + ".")
        berth.append(f"{where}, checks: " + "; ".join(c["checks"]) + ".")
    for port, time in d.get("port_time", {}).items():
        arrival.append(f"{port}: allow {time} in the berth plan.")
    for port, checks in d["port_checks"].items():
        arrival.append(f"{port}: {checks[0]}.")
        berth.append(f"{port}, port checks: " + "; ".join(checks[1:]) + ".")
    sailing.append("Record the cleaning, inspections and test results for each tank in the cargo documents.")
    return ("Before arrival:\n" + _bullets(arrival) + "\nAt berth before loading:\n" + _bullets(berth)
            + "\nBefore sailing:\n" + _bullets(sailing))


def assumed_note(p: dict) -> str:
    """What to check when a parcel's temperature or density was not in the order (no numbers)."""
    return ("Check against the cargo documents: these were not in the order, so typical values were used: "
            + ", ".join(p["assumed"][:3]) + ".")


def explain_plan(p: dict) -> str:
    d = p.get("stability")
    if d is None:
        return f"Verdict: No plan could be made. {p.get('summary') or ''}".strip()
    where = [f"{a['grade']} goes in {a['tanks']}" + (f", but {a['left_m3']:,} m³ does not fit" if a.get("left_m3") else "")
             for a in p["alloc"]]
    verdict = ("Safe to sail as planned: every parcel is placed and the ship passes all its stability checks."
               if p["status"] == "planned" else p.get("summary") or "The plan needs attention.")
    why = ["; ".join(where) + "."] if where else []
    why += plain_stability(d)
    todo = _todo(d)
    if p.get("assumed"):  # temperature or density not from the order
        todo = todo[:1] + [assumed_note(p)]
    if p.get("cleaning_saving"):
        why = why[:2] + [p["cleaning_saving"] + "."]
    blocking = [l for l in p.get("voyage_check", []) if l.endswith("(blocking)")]
    if blocking:  # cargo trapped, lines shared or unsafe after a port: not safe whatever the departure check says
        verdict = "Not safe as planned: the voyage check found a problem at a later port or in the pipelines."
        why = [blocking[0].removesuffix(" (blocking)")] + why[:2]
        todo = ["Apply the suggested tank swaps, or replan, before approving."] + todo[:1]
    return f"Verdict: {verdict}\nWhy:\n{_bullets(why[:3])}\nWhat to do:\n{_bullets(todo)}"

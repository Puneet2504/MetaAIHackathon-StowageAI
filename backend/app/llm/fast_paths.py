"""Common planner instructions handled by code, so they cost no tokens.

try_instruction returns None unless it understands the WHOLE instruction; anything else goes to the model.
Handles: keep/leave tank(s) empty, use tank(s) again, trim limits, "<grade> in tank(s)",
"X means <grade>", even or full tank filling, cargo temperature and density, and "always / from now on / remember / forget" for memory.
"""
import re

from ..schemas import ConstraintsIn, InstructionOut, MemoryOp, ParcelUpdate
from ..stowage.grades import match_grade

STANDING = re.compile(r"\b(always|from now on|by default|every time|remember( that)?|permanently)\b", re.I)
THIS_SHIP = re.compile(r"\b(on|for|with)?\s*this (ship|vessel)\b", re.I)
FORGET = re.compile(r"^(forget|stop|no longer|remove the rule|drop the rule)\b", re.I)
TANK_REF = re.compile(r"(?:cot)?(\d{1,2})\s*(p|s|port|stbd|starboard)?(?:\s*/\s*(p|s))?\b", re.I)
# How full to fill tanks. "Even": spread each grade over all its tanks at the same level, rather than
# filling some to 98% and leaving others empty. Also used to recognise such a rule written as a note.
EVEN_FILL = re.compile(
    r"\b(spread|distribute|share)\b.*\b(even|evenly|equal|equally|across|over)\b"
    r"|\b(fill|load)\b.*\b(even|evenly|equal|equally|same level)\b"
    r"|\b(use|fill)\s+(all|every)\s+(the\s+)?(cargo\s+)?(tanks?|containers?)\b"
    r"|(\bnot\b|n'?t\b|\bnever\b|\bless than\b|\bbelow\b|\bunder\b|\blower than\b).*\b98\s*%"
    r"|\ball\s+(the\s+)?(tanks|containers)\s+(are\s+)?(filled|used)\b", re.I)
FULL_FILL = re.compile(
    r"\b(fill|load)\b.*(\b(full|fully|to the max|maximum)\b|\b98\s*%)|\bstop spreading\b|\bno (even|equal) fill", re.I)


def fill_rule(text: str) -> str | None:
    """'even' or 'full' if the text is a rule about how full to fill tanks, else None."""
    if EVEN_FILL.search(text):
        return "even"
    if FULL_FILL.search(text):
        return "full"
    return None


FILLER = re.compile(r"\b(the|tanks?|no\.?|number|and|both|sides?|wing|cargo|rule|rules|about|for|of|please)\b|[,&+/.]", re.I)


def _side(s: str | None) -> str | None:
    if not s:
        return None
    return "P" if s.lower().startswith("p") else "S"


def _tanks(text: str, cargo_tanks: list[str]) -> tuple[list[str], list[str]] | None:
    """Tank ids named in text, and names that don't exist. None if text has other words."""
    if FILLER.sub(" ", TANK_REF.sub(" ", text)).strip():
        return None
    found, unknown = [], []
    for no, side, side2 in TANK_REF.findall(text):
        sides = [x for x in (_side(side), _side(side2)) if x] or ["P", "S"]
        for s in sides:
            tid = f"COT{int(no)}{s}"
            (found if tid in cargo_tanks else unknown).append(tid)
    return (found, unknown) if found or unknown else None


def _grade(name: str, grades: list[str], aliases: dict[str, str] | None = None) -> str | None:
    """The order's grade name the planner meant, or None. `aliases` are the planner's remembered names."""
    want = match_grade(name, aliases)
    for g in grades:
        if g.lower() == name.strip().lower() or (want and (mg := match_grade(g, aliases)) and mg.id == want.id):
            return g
    return None


# "cargo temperature 35 C", "ULSD at 35°C", "temp 35", "density 0.835 at 15 C", "jet density 0.80".
TEMP = re.compile(r"(?:(?P<grade>.+?)\s+)?(?:(?:cargo\s+)?(?:temperature|temp)\s*(?:is|of|=|:|to)?\s*|at\s+)"
                  r"(?P<t>-?\d+(?:\.\d+)?)\s*(?:°\s*c|deg(?:rees)?\s*c?|c)?", re.I)
DENSITY = re.compile(r"(?:(?P<grade>.+?)\s+)?(?:density|dens)\s*(?:is|of|=|:)?\s*(?P<d>\d+(?:\.\d+)?)"
                     r"(?:\s*(?:t/m3|kg/m3|kg/l))?(?:\s+at\s+(?P<t>-?\d+(?:\.\d+)?)\s*(?:°\s*c|c)?)?", re.I)


def _condition(clause: str, grades: list[str], aliases: dict[str, str] | None = None) -> ParcelUpdate | None:
    """A change to cargo temperature or density, or None if the clause is about something else."""
    for rx, kind in ((DENSITY, "density"), (TEMP, "temp")):
        m = rx.fullmatch(clause)
        if not m:
            continue
        name = (m.group("grade") or "").strip()
        name = re.sub(r"^(?:the|set|make|use|assume|with)\b\s*|\s*\b(?:cargo|is|has)$", "", name, flags=re.I).strip()
        grade = None
        if name and name.lower() not in ("cargo", "all", "the", "the cargo", "all cargo"):
            grade = _grade(name, grades, aliases)
            if grade is None:
                return None
        if kind == "temp":
            t = float(m.group("t"))
            return ParcelUpdate(grade=grade, temp_c=t) if -20 <= t <= 90 else None
        d, ref = float(m.group("d")), m.group("t")
        d = d / 1000 if d > 2 else d
        if not 0.5 <= d <= 1.2:
            return None
        return ParcelUpdate(grade=grade, density_t_m3=d, density_temp_c=float(ref) if ref else None)
    return None


def try_instruction(text: str, cargo_tanks: list[str], grades: list[str], current: dict,
                    aliases: dict[str, str] | None = None) -> InstructionOut | None:
    c = ConstraintsIn.model_validate(current or {})
    empty, fixed, trim, fill = list(c.empty_tanks), dict(c.fixed_grades), c.max_trim_m, c.fill
    notes: list[str] = []
    memory: list[MemoryOp] = []
    updates: list[ParcelUpdate] = []

    standing = bool(STANDING.search(text))
    vessel_scope = bool(THIS_SHIP.search(text))
    body = THIS_SHIP.sub(" ", STANDING.sub(" ", text)).strip(" .!")
    if not body:
        return None

    if FORGET.match(body):
        rest = FORGET.sub(" ", body)
        if re.search(r"\b(fill|filling|spread|spreading|even|evenly)\b", rest, re.I) and not TANK_REF.search(rest):
            memory.append(MemoryOp(op="forget", scope="vessel" if vessel_scope else "global", kind="fill_mode",
                                   text="Forget the fill rule"))
            return InstructionOut(constraints=ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim, fill=fill),
                                  notes=notes, memory=memory)
        if re.search(r"\btrim\b", rest, re.I):
            memory.append(MemoryOp(op="forget", scope="vessel" if vessel_scope else "global", kind="max_trim",
                                   text="Forget the trim limit"))
            trim = None
            rest = re.sub(r"\b(the\s+)?trim( limit| rule)?\b", " ", rest, flags=re.I)
            if not rest.strip(" .,"):
                return InstructionOut(constraints=ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim, fill=fill),
                                      notes=notes, memory=memory)
        parsed = _tanks(rest, cargo_tanks)
        if parsed is None or not parsed[0]:
            return None
        for tid in parsed[0]:
            for kind in ("constraint_empty", "constraint_fixed"):
                memory.append(MemoryOp(op="forget", scope="vessel", kind=kind, key=tid, text=f"Forget the rule for {tid}"))
        empty = [t for t in empty if t not in parsed[0]]
        fixed = {t: g for t, g in fixed.items() if t not in parsed[0]}
        return InstructionOut(constraints=ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim, fill=fill),
                              notes=notes, memory=memory)

    for clause in re.split(r"\s*(?:;|,\s*and\s+|\band\s+(?=(?:keep|leave|use|put|load|trim|max|cargo|temp|temperature|density)\b)|\.\s+)\s*", body):
        clause = clause.strip(" .")
        if not clause:
            continue
        if u := _condition(clause, grades, aliases):
            updates.append(u)
        elif f := fill_rule(clause):
            fill = f
            if standing:
                memory.append(MemoryOp(op="set", scope="vessel" if vessel_scope else "global", kind="fill_mode", value=f,
                                       text="Spread cargo evenly over all tanks" if f == "even" else "Fill tanks to 98%"))
        elif m := re.fullmatch(r"(?:please\s+)?(?:keep|leave)\s+(.+?)\s+empty", clause, re.I):
            parsed = _tanks(m.group(1), cargo_tanks)
            if parsed is None:
                return None
            for tid in parsed[0]:
                if tid not in empty:
                    empty.append(tid)
                fixed.pop(tid, None)
                if standing:
                    memory.append(MemoryOp(op="set", scope="vessel", kind="constraint_empty", key=tid, text=f"Keep {tid} empty"))
            notes += [f"There is no tank {t} on this ship." for t in parsed[1]]
        elif m := re.fullmatch(r"(?:you can\s+)?(?:use|release|fill)\s+(.+?)\s+again", clause, re.I):
            parsed = _tanks(m.group(1), cargo_tanks)
            if parsed is None:
                return None
            empty = [t for t in empty if t not in parsed[0]]
            notes += [f"There is no tank {t} on this ship." for t in parsed[1]]
        elif m := re.fullmatch(r"(?:keep\s+)?(?:the\s+)?(?:max(?:imum)?\s+)?trim\s+(?:must\s+)?(?:be\s+)?(?:under|below|less than|"
                               r"max(?:imum)?|at most|no more than|not more than|not exceed|must not exceed|within|<=?)\s+"
                               r"(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)?", clause, re.I):
            trim = float(m.group(1))
            if standing:
                memory.append(MemoryOp(op="set", scope="vessel" if vessel_scope else "global", kind="max_trim",
                                       value=trim, text=f"Trim at most {trim:g} m"))
        elif m := re.fullmatch(r"(?:put|load|carry|keep)?\s*(?:the\s+)?(.+?)\s+in\s+(.+)", clause, re.I):
            grade = _grade(m.group(1), grades, aliases)
            parsed = _tanks(m.group(2), cargo_tanks)
            if grade is None or parsed is None:
                return None
            for tid in parsed[0]:
                fixed[tid] = grade
                empty = [t for t in empty if t != tid]
                if standing:
                    memory.append(MemoryOp(op="set", scope="vessel", kind="constraint_fixed", key=tid, value=grade,
                                           text=f"{tid} carries {grade}"))
            notes += [f"There is no tank {t} on this ship." for t in parsed[1]]
        elif m := re.fullmatch(r"(.+?)\s+(?:means|=|is the same as)\s+(.+)", clause, re.I):
            target = match_grade(m.group(2), aliases)
            if target is None:
                return None
            alias = m.group(1).strip().strip("'\"")
            memory.append(MemoryOp(op="set", scope="global", kind="grade_alias", key=alias.lower(), value=target.id,
                                   text=f"{alias} means {target.name}"))
        else:
            return None
    return InstructionOut(constraints=ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim, fill=fill),
                          notes=notes, memory=memory, parcel_updates=updates)

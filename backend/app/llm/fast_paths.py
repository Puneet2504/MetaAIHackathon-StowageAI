"""Common planner instructions handled by code, so they cost no tokens.

try_instruction returns None unless it understands the WHOLE instruction; anything else goes to the model.
Handles: keep/leave tank(s) empty, use tank(s) again, trim limits, "<grade> in tank(s)",
"X means <grade>", and "always / from now on / remember / forget" for memory.
"""
import re

from ..schemas import ConstraintsIn, InstructionOut, MemoryOp
from ..stowage.grades import match_grade

STANDING = re.compile(r"\b(always|from now on|by default|every time|remember( that)?|permanently)\b", re.I)
THIS_SHIP = re.compile(r"\b(on|for|with)?\s*this (ship|vessel)\b", re.I)
FORGET = re.compile(r"^(forget|stop|no longer|remove the rule|drop the rule)\b", re.I)
TANK_REF = re.compile(r"(?:cot)?(\d{1,2})\s*(p|s|port|stbd|starboard)?(?:\s*/\s*(p|s))?\b", re.I)
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


def _grade(name: str, grades: list[str]) -> str | None:
    """The order's grade name the planner meant, or None."""
    want = match_grade(name)
    for g in grades:
        if g.lower() == name.strip().lower() or (want and (mg := match_grade(g)) and mg.id == want.id):
            return g
    return None


def try_instruction(text: str, cargo_tanks: list[str], grades: list[str], current: dict) -> InstructionOut | None:
    c = ConstraintsIn.model_validate(current or {})
    empty, fixed, trim = list(c.empty_tanks), dict(c.fixed_grades), c.max_trim_m
    notes: list[str] = []
    memory: list[MemoryOp] = []

    standing = bool(STANDING.search(text))
    vessel_scope = bool(THIS_SHIP.search(text))
    body = THIS_SHIP.sub(" ", STANDING.sub(" ", text)).strip(" .!")
    if not body:
        return None

    if FORGET.match(body):
        rest = FORGET.sub(" ", body)
        if re.search(r"\btrim\b", rest, re.I):
            memory.append(MemoryOp(op="forget", scope="vessel" if vessel_scope else "global", kind="max_trim",
                                   text="Forget the trim limit"))
            trim = None
            rest = re.sub(r"\b(the\s+)?trim( limit| rule)?\b", " ", rest, flags=re.I)
            if not rest.strip(" .,"):
                return InstructionOut(constraints=ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim),
                                      notes=notes, memory=memory)
        parsed = _tanks(rest, cargo_tanks)
        if parsed is None or not parsed[0]:
            return None
        for tid in parsed[0]:
            for kind in ("constraint_empty", "constraint_fixed"):
                memory.append(MemoryOp(op="forget", scope="vessel", kind=kind, key=tid, text=f"Forget the rule for {tid}"))
        empty = [t for t in empty if t not in parsed[0]]
        fixed = {t: g for t, g in fixed.items() if t not in parsed[0]}
        return InstructionOut(constraints=ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim),
                              notes=notes, memory=memory)

    for clause in re.split(r"\s*(?:;|,\s*and\s+|\band\s+(?=(?:keep|leave|use|put|load|trim|max)\b)|\.\s+)\s*", body):
        clause = clause.strip(" .")
        if not clause:
            continue
        if m := re.fullmatch(r"(?:please\s+)?(?:keep|leave)\s+(.+?)\s+empty", clause, re.I):
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
            grade = _grade(m.group(1), grades)
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
            target = match_grade(m.group(2))
            if target is None:
                return None
            alias = m.group(1).strip().strip("'\"")
            memory.append(MemoryOp(op="set", scope="global", kind="grade_alias", key=alias.lower(), value=target.id,
                                   text=f"{alias} means {target.name}"))
        else:
            return None
    return InstructionOut(constraints=ConstraintsIn(empty_tanks=empty, fixed_grades=fixed, max_trim_m=trim),
                          notes=notes, memory=memory)

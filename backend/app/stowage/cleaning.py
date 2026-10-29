"""Tank cleaning on a change of grade: what a tank needs before it takes a different product. No AI here.

The level for each previous -> next grade comes from this fixed table, so a language model never
decides whether a tank is fit to load; the AI only writes these rules up as a port checklist.
Levels follow common product-tanker practice for clean products and are demo values: the cargo's
own cleaning guide, the charterer's instructions and the surveyor always decide.
"""
from __future__ import annotations

# Typical time and cost per tank for each level (demo estimates; real figures depend on port and contractor).
HOURS_PER_TANK = {0: 0, 1: 1, 2: 4, 3: 8}
USD_PER_TANK = {0: 0, 1: 500, 2: 3000, 3: 6000}
TANKS_AT_ONCE = 2          # tanks washed at the same time with the ship's cleaning machines
INSPECTION_HOURS = 2       # wall-wash tests and surveyor inspection, once per port with a full wash

LEVELS = {
    0: ("none", "No cleaning", []),
    1: ("drain", "Drain and strip", [
        "Drain and strip the tank, its pump and lines until empty",
        "Check the tank is dry and free of residue before loading",
    ]),
    2: ("wash", "Water wash", [
        "Water wash the tank (cold sea water, tank-cleaning machines)",
        "Strip, mop out and dry the tank",
        "Flush the cargo line and pump with the new grade or water, then drain",
    ]),
    3: ("full_wash", "Full wash", [
        "Hot water wash, then rinse with fresh water to remove salt",
        "Strip, mop out, ventilate and dry the tank",
        "Flush and drain the cargo line, pump and drop line",
        "Wall-wash test and surveyor's tank inspection before loading",
    ]),
}

# Previous grade -> next grade: (level, reason). Same grade is always 0.
RULES: dict[tuple[str, str], tuple[int, str]] = {
    ("JET", "ULSD"): (1, "a lighter, cleaner product before diesel"),
    ("JET", "GASOLINE"): (1, "jet residue is acceptable in gasoline"),
    ("JET", "NAPHTHA"): (1, "jet residue is acceptable in naphtha"),
    ("GASOLINE", "NAPHTHA"): (1, "similar light products"),
    ("NAPHTHA", "GASOLINE"): (1, "similar light products"),
    ("GASOLINE", "ULSD"): (2, "gasoline residue would lower the diesel's flash point"),
    ("NAPHTHA", "ULSD"): (2, "naphtha residue would lower the diesel's flash point"),
    ("ULSD", "GASOLINE"): (2, "diesel residue raises the gasoline's final boiling point and colour"),
    ("ULSD", "NAPHTHA"): (2, "diesel residue spoils naphtha"),
    ("ULSD", "JET"): (3, "diesel may contain FAME (biodiesel), which is not allowed in jet fuel"),
    ("GASOLINE", "JET"): (3, "gasoline residue would lower the jet fuel's flash point"),
    ("NAPHTHA", "JET"): (3, "naphtha residue would lower the jet fuel's flash point"),
}
LIBRARY = {"ULSD", "JET", "GASOLINE", "NAPHTHA"}


def _checks(prev: str, nxt: str, level: int) -> list[str]:
    checks = ["Visual check that the tank, pump and lines are drained, dry and free of the last cargo"]
    if nxt == "JET" and level:
        checks += ["Wall-wash test of the tank before loading jet",
                   "FAME (biodiesel) content of the first-foot sample below the jet limit",
                   "No free water in the tank or lines"]
    if nxt == "GASOLINE" and prev == "ULSD":
        checks.append("Final boiling point and colour of the first-foot sample")
    if nxt == "ULSD" and prev in ("GASOLINE", "NAPHTHA"):
        checks.append("Flash point of the first-foot sample")
    if level >= 2:
        checks += ["Room in the slop tanks for the washings",
                   "Gas-free and oxygen checks before anyone enters the tank"]
    if prev not in LIBRARY or nxt not in LIBRARY:
        checks.append("Check the cargo's own cleaning guide: this grade is not in the built-in rules")
    return checks


def requirement(prev: str | None, nxt: str) -> dict:
    """What a tank that last carried `prev` needs before loading `nxt` (grade ids; None = empty or clean)."""
    if not prev or prev == nxt:
        level, reason = 0, "same grade" if prev else "tank was empty or clean"
    elif (prev, nxt) in RULES:
        level, reason = RULES[(prev, nxt)]
    else:
        level, reason = 2, "grade not in the built-in rules, so a water wash is assumed"
    key, label, steps = LEVELS[level]
    return {"level": level, "key": key, "label": label, "reason": reason, "steps": steps,
            "checks": _checks(prev or "", nxt, level) if level else []}


def cost(prev: str | None, nxt: str) -> int:
    return requirement(prev, nxt)["level"]


def port_checks(changes: list[dict]) -> list[str]:
    """General checks at a port where tanks change grade."""
    checks = [
        "Last three cargoes of every tank to be loaded, from the cargo documents",
        "Which cargo lines and pumps the new grade uses; flush and drain any that carried another grade",
        "Pressure/vacuum valves and the inert gas oxygen level before loading",
        "Shore and ship samples, and first-foot samples checked before full-rate loading",
    ]
    if any(c["level"] >= 2 for c in changes):
        checks.append("Time and cost of tank cleaning before the loading slot, and where the washings will be discharged")
    if any(c["level"] >= 3 for c in changes):
        checks.append("Surveyor booked to inspect the washed tanks before loading")
    return checks


def estimate(changes: list[dict]) -> dict:
    """Time at the port and cost for the tanks in `changes` (rows from requirement() with a tank id)."""
    levels = [c["level"] for c in changes]
    hours = sum(HOURS_PER_TANK[l] for l in levels) / TANKS_AT_ONCE + (INSPECTION_HOURS if 3 in levels else 0)
    return {"hours": round(hours, 1), "cost_usd": sum(USD_PER_TANK[l] for l in levels),
            "tanks": sum(1 for l in levels if l > 0)}

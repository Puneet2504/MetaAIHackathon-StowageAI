"""Server copy of the demo grade library (ids and densities match frontend/src/grades.ts).
Densities are typical example values; real plans must use the density from the cargo documents."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Grade:
    id: str
    name: str
    density: float  # t/m3 at 15 C, demo value
    known: bool = True


GRADES = [
    Grade("ULSD", "Diesel (ULSD)", 0.845),
    Grade("JET", "Jet A-1", 0.80),
    Grade("GASOLINE", "Gasoline", 0.745),
    Grade("NAPHTHA", "Naphtha", 0.70),
]

ALIASES = {
    "ULSD": ["ulsd", "diesel", "gasoil", "gas oil", "go 10ppm", "ago", "en590"],
    "JET": ["jet", "jet a-1", "jet a1", "jeta1", "kerosene", "avtur", "atk"],
    "GASOLINE": ["gasoline", "petrol", "mogas", "eurobob", "rbob", "unleaded"],
    "NAPHTHA": ["naphtha", "nap"],
}

TYPICAL_DENSITY = 0.80


def match_grade(name: str, aliases: dict[str, str] | None = None) -> Grade | None:
    """Library grade for a name. `aliases` (lower-case alias -> grade id) come from the planner's memory."""
    key = name.strip().lower()
    if aliases and key in aliases:
        hit = next((g for g in GRADES if g.id == aliases[key]), None)
        if hit:
            return hit
    for g in GRADES:
        if key == g.id.lower() or key == g.name.lower():
            return g
    for gid, words in ALIASES.items():
        if any(re.search(rf"\b{re.escape(w)}\b", key) for w in words):
            return next(g for g in GRADES if g.id == gid)
    return None


def grade_for(name: str, density: float | None, aliases: dict[str, str] | None = None) -> tuple[Grade, str | None]:
    """The library grade, or an ad-hoc one for unknown names. Returns a warning when a density was assumed."""
    g = match_grade(name, aliases)
    if g:
        return (Grade(g.id, g.name, density) if density else g), None
    slug = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:20] or "GRADE"
    if density:
        return Grade(slug, name, density, known=False), None
    return (Grade(slug, name, TYPICAL_DENSITY, known=False),
            f"No density for '{name}'; assumed {TYPICAL_DENSITY} t/m3. Use the density from the cargo documents.")

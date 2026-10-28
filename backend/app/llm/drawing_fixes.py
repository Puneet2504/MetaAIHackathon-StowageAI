"""Repairs and flags for a layout read from a drawing. Deterministic rules, no model calls.

Real drawings and scans go wrong in a few predictable ways:
  - only LOA is printed, so LBP is missing            -> estimate LBP from LOA, flagged
  - extents, breadth, depth or draught only drawn      -> scale from the model's measured fractions, flagged
  - twin tanks disagree (7050 vs 7250)                 -> use the value the sister tanks share, flagged
  - extents reversed or past the ends of the ship      -> swap / clamp, flagged
  - OCR-misread capacities (7250 for 2750)             -> volume sanity check, likely fix, flagged
  - duplicate labels (two COT4P, two slop pairs)       -> renumber by position, flagged
Shipyard numbering is kept as printed, even when it starts aft. Nothing missing is invented:
estimates are limited to LBP, dimensions and scaled positions, always flagged. Every change is returned
as a fix the user can undo, and the model's own notes about things fixed here are dropped as stale.
"""
import itertools
import re

from .. import config

TYPE_ORDER = {"cargo": 0, "slop": 1, "ballast": 2, "fuel_oil": 3, "diesel_oil": 4, "fresh_water": 5}
OCR_CONFUSIONS = {"1": "7", "7": "1", "2": "7", "3": "8", "8": "3", "5": "6", "6": "5", "0": "8"}
EXTRA_CONFUSIONS = {"7": "2", "8": "6", "5": "3", "3": "5"}
DEFAULT_HULL = 2.0  # double bottom and double side, as in vessel_builder.Hull
# Typical product-tanker proportions, used only when a dimension is neither printed nor measurable.
L_OVER_B, B_OVER_D, T_OVER_D = 5.4, 1.75, 0.68
MEASURED = ("lengthRef", "breadthFrac", "depthFrac", "draughtMarks")
CARGO_ID = re.compile(r"^([A-Z]+?)(\d+)([PS])$")


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _fmt(v: float) -> str:
    return f"{v:,.1f}".rstrip("0").rstrip(".")


class _Log:
    def __init__(self):
        self.notes: list[str] = []
        self.fixes: list[dict] = []
        self.estimated: list[str] = []

    def fix(self, tank: str | None, field: str, old, new, reason: str, obj: dict | None = None):
        # obj: the tank dict, so the fix follows the tank if a later step renames it
        self.fixes.append({"tank": tank, "field": field, "old": old, "new": new, "reason": reason, "_obj": obj})


# ---------- a. LBP from LOA ----------

def _lbp(L: dict, log: _Log) -> None:
    for k in ("loa", "lbp", "breadth", "depth", "summerDraught", "doubleBottom", "doubleSide"):
        if k in L:
            L[k] = _num(L[k])
    if L.get("lbp") is None and L.get("loa"):
        est = round(L["loa"] * config.LBP_FROM_LOA, 1)
        L["lbp"] = est
        log.estimated.append("lbp")
        log.fix(None, "lbp", None, est, f"only LOA given; LBP estimated as {config.LBP_FROM_LOA:.0%} of LOA")
        log.notes.append(f"LBP estimated as {config.LBP_FROM_LOA:.0%} of LOA {_fmt(L['loa'])} m = {_fmt(est)} m. "
                         "Check it against the drawing.")
    elif L.get("lbp") is None and any(t.get("xFwd") is not None for t in L.get("tanks") or []):
        log.notes.append("No LBP or LOA found. Enter the LBP so the tank positions can be used.")


# ---------- a2. dimensions and positions measured on the drawing ----------

def _ref_length(L: dict) -> float | None:
    if L.get("lengthRef") == "LBP" or not L.get("loa"):
        return L.get("lbp")
    return L.get("loa")


def _half(v: float) -> float:
    return round(v * 2) / 2


def _dimensions(L: dict, log: _Log) -> None:
    ref, lbp = _ref_length(L), L.get("lbp")
    for key, frac_key, typical in (("breadth", "breadthFrac", lambda: lbp / L_OVER_B if lbp else None),
                                   ("depth", "depthFrac", lambda: L["breadth"] / B_OVER_D if L.get("breadth") else None)):
        if L.get(key) is not None:
            continue
        frac = _num(L.get(frac_key))
        if frac and ref and 0 < frac < 1:
            val, how = frac * ref, "scaled from the drawing"
        elif typical() is not None:
            val, how = typical(), "typical product-tanker proportions"
        else:
            continue
        L[key] = round(val, 1)
        log.estimated.append(key)
        log.fix(None, key, None, L[key], f"not printed; {how}")
    if L.get("summerDraught") is None and L.get("depth"):
        marks = [m for m in (_num(x) for x in (L.get("draughtMarks") or [])) if m]
        if marks and max(marks) < L["depth"]:
            val, how = max(marks), "deepest draught mark on the profile"
        else:
            val, how = L["depth"] * T_OVER_D, "typical product-tanker proportions"
        L["summerDraught"] = round(val, 1)
        log.estimated.append("summerDraught")
        log.fix(None, "summerDraught", None, L["summerDraught"], f"not printed; {how}")
    if any(k in log.estimated for k in ("breadth", "depth", "summerDraught")):
        log.notes.append("Some main dimensions were not printed and have been estimated (outlined in amber). "
                         "Check them against the drawing: tank volumes and stability depend on them.")


def _positions(L: dict, log: _Log) -> None:
    ref, lbp, loa = _ref_length(L), L.get("lbp"), L.get("loa")
    if not (ref and lbp):
        return
    # Fractions are along the drawn length line; the AP sits roughly half the overhang in from the aft end.
    offset = (loa - lbp) / 2 if ref == loa and loa else 0.0
    scaled = 0
    for t in L["tanks"]:
        a, f = _num(t.pop("aFrac", None)), _num(t.pop("fFrac", None))
        if t.get("xAft") is not None or t.get("xFwd") is not None or a is None or f is None                 or not (0 <= a <= 1 and 0 <= f <= 1):
            continue  # printed metres always win
        xa = min(max(_half(min(a, f) * ref - offset), 0.0), lbp)
        xf = min(max(_half(max(a, f) * ref - offset), 0.0), lbp)
        if xf <= xa:
            continue
        t["xAft"], t["xFwd"] = xa, xf
        t["_est"] = ["xAft", "xFwd"]
        log.fix(t.get("id"), "position", [None, None], [xa, xf], "not dimensioned; scaled from the drawing (±1–2 m)", t)
        scaled += 1
    if scaled:
        log.notes.append(f"{scaled} tank positions were scaled from the drawing (outlined in amber). "
                         "They are usually within 1–2 m; check the ends against the drawing.")


# ---------- b. extents and sides ----------

def _extents(L: dict, log: _Log) -> None:
    lbp, loa = L.get("lbp"), L.get("loa")
    for t in L["tanks"]:
        tid = t.get("id")
        a, f = _num(t.get("xAft")), _num(t.get("xFwd"))
        if a is not None and f is not None and a > f:
            log.fix(tid, "xAft/xFwd", [a, f], [f, a], "aft and forward ends were reversed", t)
            a, f = f, a
        if a is not None and a < 0:
            log.fix(tid, "xAft", a, 0.0, "before the aft perpendicular; set to 0", t)
            a = 0.0
        if lbp and f is not None and f > lbp:
            if f <= (loa or lbp * 1.08):
                log.fix(tid, "xFwd", f, lbp, "past the forward perpendicular (measured along LOA?); set to LBP", t)
                f = lbp
            else:
                log.notes.append(f"{tid} ends at {_fmt(f)} m, beyond the LBP of {_fmt(lbp)} m. Check its position.")
        t["xAft"], t["xFwd"] = a, f
        cap = _num(t.get("capacity100"))
        t["capacity100"] = cap
        tid_u = str(tid or "").upper()
        if t.get("side") in ("P", "S", "C") and tid_u[-1:] in ("P", "S") and t.get("type") != "ballast" \
                and tid_u[-1] != t["side"] and CARGO_ID.match(tid_u):
            log.fix(tid, "side", t["side"], tid_u[-1], "side taken from the tank id", t)
            t["side"] = tid_u[-1]


# ---------- d. duplicate labels ----------

def _unique_name(base_id: str, taken: set[str]) -> str:
    m = re.match(r"^(.*?)(\d*)([PSC]?)$", base_id)
    head, digits, side = m.group(1), m.group(2), m.group(3)
    for n in itertools.count(2):
        cand = f"{head}{n}{side}" if not digits else f"{base_id}-{n}"
        if cand not in taken:
            return cand
    raise AssertionError("unreachable")


def _plain(t: dict) -> dict:
    return {k: v for k, v in t.items() if not k.startswith("_")}


def _merge_same(L: dict, log: _Log) -> None:
    """The same tank listed twice (e.g. once from a deck label, once from the cell text): keep one."""
    keep: dict[tuple, dict] = {}
    out = []
    for t in L["tanks"]:
        t["id"] = str(t.get("id") or "").upper()
        if t.get("xAft") is None or t.get("xFwd") is None:
            out.append(t)
            continue
        k = (t.get("type"), t.get("side"), t["xAft"], t["xFwd"])
        first = keep.get(k)
        if first is None:
            keep[k] = t
            out.append(t)
            continue
        drop = t if first.get("capacity100") or not t.get("capacity100") else first
        stay = first if drop is t else t
        if drop is first:
            out[out.index(first)] = t
            keep[k] = t
        log.fix(drop["id"], "removed", _plain(drop), None,
                f"same tank as {stay['id']} at {_fmt(stay['xAft'])}–{_fmt(stay['xFwd'])} m, listed twice "
                "(e.g. a deck label and the cell text)")
    L["tanks"] = out


def _set_aside(L: dict, log: _Log) -> None:
    """Ballast tanks with no position can't be saved; when the rest of the drawing has positions, set them aside."""
    tanks = L["tanks"]
    placed = sum(1 for t in tanks if t.get("xAft") is not None and t.get("xFwd") is not None)
    if placed < len(tanks) / 2:
        return  # positions are missing generally: leave them for the user to fill in
    out = []
    for t in tanks:
        if t.get("type") == "ballast" and (t.get("xAft") is None or t.get("xFwd") is None):
            log.fix(t["id"], "removed", _plain(t), None, "no position could be read; set aside (Undo to put it back)")
        else:
            out.append(t)
    L["tanks"] = out


WBT_ID = re.compile(r"^WBT\d+[PS]$")


def _ballast_names(L: dict, log: _Log) -> None:
    """Garbled or repeated wing-tank labels: name each one after the cargo tank it sits beside."""
    ballast = [t for t in L["tanks"] if t.get("type") == "ballast" and t.get("side") in ("P", "S")]
    ids = [t["id"] for t in ballast]
    if not ballast or all(WBT_ID.match(i) for i in ids) and len(ids) == len(set(ids)):
        return
    beside = {(t["xAft"], t["xFwd"]): int(CARGO_ID.match(t["id"]).group(2)) for t in L["tanks"]
              if t.get("type") == "cargo" and CARGO_ID.match(t["id"]) and t.get("xAft") is not None}
    renamed = 0
    for t in ballast:
        n = beside.get((t.get("xAft"), t.get("xFwd")))
        if n is None:
            continue
        new = f"WBT{n}{t['side']}"
        if new != t["id"]:
            log.fix(t["id"], "id", t["id"], new, f"wing tank renamed after the cargo tank beside it (COT{n})")
            t["id"] = new
            renamed += 1
    if renamed:
        log.notes.append(f"{renamed} wing ballast tanks had unclear labels and were named after the cargo tank "
                         "beside each one.")


def _duplicates(L: dict, log: _Log) -> None:
    tanks = L["tanks"]
    cargo = [t for t in tanks if t.get("type") == "cargo" and CARGO_ID.match(t["id"])]
    cargo_dupes = {i for i in (t["id"] for t in cargo) if sum(1 for t in cargo if t["id"] == i) > 1}

    if cargo_dupes and all(t.get("xAft") is not None for t in cargo):
        # Numbering direction from the tanks whose labels are unique: does the number fall going forward?
        pts = [(int(CARGO_ID.match(t["id"]).group(2)), t["xAft"]) for t in cargo if t["id"] not in cargo_dupes]
        forward_first = True
        if len({n for n, _ in pts}) >= 2:
            pts.sort(key=lambda p: p[1])
            forward_first = pts[0][0] > pts[-1][0]  # higher numbers aft = number 1 forward
        for side in ("P", "S"):
            series = [t for t in cargo if t["side"] == side]
            if not series:
                continue
            prefix = CARGO_ID.match(series[0]["id"]).group(1)
            start = min(int(CARGO_ID.match(t["id"]).group(2)) for t in series)
            series.sort(key=lambda t: t["xAft"], reverse=forward_first)
            for n, t in enumerate(series, start=start):
                new = f"{prefix}{n}{side}"
                if new != t["id"]:
                    log.fix(t["id"], "id", t["id"], new, "duplicate label; cargo tanks renumbered by position "
                            f"({'1 forward' if forward_first else 'numbered from aft'})")
                    t["id"] = new
        log.notes.append(f"Duplicate tank labels ({', '.join(sorted(cargo_dupes))}) were renumbered by position. "
                         "Check the ids against the drawing.")

    _ballast_names(L, log)
    taken: set[str] = set()
    # Anything still repeated (slop tanks, centre tanks, missing positions): the forward-most keeps the name.
    for t in sorted(tanks, key=lambda t: -(t["xAft"]) if t.get("xAft") is not None else 1e9):
        if t["id"] in taken:
            new = _unique_name(t["id"], taken | {x["id"] for x in tanks})
            log.fix(t["id"], "id", t["id"], new, "repeated label; numbered to keep ids unique")
            t["id"] = new
        taken.add(t["id"])

    for a, b in itertools.combinations([t for t in tanks if t.get("type") in ("cargo", "slop")], 2):
        if (a["side"] == b["side"] and a["type"] == b["type"] and None not in (a["xAft"], a["xFwd"], b["xAft"], b["xFwd"])
                and a["xAft"] < b["xFwd"] - 1e-6 and b["xAft"] < a["xFwd"] - 1e-6):
            log.notes.append(f"{a['id']} and {b['id']} overlap lengthwise. Check their positions.")


# ---------- e. twin tanks that disagree ----------

def _digits_apart(a: float, b: float) -> int:
    x, y = _fmt(a).replace(",", ""), _fmt(b).replace(",", "")
    if len(x) != len(y):
        return 99
    return sum(1 for i, j in zip(x, y) if i != j)


def _twins(L: dict, log: _Log) -> None:
    cargo = [t for t in L["tanks"] if t.get("type") == "cargo" and t.get("xAft") is not None
             and t.get("xFwd") is not None and t.get("capacity100")]
    pairs: dict[tuple, list[dict]] = {}
    for t in cargo:
        pairs.setdefault((t["xAft"], t["xFwd"]), []).append(t)
    for (a, f), group in pairs.items():
        ps = [t for t in group if t.get("side") in ("P", "S")]
        if len(ps) != 2 or ps[0]["capacity100"] == ps[1]["capacity100"]:
            continue
        length = f - a
        sisters = [t["capacity100"] for t in cargo if abs((t["xFwd"] - t["xAft"]) - length) <= 0.1 * length]
        common = max(set(sisters), key=sisters.count)
        odd = [t for t in ps if t["capacity100"] != common]
        if sisters.count(common) >= 3 and len(odd) == 1 and _digits_apart(odd[0]["capacity100"], common) <= 2:
            t = odd[0]
            twin = next(x for x in ps if x is not t)
            log.fix(t["id"], "capacity100", t["capacity100"], common,
                    f"read as {_fmt(t['capacity100'])} m³; its twin {twin['id']} and {sisters.count(common) - 1} other "
                    f"tanks of the same size read {_fmt(common)} m³", t)
            t["capacity100"] = common
        else:
            log.notes.append(f"{ps[0]['id']} and {ps[1]['id']} are the same size but read "
                             f"{_fmt(ps[0]['capacity100'])} and {_fmt(ps[1]['capacity100'])} m³. Check which is right.")


# ---------- c. capacity sanity check ----------

def _candidates(value: float) -> set[float]:
    text = _fmt(value).replace(",", "")
    digits = [i for i, ch in enumerate(text) if ch.isdigit()]
    out = {value * 10, value / 10}
    for i, j in zip(digits, digits[1:]):  # swap neighbouring digits
        s = list(text)
        s[i], s[j] = s[j], s[i]
        out.add(float("".join(s)))
    for i in digits:  # one misread digit
        for table in (OCR_CONFUSIONS, EXTRA_CONFUSIONS):
            if text[i] in table:
                out.add(float(text[:i] + table[text[i]] + text[i + 1:]))
    return {c for c in out if c > 0 and c != value}


def _capacities(L: dict, log: _Log) -> None:
    B, D = L.get("breadth"), L.get("depth")
    if not (B and D):
        return
    # With estimated dimensions the tank's space is only approximate: flag, never "correct".
    may_correct = not any(k in log.estimated for k in ("breadth", "depth"))
    flagged: list[tuple[str, float, float]] = []
    db = L.get("doubleBottom") or DEFAULT_HULL
    ds = L.get("doubleSide") or DEFAULT_HULL
    by_extent: dict[tuple, list[dict]] = {}
    for t in L["tanks"]:
        by_extent.setdefault((t.get("type"), t.get("xAft"), t.get("xFwd")), []).append(t)
    for t in L["tanks"]:
        cap, a, f = t.get("capacity100"), t.get("xAft"), t.get("xFwd")
        if cap is None or a is None or f is None or t.get("type") not in ("cargo", "slop"):
            continue
        width = (B / 2 - ds) * (2 if t.get("side") == "C" else 1)
        box = (f - a) * width * (D - db)
        if box <= 0 or 0.5 * box <= cap <= box:
            continue
        partner = [p["capacity100"] for p in by_extent[(t["type"], a, f)] if p is not t and p.get("capacity100")
                   and 0.5 * box <= p["capacity100"] <= box]
        if not may_correct:
            flagged.append((t["id"], cap, box))
            continue
        cands = _candidates(cap)
        good = [c for c in cands if 0.85 * box <= c <= 0.99 * box]
        # A twin tank at the same position (e.g. the other side) with a sensible value is the best evidence.
        pick = next((c for c in cands if partner and abs(c - partner[0]) < 0.5), None)
        if pick is None and good:
            pick = min(good, key=lambda c: abs(c - 0.95 * box))
        problem = "more than the tank can hold" if cap > box else "far less than the tank's size"
        if pick is not None:
            log.fix(t["id"], "capacity100", cap, pick,
                    f"read as {_fmt(cap)} m³, {problem} (about {_fmt(round(box))} m³); likely a misread digit", t)
            t["capacity100"] = pick
        else:
            flagged.append((t["id"], cap, box))
    if len(flagged) > 2:  # one summary instead of a note per tank
        tid, cap, box = flagged[0]
        log.notes.append(f"{len(flagged)} tank capacities don't fit their space at these dimensions "
                         f"(e.g. {tid} {_fmt(cap)} m³ vs about {_fmt(round(box))} m³). Check the breadth, depth "
                         "and capacities against the drawing.")
    else:
        for tid, cap, box in flagged:
            problem = "more than the tank can hold" if cap > box else "far less than the tank's size"
            log.notes.append(f"{tid} capacity {_fmt(cap)} m³ is {problem} (about {_fmt(round(box))} m³). "
                             "Check it against the drawing.")


# ---------- g. the model's own notes that a fix has made stale ----------

STALE = {
    "lbp": ("lbp", "loa"), "breadth": ("breadth", "beam"), "depth": ("depth",),
    "summerDraught": ("draught", "draft"), "position": ("xaft", "xfwd", "extent", "scale", "position", "dimensioned"),
    "id": ("renumber", "duplicate", "repeat", "unique"),
}


def filter_ai_notes(notes: list[str], fixes: list[dict]) -> list[str]:
    """Drop the model's notes about things the rules above have since filled in or fixed."""
    words = {w for f in fixes for w in STALE.get(f["field"], ())}
    capacity_tanks = {str(f["tank"]).lower() for f in fixes if f["field"] == "capacity100"}
    out = []
    for n in notes:
        low = n.lower()
        if any(w in low for w in words):
            continue
        if capacity_tanks and any(t in low for t in capacity_tanks) and ("differ" in low or "m3" in low):
            continue
        out.append(n)
    return out


def normalise(layout: dict) -> tuple[dict, list[str], list[dict], list[str]]:
    """Return (layout, checks, fixes, estimated). The input dict is not changed."""
    L = {**layout, "tanks": [dict(t) for t in (layout.get("tanks") or []) if isinstance(t, dict)]}
    log = _Log()
    _lbp(L, log)
    _dimensions(L, log)
    _positions(L, log)
    _extents(L, log)
    _merge_same(L, log)
    _set_aside(L, log)
    _duplicates(L, log)
    _twins(L, log)
    _capacities(L, log)
    # Fixes follow their tank through renames; estimated tank fields are reported under the final id.
    for f in log.fixes:
        obj = f.pop("_obj", None)
        if obj is not None and f["field"] != "id":
            f["tank"] = obj["id"]
    for t in L["tanks"]:
        log.estimated += [f"{t['id']}.{k}" for k in t.pop("_est", [])]
        t.pop("aFrac", None), t.pop("fFrac", None)
    for k in MEASURED:
        L.pop(k, None)
    # Forward to aft within each type, port before starboard, so pairs sit together.
    L["tanks"].sort(key=lambda t: (TYPE_ORDER.get(t.get("type"), 9),
                                   -(t["xAft"]) if t.get("xAft") is not None else 1e9, t.get("side") or ""))
    return L, log.notes, log.fixes, log.estimated

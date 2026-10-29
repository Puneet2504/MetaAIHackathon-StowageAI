"""What the assistant remembers for each user: standing instructions and preferences.

Only things the planner says explicitly are remembered ("always…", "from now on…", "remember…"),
and "forget…" removes them. Everything is visible and editable on the Memory page.
Stored in data/users/<id>/memory.json.

Items with the same scope, kind and key replace each other, so a new trim limit updates the old one.
Constraint items are applied by code before the solver runs (no tokens); notes, grade aliases and
explanation style are passed to the model as a few short "Memory:" lines.
"""
import json
import secrets
import threading
from datetime import datetime, timezone

from . import config, vessels
from .schemas import MemoryOp

SINGLE = {"max_trim", "explain_style", "fill_mode"}  # one per scope
CONSTRAINT_KINDS = {"constraint_empty", "constraint_fixed", "max_trim", "fill_mode"}
MAX_LINES, MAX_CHARS = 8, 300

_lock = threading.Lock()


def _path(user: str):
    return config.USER_DIR / user / "memory.json"


def load(user: str) -> list[dict]:
    p = _path(user)
    items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    if _notes_to_rules(items):
        _save(user, items)
    return items


def _notes_to_rules(items: list[dict]) -> bool:
    """A note that is really a fill rule ("keep tanks below 98% so all are used") becomes one, so the solver applies it.

    Notes saved before fill rules existed are converted the first time memory is read; the person's wording is kept.
    """
    from .llm.fast_paths import fill_rule  # imported here: fast_paths imports schemas, which memory also needs
    changed = False
    for i in items:
        if i["kind"] == "note" and (f := fill_rule(i.get("text", ""))):
            if any(j is not i and j["kind"] == "fill_mode" and j["scope"] == i["scope"] for j in items):
                continue  # a fill rule for this scope already exists; leave the note as it is
            i.update(kind="fill_mode", key=None, value=f, updated_at=_now())
            changed = True
    return changed


def _save(user: str, items: list[dict]) -> None:
    vessels.write_json(_path(user), items)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(kind: str, key) -> str | None:
    return None if kind in SINGLE else (str(key).strip().upper() if kind.startswith("constraint") else
                                        str(key).strip().lower() if key is not None else None)


def _same(item: dict, scope: str, kind: str, key) -> bool:
    return item["scope"] == scope and item["kind"] == kind and item.get("key") == _key(kind, key)


def apply_ops(user: str, vessel_id: str | None, ops: list[MemoryOp], source: str = "instruction") -> tuple[list[dict], list[dict]]:
    """Apply set/forget operations. Returns (remembered, forgotten) items."""
    remembered, forgotten = [], []
    with _lock:
        items = load(user)
        for op in ops:
            scope = vessel_id if op.scope == "vessel" and vessel_id else "global"
            if op.kind.startswith("constraint") and op.scope == "vessel" and not vessel_id:
                continue  # tank rules only make sense for one ship
            if op.op == "forget":
                scopes = {scope, "global"} if scope != "global" else {"global"}
                gone = [i for i in items if any(_same(i, s, op.kind, op.key) for s in scopes)
                        and (op.kind != "note" or op.text.lower() in i["text"].lower())]
                items = [i for i in items if i not in gone]
                forgotten += gone
                continue
            existing = next((i for i in items if op.kind != "note" and _same(i, scope, op.kind, op.key)), None)
            if existing:
                existing.update(value=op.value, text=op.text or existing["text"], updated_at=_now(), source=source)
                remembered.append(existing)
            else:
                item = {"id": secrets.token_hex(6), "scope": scope, "kind": op.kind, "key": _key(op.kind, op.key),
                        "value": op.value, "text": op.text or op.kind, "source": source,
                        "created_at": _now(), "updated_at": _now(), "uses": 0}
                items.append(item)
                remembered.append(item)
        _save(user, items)
    return remembered, forgotten


def relevant(user: str, vessel_id: str | None) -> list[dict]:
    return [i for i in load(user) if i["scope"] in ("global", vessel_id)]


def constraints_for(user: str, vessel: dict, skip: set[str] = frozenset()) -> tuple[dict, list[dict]]:
    """Remembered constraints for this ship, as solver constraints, plus the items used."""
    cargo = {t["id"] for t in vessel["tanks"] if t["type"] == "cargo"}
    empty, fixed, trim, fill, used = [], {}, None, None, []
    # Global first, so a ship's own rule wins over a general one.
    for i in sorted(relevant(user, vessel["id"]), key=lambda i: i["scope"] != "global"):
        if i["id"] in skip or i["kind"] not in CONSTRAINT_KINDS:
            continue
        if i["kind"] == "constraint_empty" and i["key"] in cargo:
            empty.append(i["key"]); used.append(i)
        elif i["kind"] == "constraint_fixed" and i["key"] in cargo and i.get("value"):
            fixed[i["key"]] = str(i["value"]); used.append(i)
        elif i["kind"] == "max_trim" and isinstance(i.get("value"), (int, float)):
            trim = float(i["value"]); used.append(i)
        elif i["kind"] == "fill_mode" and i.get("value") in ("even", "full"):
            used = [u for u in used if u["kind"] != "fill_mode"]  # the ship's own rule replaces the general one
            fill = i["value"]; used.append(i)
    return {"empty_tanks": sorted(set(empty)), "fixed_grades": fixed, "max_trim_m": trim, "fill": fill}, used


def aliases(user: str) -> dict[str, str]:
    return {i["key"]: str(i["value"]) for i in load(user) if i["kind"] == "grade_alias" and i.get("key") and i.get("value")}


def context_lines(user: str, vessel_id: str | None, kinds=("grade_alias", "note", "explain_style")) -> list[str]:
    """A few short lines for the model. Ship-specific first, capped by count and length."""
    items = sorted((i for i in relevant(user, vessel_id) if i["kind"] in kinds), key=lambda i: i["scope"] == "global")
    out, size = [], 0
    for i in items:
        line = i["text"].strip()
        if len(out) >= MAX_LINES or size + len(line) > MAX_CHARS:
            break
        out.append(line)
        size += len(line)
    return out


def mark_used(user: str, ids: list[str]) -> None:
    if not ids:
        return
    with _lock:
        items = load(user)
        for i in items:
            if i["id"] in ids:
                i["uses"] = i.get("uses", 0) + 1
        _save(user, items)


# ---------- editing from the Memory page ----------

def add_note(user: str, text: str, vessel_id: str | None = None) -> dict:
    """A note for the assistant; one that states a fill rule is stored as that rule, so the solver applies it."""
    from .llm.fast_paths import fill_rule
    fill = fill_rule(text)
    op = MemoryOp(op="set", scope="vessel" if vessel_id else "global", kind="fill_mode" if fill else "note",
                  value=fill, text=text.strip())
    remembered, _ = apply_ops(user, vessel_id, [op], source="memory page")
    return remembered[0]


def patch(user: str, item_id: str, text: str | None = None, value=None) -> dict | None:
    with _lock:
        items = load(user)
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            return None
        if text is not None:
            item["text"] = text.strip()
        if value is not None:
            if item["kind"] == "max_trim":
                item["value"] = float(value)
            else:
                item["value"] = value
        item["updated_at"] = _now()
        _save(user, items)
    return item


def delete(user: str, item_id: str | None = None) -> int:
    with _lock:
        items = load(user)
        keep = [i for i in items if item_id is not None and i["id"] != item_id]
        _save(user, keep)
    return len(items) - len(keep)

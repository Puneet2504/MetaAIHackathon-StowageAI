"""Loads vessel models: built-ins from data/vessels/*.json, user ships from data/users/<user>/*.json."""
import json
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path

from . import config
from .config import VESSEL_DIR


@lru_cache
def load_all() -> dict[str, dict]:
    vessels = {}
    for path in sorted(VESSEL_DIR.glob("*.json")):
        v = json.loads(path.read_text(encoding="utf-8"))
        vessels[v["id"]] = v
    return vessels


def _user_dir(user: str) -> Path:
    return config.USER_DIR / user


# Other per-user files that live next to the ship files.
RESERVED = {"memory", "usage", "accounts"}


def _user_path(user: str, vessel_id: str) -> Path | None:
    if not re.fullmatch(r"[a-z0-9-]{1,64}", vessel_id) or vessel_id in RESERVED:
        return None
    return _user_dir(user) / f"{vessel_id}.json"


def get(vessel_id: str, user: str | None = None) -> dict | None:
    if user:
        path = _user_path(user, vessel_id)
        if path and path.exists():
            return _with_strength(json.loads(path.read_text(encoding="utf-8")))
    v = load_all().get(vessel_id)
    return _with_strength(v) if v else None


def _with_strength(v: dict) -> dict:
    """Ships saved before strength limits existed get them on read (not saved), so every ship is checked."""
    if not (v.get("strength") or {}).get("readingPoints"):
        from .vessel_builder import strength_for  # imported here: the builder imports the schemas that use this module
        v = {**v, "strength": strength_for(v)}
    return v


def is_owned(vessel_id: str, user: str) -> bool:
    path = _user_path(user, vessel_id)
    return bool(path and path.exists())


def own_paths(user: str) -> list[Path]:
    return [p for p in sorted(_user_dir(user).glob("*.json")) if p.stem not in RESERVED]


def list_for(user: str) -> list[dict]:
    own = [json.loads(p.read_text(encoding="utf-8")) for p in own_paths(user)]
    return list(load_all().values()) + own


def new_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "ship"
    return f"{slug}-{secrets.token_hex(3)}"


def write_json(path: Path, data: dict) -> None:
    """Write atomically, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def save(user: str, vessel: dict) -> None:
    path = _user_path(user, vessel["id"])
    if path is None:
        raise ValueError("Invalid vessel id")
    write_json(path, vessel)


def delete(user: str, vessel_id: str) -> bool:
    path = _user_path(user, vessel_id)
    if path and path.exists():
        path.unlink()
        return True
    return False

"""Loads vessel models from data/vessels/*.json."""
import json
from functools import lru_cache

from .config import VESSEL_DIR


@lru_cache
def load_all() -> dict[str, dict]:
    vessels = {}
    for path in sorted(VESSEL_DIR.glob("*.json")):
        v = json.loads(path.read_text(encoding="utf-8"))
        vessels[v["id"]] = v
    return vessels


def get(vessel_id: str) -> dict | None:
    return load_all().get(vessel_id)

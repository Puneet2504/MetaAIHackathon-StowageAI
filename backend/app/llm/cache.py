"""Small disk cache of model replies, so asking the same thing twice costs no tokens.

Keyed by a hash of the model and messages. Image requests are never cached.
"""
import hashlib
import json
import threading
import time

from .. import config

MAX_ENTRIES = 500
TTL_S = 7 * 86400
enabled = True  # the test-set runner turns this off so every case reaches the model

_lock = threading.Lock()


def _path():
    return config.CACHE_DIR / "llm.json"


def key(model: str, messages: list[dict], max_tokens: int | None, effort: str | None = None) -> str | None:
    if any(isinstance(m.get("content"), list) for m in messages):
        return None  # has an image
    raw = json.dumps([model, max_tokens, effort, messages], separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _load() -> dict:
    p = _path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except ValueError:
        return {}


def get(k: str | None) -> str | None:
    if not (enabled and k):
        return None
    with _lock:
        hit = _load().get(k)
    if hit and time.time() - hit["t"] < TTL_S:
        return hit["v"]
    return None


def put(k: str | None, value: str) -> None:
    if not (enabled and k):
        return
    from ..vessels import write_json  # avoid an import cycle at module load
    with _lock:
        data = _load()
        data[k] = {"t": time.time(), "v": value}
        if len(data) > MAX_ENTRIES:
            data = dict(sorted(data.items(), key=lambda kv: kv[1]["t"])[-MAX_ENTRIES:])
        write_json(_path(), data)

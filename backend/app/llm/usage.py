"""Token accounting. Routes wrap model work in `track(user)`; the client records into it.

Per-user daily totals are kept in data/users/<id>/usage.json (input/output tokens, model calls,
cache hits, and requests handled by code without the model).
"""
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from .. import config

_lock = threading.Lock()
KEEP_DAYS = 30


@dataclass
class Counter:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    cache_hits: int = 0
    skipped: int = 0  # handled by code, no model call

    def add(self, other: "Counter") -> None:
        for k, v in asdict(other).items():
            setattr(self, k, getattr(self, k) + v)


_active: ContextVar[Counter | None] = ContextVar("llm_usage", default=None)


@contextmanager
def track(user: str | None = None):
    """Collect usage for the code inside; saved to the user's daily totals when a user is given."""
    counter = Counter()
    token = _active.set(counter)
    try:
        yield counter
    finally:
        _active.reset(token)
        if user and asdict(counter) != asdict(Counter()):
            _save(user, counter)


def record(prompt_tokens: int = 0, completion_tokens: int = 0, *, call: bool = False,
           cache_hit: bool = False, skipped: bool = False) -> None:
    c = _active.get()
    if c is None:
        return
    c.prompt_tokens += int(prompt_tokens or 0)
    c.completion_tokens += int(completion_tokens or 0)
    c.calls += int(call)
    c.cache_hits += int(cache_hit)
    c.skipped += int(skipped)


def _path(user: str):
    return config.USER_DIR / user / "usage.json"


def _load(user: str) -> dict:
    p = _path(user)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(user: str, counter: Counter) -> None:
    from ..vessels import write_json  # avoid an import cycle at module load
    with _lock:
        data = _load(user)
        today = date.today().isoformat()
        day = Counter(**data.get(today, {}))
        day.add(counter)
        data[today] = asdict(day)
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        write_json(_path(user), {k: v for k, v in sorted(data.items()) if k >= cutoff})


def summary(user: str, days: int = 7) -> dict:
    data = _load(user)
    out = []
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        out.append({"date": d, **asdict(Counter(**data.get(d, {})))})
    total = Counter()
    for row in out:
        total.add(Counter(**{k: v for k, v in row.items() if k != "date"}))
    return {"today": out[-1], "days": out, "total": asdict(total)}

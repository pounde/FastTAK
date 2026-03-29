"""In-memory health cache — scheduler writes, everyone else reads."""

import threading
from datetime import UTC, datetime

_cache: dict[str, dict] = {}
_lock = threading.Lock()


def update(service: str, raw: dict, evaluated: dict, thresholds: dict | None) -> None:
    """Write an evaluated health snapshot to the cache."""
    with _lock:
        _cache[service] = {
            "status": evaluated["status"],
            "message": evaluated.get("message"),
            "data": raw,
            "thresholds": thresholds,
            "updated_at": datetime.now(UTC).isoformat(),
        }


def fetch(service: str) -> dict | None:
    """Read a single service's cached health snapshot."""
    return _cache.get(service)


def fetch_all() -> dict:
    """Read the full cache snapshot."""
    with _lock:
        return dict(_cache)

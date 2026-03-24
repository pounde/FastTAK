"""Check GitHub for newer releases of stack components."""

import time
from typing import Any

import httpx

from app.config import settings

# Map of component -> (GitHub owner/repo, current version from .env)
COMPONENTS = {
    "authentik": ("goauthentik/authentik", settings.authentik_version),
    "mediamtx": ("bluenviron/mediamtx", settings.mediamtx_version),
    "nodered": ("node-red/node-red", settings.nodered_version),
    "tak-portal": ("AdventureSeeker423/TAK-Portal", settings.tak_portal_version),
}

def _extract_version(tag: str) -> str:
    """Extract version number from various tag formats.

    Handles: "v1.2.3", "version/2026.2.1", "1.2.3", "4.1.7"
    """
    # Strip common prefixes
    for prefix in ("version/", "v"):
        if tag.startswith(prefix):
            tag = tag[len(prefix):]
            break
    return tag


_cache: dict[str, Any] = {}
_cache_time: float = 0
CACHE_TTL = 3600  # 1 hour


async def check_updates() -> list[dict]:
    """Check GitHub for latest releases of each component."""
    global _cache, _cache_time

    if _cache and (time.time() - _cache_time) < CACHE_TTL:
        return _cache.get("results", [])

    results = []
    async with httpx.AsyncClient(timeout=15) as client:
        for name, (repo, current) in COMPONENTS.items():
            try:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo}/releases/latest",
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    latest = _extract_version(data.get("tag_name", ""))
                    current_clean = _extract_version(current)
                    # Simple string comparison — flags any difference as an
                    # update. Does not handle pre-release pins or downgrades.
                    # Use packaging.version if semver comparison is needed later.
                    results.append({
                        "name": name,
                        "current": current_clean,
                        "latest": latest,
                        "update_available": latest != current_clean and latest != "",
                        "release_url": data.get("html_url", ""),
                    })
                else:
                    results.append({
                        "name": name,
                        "current": current,
                        "latest": "unknown",
                        "update_available": False,
                        "error": f"HTTP {resp.status_code}",
                    })
            except Exception as e:
                results.append({
                    "name": name, "current": current, "latest": "unknown",
                    "update_available": False, "error": str(e)[:100],
                })

    _cache = {"results": results}
    _cache_time = time.time()
    return results

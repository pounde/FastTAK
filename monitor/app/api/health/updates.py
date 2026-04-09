"""Check GitHub for newer releases of stack components."""

import httpx

from app.config import settings

# Map of component -> (GitHub owner/repo, current version from .env)
COMPONENTS = {
    "lldap": ("lldap/lldap", settings.lldap_version),
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
            tag = tag[len(prefix) :]
            break
    return tag


def check_updates() -> dict:
    """Check GitHub for latest releases of each component."""
    results = []
    with httpx.Client(timeout=15) as client:
        for name, (repo, current) in COMPONENTS.items():
            try:
                resp = client.get(
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
                    results.append(
                        {
                            "name": name,
                            "current": current_clean,
                            "latest": latest,
                            "update_available": latest != current_clean and latest != "",
                            "release_url": data.get("html_url", ""),
                        }
                    )
                else:
                    results.append(
                        {
                            "name": name,
                            "current": current,
                            "latest": "unknown",
                            "update_available": False,
                            "error": f"HTTP {resp.status_code}",
                        }
                    )
            except Exception as e:
                results.append(
                    {
                        "name": name,
                        "current": current,
                        "latest": "unknown",
                        "update_available": False,
                        "error": str(e)[:100],
                    }
                )

    return {"items": results}

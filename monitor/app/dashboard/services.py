"""Parse Caddyfile to discover service URLs."""

import re
from pathlib import Path

from app.config import settings

CADDYFILE = Path("/opt/fastak/caddy/Caddyfile")

# Matches: {$SOMETHING_SUBDOMAIN}.{$FQDN} {
# Assumes the opening brace is on the same line as the site address.
# Caddy also supports other formats, but FastTAK's Caddyfile uses this pattern.
_SITE_RE = re.compile(r"^\{\$(\w+)\}\.\{\$FQDN\}\s*\{")

# Map env var names to friendly display names (strip _SUBDOMAIN suffix, title case)
_NAME_OVERRIDES = {
    "TAKSERVER_SUBDOMAIN": "TAK Server",
    "TAKPORTAL_SUBDOMAIN": "TAK Portal",
    "MEDIAMTX_SUBDOMAIN": "MediaMTX",
    "NODERED_SUBDOMAIN": "Node-RED",
    "AUTHENTIK_SUBDOMAIN": "Authentik",
    "MONITOR_SUBDOMAIN": "Monitor",
}


def _resolve_var(var_name: str) -> str | None:
    """Resolve a Caddyfile env var to its value from settings."""
    # Convert TAKSERVER_SUBDOMAIN -> takserver_subdomain
    attr = var_name.lower()
    return getattr(settings, attr, None)


def get_service_links() -> list[dict]:
    """Parse Caddyfile and return discovered service URLs."""
    if not CADDYFILE.exists():
        return []

    links = []
    try:
        for line in CADDYFILE.read_text().splitlines():
            m = _SITE_RE.match(line.strip())
            if not m:
                continue

            var_name = m.group(1)
            subdomain = _resolve_var(var_name)
            if not subdomain or not settings.fqdn:
                continue

            default_name = var_name.replace("_SUBDOMAIN", "").replace("_", " ").title()
            name = _NAME_OVERRIDES.get(var_name, default_name)
            links.append(
                {
                    "name": name,
                    "url": f"https://{subdomain}.{settings.fqdn}",
                }
            )
    except OSError:
        pass

    return links

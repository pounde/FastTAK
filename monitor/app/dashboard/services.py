"""Build service URLs from deployment configuration."""

from app.config import settings

# Service definitions: (name, subdomain_attr, port_attr)
# portal has no dedicated port — it's on Caddy's default 443
_SERVICES = [
    ("TAK Portal", "takportal_subdomain", None),
    ("TAK Server", "takserver_subdomain", "takserver_admin_port"),
    ("Node-RED", "nodered_subdomain", "nodered_port"),
    ("Monitor", "monitor_subdomain", "monitor_port"),
    ("MediaMTX", "mediamtx_subdomain", "mediamtx_port"),
]


def get_service_links() -> list[dict]:
    """Return service URLs based on deploy mode."""
    if not settings.server_address:
        return []

    links = []
    for name, subdomain_attr, port_attr in _SERVICES:
        if settings.deploy_mode == "direct":
            if port_attr:
                port = getattr(settings, port_attr)
                url = f"https://{settings.server_address}:{port}"
            else:
                url = f"https://{settings.server_address}"
        else:
            subdomain = getattr(settings, subdomain_attr)
            url = f"https://{subdomain}.{settings.server_address}"

        links.append({"name": name, "url": url})

    return links

"""TLS certificate expiry checks via socket connection.

Probes the HTTPS endpoints served by Caddy to check certificate expiry.
In subdomain mode, probes subdomain endpoints with trusted TLS.
In direct mode, probes port-based endpoints with self-signed TLS
(Caddy's internal CA). This is separate from the TAK cert monitoring
(health/certs.py) which reads PEM files from disk.
"""

import socket
import ssl
from datetime import UTC, datetime

from app.config import settings


def _probe_tls_expiry(hostname: str, port: int = 443) -> dict | None:
    """Connect to a TLS endpoint and return cert expiry info."""
    try:
        ctx = ssl.create_default_context()
        # In direct mode, Caddy uses self-signed certs (internal CA).
        # Disable verification so probes don't fail on untrusted certs.
        if settings.deploy_mode == "direct":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((hostname, port), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                if not cert:
                    return None

                not_after = cert.get("notAfter", "")
                # Format: "Mar 23 12:00:00 2027 GMT"
                expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                expiry = expiry.replace(tzinfo=UTC)
                now = datetime.now(UTC)
                days_left = (expiry - now).days

                return {
                    "domain": f"{hostname}:{port}" if port != 443 else hostname,
                    "expires": expiry.strftime("%Y-%m-%d"),
                    "days_left": days_left,
                }
    except Exception:
        return None


def get_tls_status() -> dict:
    """Probe TLS expiry on all Caddy-served endpoints."""
    server_address = settings.server_address
    if not server_address or server_address == "localhost":
        return {"items": []}

    if settings.deploy_mode == "direct":
        endpoints = [
            (server_address, 443),
            (server_address, settings.takserver_admin_port),
            (server_address, settings.nodered_port),
            (server_address, settings.monitor_port),
            (server_address, settings.mediamtx_port),
        ]
    else:
        endpoints = [
            (f"{settings.takserver_subdomain}.{server_address}", 443),
            (f"{settings.takportal_subdomain}.{server_address}", 443),
            (f"{settings.nodered_subdomain}.{server_address}", 443),
            (f"{settings.mediamtx_subdomain}.{server_address}", 443),
        ]

    results = []
    seen_expiry = set()
    for host, port in endpoints:
        info = _probe_tls_expiry(host, port)
        if info:
            key = info["expires"]
            if key in seen_expiry:
                continue
            seen_expiry.add(key)
            results.append(info)

    return {"items": results}

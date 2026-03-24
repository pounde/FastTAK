"""TLS certificate expiry checks via socket connection.

Probes the HTTPS endpoints served by Caddy to check Let's Encrypt
certificate expiry. This is separate from the TAK cert monitoring
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

                subject = dict(x[0] for x in cert.get("subject", ()))
                cn = subject.get("commonName", hostname)

                return {
                    "hostname": hostname,
                    "cn": cn,
                    "expires": expiry.isoformat(),
                    "days_left": days_left,
                    "status": (
                        "expired"
                        if days_left <= 0
                        else "critical"
                        if days_left <= 7
                        else "warning"
                        if days_left <= 14
                        else "ok"
                    ),
                }
    except Exception:
        return None


def get_tls_status() -> list[dict]:
    """Probe TLS expiry on all Caddy-served endpoints."""
    fqdn = settings.fqdn
    if not fqdn or fqdn == "localhost":
        return []

    endpoints = [
        f"{settings.takserver_subdomain}.{fqdn}",
        f"{settings.authentik_subdomain}.{fqdn}",
        f"{settings.takportal_subdomain}.{fqdn}",
        f"{settings.nodered_subdomain}.{fqdn}",
        f"{settings.mediamtx_subdomain}.{fqdn}",
    ]

    results = []
    seen_expiry = set()
    for host in endpoints:
        info = _probe_tls_expiry(host)
        if info:
            # Caddy uses the same cert for all subdomains (wildcard or SAN),
            # so deduplicate by expiry date
            key = info["expires"]
            if key in seen_expiry:
                continue
            seen_expiry.add(key)
            results.append(info)

    return results

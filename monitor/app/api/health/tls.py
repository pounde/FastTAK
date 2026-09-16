"""TLS certificate expiry checks via socket connection.

Probes the HTTPS endpoints served by Caddy to check certificate expiry.
In subdomain mode these are public-CA certs; in direct mode Caddy's internal
CA. Either way the probe's job is *expiry*, not trust, and a verifying
handshake cannot read the expiry of an already-expired cert — so the
handshake never verifies, and the leaf is read as DER and parsed (#57).
This is separate from the TAK cert monitoring (health/certs.py) which reads
PEM files from disk.
"""

import socket
import ssl
from datetime import UTC, datetime

from cryptography import x509

from app.config import settings


def _probe_tls_expiry(hostname: str, port: int = 443) -> dict:
    """Connect to a TLS endpoint and return cert expiry, or the reason it
    could not be read. Never None."""
    domain = f"{hostname}:{port}" if port != 443 else hostname
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((hostname, port), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                der = ssock.getpeercert(binary_form=True)
    except Exception as exc:  # any transport or handshake failure is a reason
        reason = f"{type(exc).__name__}{': ' + str(exc) if str(exc) else ''}"
        return {"domain": domain, "error": reason[:200]}

    if not der:
        return {"domain": domain, "error": "no certificate presented"}
    try:
        expiry = x509.load_der_x509_certificate(der).not_valid_after_utc
    except ValueError as exc:
        return {"domain": domain, "error": f"unparseable certificate: {exc}"[:200]}

    days_left = (expiry - datetime.now(UTC)).days
    return {"domain": domain, "expires": expiry.strftime("%Y-%m-%d"), "days_left": days_left}


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
            (f"{settings.nodered_subdomain}.{server_address}", 443),
            (f"{settings.mediamtx_subdomain}.{server_address}", 443),
        ]

    results = []
    seen_expiry = set()
    for host, port in endpoints:
        info = _probe_tls_expiry(host, port)
        if "error" in info:
            results.append(info)  # a reason is never collapsed away
            continue
        key = info["expires"]
        if key in seen_expiry:
            continue
        seen_expiry.add(key)
        results.append(info)

    return {"items": results}

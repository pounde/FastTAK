"""Parse x509 certificate files and report expiry status.

Certs are categorized as infrastructure, service, or user:
- Infrastructure (CA, server): triggers health degradation on expiry
- Service (svc_*): worth monitoring but not a system health issue
- User: excluded from health monitoring entirely
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

CERT_DIR = Path("/opt/tak/certs/files")

# Infrastructure cert filename patterns — these affect system operation
_INFRA_NAMES = {"ca", "root-ca", "ca-trusted", "root-ca-trusted", "takserver"}


def _categorize_cert(filename: str) -> str:
    """Categorize a cert file as infrastructure, service, or user.

    Args:
        filename: The .pem filename (e.g., "ca.pem", "svc_bot.pem").

    Returns:
        "infrastructure", "service", or "user".
    """
    stem = filename.removesuffix(".pem")
    if stem in _INFRA_NAMES:
        return "infrastructure"
    if stem.startswith("svc_"):
        return "service"
    # Server certs for SERVER_ADDRESS (e.g., "mbp.fold-harmonic.ts.net.pem")
    # contain dots — user certs use alphanumeric + hyphens only
    if "." in stem and stem not in _INFRA_NAMES:
        return "infrastructure"
    return "user"


def _parse_cert_expiry(pem_path: Path) -> dict | None:
    """Parse a PEM file and return name + expiry info."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout", "-subject", "-enddate"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        subject = ""
        expiry_str = ""
        for line in result.stdout.strip().split("\n"):
            if line.startswith("subject="):
                subject = line.split("subject=", 1)[1].strip()
            elif line.startswith("notAfter="):
                expiry_str = line.split("notAfter=", 1)[1].strip()

        if not expiry_str:
            return None

        # Parse: "Mar 23 12:00:00 2027 GMT"
        expiry = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
        expiry = expiry.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        days_left = (expiry - now).days

        return {
            "file": pem_path.name,
            "subject": subject,
            "expires": expiry.strftime("%Y-%m-%d"),
            "days_left": days_left,
        }
    except Exception:
        return None


def get_cert_status() -> dict:
    """Return expiry info for infrastructure and service certs.

    User certs are excluded — their expiry is managed through the user
    detail panel, not the health dashboard. Only infrastructure certs
    (CA, server) and service account certs are monitored.
    """
    if not CERT_DIR.exists():
        return {"items": []}
    results = []
    for pem in sorted(CERT_DIR.glob("*.pem")):
        category = _categorize_cert(pem.name)
        if category == "user":
            continue
        info = _parse_cert_expiry(pem)
        if info:
            info["category"] = category
            results.append(info)
    return {"items": results}

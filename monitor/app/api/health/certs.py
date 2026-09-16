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


def _parse_cert_expiry(pem_path: Path) -> dict:
    """Parse a PEM file: name + expiry, or an item carrying the reason it
    could not be read. Never None — a cert that vanishes from the list reads
    as fine on the dashboard, which is the failure mode #57 is about.
    """
    name = pem_path.name
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout", "-subject", "-enddate"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return {"file": name, "error": "openssl timed out after 5s"}
    if result.returncode != 0:
        reason = (result.stderr or "").strip().splitlines()
        return {
            "file": name,
            "error": "openssl: " + (reason[0] if reason else f"exit {result.returncode}"),
        }

    subject = ""
    expiry_str = ""
    for line in result.stdout.strip().split("\n"):
        if line.startswith("subject="):
            subject = line.split("subject=", 1)[1].strip()
        elif line.startswith("notAfter="):
            expiry_str = line.split("notAfter=", 1)[1].strip()

    if not expiry_str:
        return {"file": name, "error": "no notAfter in openssl output"}

    try:
        # Parse: "Mar 23 12:00:00 2027 GMT"
        expiry = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except ValueError:
        return {"file": name, "error": f"unparseable notAfter: {expiry_str}"}
    days_left = (expiry - datetime.now(UTC)).days

    return {
        "file": name,
        "subject": subject,
        "expires": expiry.strftime("%Y-%m-%d"),
        "days_left": days_left,
    }


def get_cert_status() -> dict:
    """Return expiry info for infrastructure and service certs.

    User certs are excluded — their expiry is managed through the user
    detail panel, not the health dashboard. Only infrastructure certs
    (CA, server) and service account certs are monitored. A cert that cannot
    be read is an item with `error`; openssl missing altogether is a
    probe-level error, since nothing can be read.
    """
    if not CERT_DIR.exists():
        return {"items": []}
    results = []
    for pem in sorted(CERT_DIR.glob("*.pem")):
        category = _categorize_cert(pem.name)
        if category == "user":
            continue
        try:
            info = _parse_cert_expiry(pem)
        except OSError as exc:
            return {"error": f"openssl could not run: {exc}"}
        info["category"] = category
        results.append(info)
    return {"items": results}

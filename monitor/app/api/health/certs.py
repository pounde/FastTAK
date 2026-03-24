"""Parse x509 certificate files and report expiry status."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

CERT_DIR = Path("/opt/tak/certs/files")


def _parse_cert_expiry(pem_path: Path) -> dict | None:
    """Parse a PEM file and return name + expiry info."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout",
             "-subject", "-enddate"],
            capture_output=True, text=True, timeout=5,
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
        expiry = expiry.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_left = (expiry - now).days

        return {
            "file": pem_path.name,
            "subject": subject,
            "expires": expiry.isoformat(),
            "days_left": days_left,
            "status": (
                "expired" if days_left <= 0
                else "critical" if days_left <= 14
                else "warning" if days_left <= settings.cert_warn_days
                else "ok"
            ),
        }
    except Exception:
        return None


def get_cert_status() -> list[dict]:
    """Return expiry status for all PEM files in the cert directory."""
    if not CERT_DIR.exists():
        return []
    results = []
    for pem in sorted(CERT_DIR.glob("*.pem")):
        info = _parse_cert_expiry(pem)
        if info:
            results.append(info)
    return results

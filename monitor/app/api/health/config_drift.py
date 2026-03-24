"""Detect .env file changes since monitor startup.

The .env file is mounted read-only into the monitor container.
On startup, we hash it. Periodically, we re-hash and compare.
If the hash changes, we surface a warning that init containers
need to re-run to apply the new configuration.
"""

import hashlib
from pathlib import Path

ENV_FILE = Path("/opt/fastak/.env")

_startup_hash: str | None = None


def _hash_file(path: Path) -> str | None:
    """Return SHA-256 hex digest of a file, or None if unreadable."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, IOError):
        return None


def init_config_hash():
    """Call once at startup to record the baseline .env hash."""
    global _startup_hash
    _startup_hash = _hash_file(ENV_FILE)


def check_config_drift() -> dict:
    """Compare current .env hash against startup hash."""
    if _startup_hash is None:
        return {"status": "unavailable", "message": ".env file not mounted"}

    current = _hash_file(ENV_FILE)

    if current is None:
        return {"status": "error", "message": ".env file unreadable"}

    if current != _startup_hash:
        return {
            "status": "changed",
            "message": (
                "Configuration has changed since monitor started. "
                "Run: docker compose up -d --force-recreate init-config init-identity"
            ),
        }

    return {"status": "ok", "message": "Configuration unchanged"}

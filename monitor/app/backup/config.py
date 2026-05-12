"""Environment + path resolution for the backup module.

Every value here is read on demand. The compose mount maps the operator's
chosen host directory to `/backups` inside the container, so application
code always references `/backups`; only `BACKUP_DIR` for unit-test
overrides differs from that default.
"""

from __future__ import annotations

import os
from pathlib import Path


def backup_dir() -> Path:
    """Directory where tarballs, the age identity, and state.json live."""
    return Path(os.environ.get("BACKUP_DIR", "/backups"))


def retention_keep() -> int:
    """How many newest backups to retain after each successful run."""
    raw = os.environ.get("BACKUP_RETENTION_KEEP", "14")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"BACKUP_RETENTION_KEEP must be an int, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"BACKUP_RETENTION_KEEP must be >= 1, got {value}")
    return value


def fasttak_version() -> str:
    return os.environ.get("FASTTAK_VERSION", "dev")


def fasttak_commit() -> str:
    return os.environ.get("FASTTAK_COMMIT", "unknown")


def admin_group_default() -> str:
    """Default group name when BACKUP_ADMIN_GROUP is unset."""
    return "monitor_admin"


def ensure_backup_dir() -> None:
    """Create the backup directory with mode 0700 if missing."""
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)

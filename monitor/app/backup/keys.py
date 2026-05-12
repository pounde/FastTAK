"""Age identity lifecycle for the backup module.

Single file at `<BACKUP_DIR>/.age-identity` holds the x25519 identity. It
is created on first backup if missing and never rotated automatically
(rotation would invalidate every prior backup).

`age-keygen` produces text like:

    # created: 2026-05-11T12:00:00Z
    # public key: age1abc...
    AGE-SECRET-KEY-1XYZ...

We persist the file verbatim. `read_identity()` returns the full file so
the operator's downloaded copy is the same artifact `age` will accept on
restore.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.backup.config import backup_dir


def _path() -> Path:
    return backup_dir() / ".age-identity"


def read_identity() -> bytes | None:
    """Return the raw identity-file bytes, or None if not yet generated."""
    p = _path()
    if not p.exists():
        return None
    return p.read_bytes()


def load_or_create() -> tuple[str, str]:
    """Return `(identity, recipient)`.

    Generates a fresh identity if the file does not exist.
    """
    p = _path()
    if not p.exists():
        _generate(p)
    return _parse(p)


def _generate(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Tighten umask before age-keygen creates the file so it is never world-
    # or group-readable, even briefly between create and chmod.
    prev_umask = os.umask(0o077)
    try:
        proc = subprocess.run(["age-keygen", "-o", str(path)], capture_output=True, text=True)
    finally:
        os.umask(prev_umask)
    if proc.returncode != 0:
        raise RuntimeError(f"age-keygen failed: {proc.stderr.strip()}")
    os.chmod(path, 0o600)


def _parse(path: Path) -> tuple[str, str]:
    identity = ""
    recipient = ""
    for line in path.read_text().splitlines():
        if line.startswith("AGE-SECRET-KEY-"):
            identity = line.strip()
        elif line.startswith("# public key:"):
            recipient = line.split(":", 1)[1].strip()
    if not identity or not recipient:
        raise RuntimeError(f"identity file {path} is malformed")
    return identity, recipient

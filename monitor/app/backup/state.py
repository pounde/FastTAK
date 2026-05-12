"""Atomic JSON state for the backup module.

Schema (the only file under BACKUP_DIR with a leading dot besides
`.age-identity` and `.backup.lock`):

    {
      "key_downloaded_at": "2026-05-11T12:00:00+00:00" | null,
      "last_run": null | {
        "started_at": "...",
        "finished_at": "...",
        "status": "success" | "running" | "failed",
        "filename": "fasttak-backup-...age",
        "error": "..."
      }
    }

Atomicity: write to a sibling tempfile and `os.replace`. A corrupt file is
treated as missing — the operator's first read after recovery resets state
to defaults; the audit log retains the durable history.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.backup.config import backup_dir

DEFAULTS: dict[str, Any] = {"key_downloaded_at": None, "last_run": None}


def _path() -> Path:
    return backup_dir() / ".state.json"


def read() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return dict(DEFAULTS)
    try:
        return {**DEFAULTS, **json.loads(p.read_text())}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def write(payload: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state.json.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def mark_key_downloaded() -> None:
    current = read()
    current["key_downloaded_at"] = datetime.now(UTC).isoformat()
    write(current)


def write_last_run(last_run: dict[str, Any]) -> None:
    current = read()
    current["last_run"] = last_run
    write(current)

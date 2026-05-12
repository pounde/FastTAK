"""Backup retention: keep newest N, reap stale partials."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from app.backup.config import backup_dir

log = logging.getLogger(__name__)

# Reap *.partial files older than this. The default of 6 hours covers a
# very large `cot` dump (multi-GB CoT history) without prematurely
# clobbering an in-flight backup whose retention pass touches a sibling
# .partial. Operators with much larger DBs can extend via
# BACKUP_PARTIAL_REAP_AGE_SECONDS.
_DEFAULT_PARTIAL_REAP_AGE_SECONDS = 6 * 3600


def _partial_reap_age() -> int:
    raw = os.environ.get("BACKUP_PARTIAL_REAP_AGE_SECONDS")
    if not raw:
        return _DEFAULT_PARTIAL_REAP_AGE_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        log.warning(
            "BACKUP_PARTIAL_REAP_AGE_SECONDS=%r is not an integer; using default %ds",
            raw,
            _DEFAULT_PARTIAL_REAP_AGE_SECONDS,
        )
        return _DEFAULT_PARTIAL_REAP_AGE_SECONDS


def prune(*, keep: int) -> list[str]:
    """Delete `fasttak-backup-*.age` files older than the newest `keep` (by mtime).

    Also removes `*.partial` orphan files from crashed runs and any matching
    `.sha256` sidecar that lives next to a pruned archive. The partial cutoff
    defaults to 6 hours and can be overridden via the
    `BACKUP_PARTIAL_REAP_AGE_SECONDS` env var.

    Returns the list of deleted backup filenames (not partials, not sidecars).
    """
    d = backup_dir()
    _reap_partials(d)
    backups = sorted(
        d.glob("fasttak-backup-*.age"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    to_delete = backups[keep:]
    deleted: list[str] = []
    for p in to_delete:
        try:
            p.unlink()
            deleted.append(p.name)
        except FileNotFoundError:
            pass
        # Best-effort sidecar reap; a missing sidecar isn't an error
        # (e.g. backup predates the sidecar feature).
        sidecar = p.with_suffix(p.suffix + ".sha256")
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
    return deleted


def _reap_partials(d: Path) -> None:
    max_age = _partial_reap_age()
    now = time.time()
    for p in d.glob("fasttak-backup-*.partial"):
        try:
            if now - p.stat().st_mtime > max_age:
                p.unlink()
        except FileNotFoundError:
            continue

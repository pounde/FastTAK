"""Backup retention: keep newest N, reap stale partials."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from app.backup.config import backup_dir

log = logging.getLogger(__name__)

# Reap *.partial files older than this. The default of 6 hours covers a
# very large `cot` dump (multi-GB CoT history) without prematurely
# clobbering an in-flight backup whose retention pass touches a sibling
# .partial. Operators with much larger DBs can extend via
# BACKUP_PARTIAL_REAP_AGE_SECONDS.
_DEFAULT_PARTIAL_REAP_AGE_SECONDS = 6 * 3600

# runner._filename: fasttak-backup-<YYYYMMDDTHHMMSSZ>-<version>.age. The
# timestamp in the name is when the backup was taken; mtime is when the file
# last landed on this disk, which a restore or a copy resets.
_BACKUP_NAME = re.compile(r"^fasttak-backup-(\d{8}T\d{6}Z)-.+\.age$")


def stamped_backups(d: Path) -> list[tuple[datetime, Path]]:
    """Archives in `d` newest first, each with the time it was taken.

    The order and the age both come from the timestamp in the name, so prune,
    `backup list` and the dashboard agree on which backup is newest. A file
    matching the glob but not the name format cannot be placed in the order;
    it is logged and left out.
    """
    stamped: list[tuple[datetime, Path]] = []
    for p in d.glob("fasttak-backup-*.age"):
        m = _BACKUP_NAME.match(p.name)
        try:
            taken_at = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except (AttributeError, ValueError):
            # No match, or a stamp that is not a calendar date (day 00).
            log.warning("%s does not match the backup name format; leaving it", p.name)
            continue
        stamped.append((taken_at, p))
    return sorted(stamped, reverse=True)


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
    """Delete `fasttak-backup-*.age` files older than the newest `keep`.

    Age is the timestamp in the filename, not mtime — a restored or copied
    archive has a fresh mtime and would otherwise outlive the backup it was
    copied from. A file matching the glob but not the name format cannot be
    placed in the order, so it is left alone and logged.

    Also removes `*.partial` orphan files from crashed runs and any matching
    `.sha256` sidecar that lives next to a pruned archive. The partial cutoff
    defaults to 6 hours and can be overridden via the
    `BACKUP_PARTIAL_REAP_AGE_SECONDS` env var.

    Returns the list of deleted backup filenames (not partials, not sidecars).
    """
    d = backup_dir()
    _reap_partials(d)
    to_delete = [p for _, p in stamped_backups(d)[keep:]]
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

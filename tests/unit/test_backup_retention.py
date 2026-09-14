"""Tests for monitor/app/backup/retention.py."""

import os
import time

import pytest
from app.backup import retention


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    return tmp_path


def _mk(path, mtime_offset_seconds: float = 0.0) -> None:
    path.write_bytes(b"x")
    t = time.time() + mtime_offset_seconds
    os.utime(path, (t, t))


def test_prune_keeps_newest_n(backup_dir):
    for i in range(1, 6):
        _mk(backup_dir / f"fasttak-backup-2026010{i}T000000Z-v0.0.1.age", mtime_offset_seconds=i)
    removed = retention.prune(keep=2)
    remaining = sorted(p.name for p in backup_dir.glob("fasttak-backup-*.age"))
    assert remaining == [
        "fasttak-backup-20260104T000000Z-v0.0.1.age",
        "fasttak-backup-20260105T000000Z-v0.0.1.age",
    ]
    assert set(removed) == {
        "fasttak-backup-20260101T000000Z-v0.0.1.age",
        "fasttak-backup-20260102T000000Z-v0.0.1.age",
        "fasttak-backup-20260103T000000Z-v0.0.1.age",
    }


def test_prune_returns_empty_list_when_under_threshold(backup_dir):
    _mk(backup_dir / "fasttak-backup-x.age")
    assert retention.prune(keep=14) == []


def test_prune_ignores_non_backup_files(backup_dir):
    _mk(backup_dir / "fasttak-backup-x.age")
    _mk(backup_dir / "random.txt")
    _mk(backup_dir / ".age-identity")
    removed = retention.prune(keep=14)
    assert removed == []
    assert (backup_dir / "random.txt").exists()
    assert (backup_dir / ".age-identity").exists()


def test_prune_reaps_stale_partial_files(backup_dir, monkeypatch):
    # Configure a short threshold so the test doesn't need to fabricate
    # mtimes more than a few hours old.
    monkeypatch.setenv("BACKUP_PARTIAL_REAP_AGE_SECONDS", "3600")
    fresh = backup_dir / "fasttak-backup-fresh.age.partial"
    stale = backup_dir / "fasttak-backup-stale.age.partial"
    _mk(fresh, mtime_offset_seconds=-60)  # 1 minute ago — kept
    _mk(stale, mtime_offset_seconds=-7200)  # 2 hours ago — reaped
    retention.prune(keep=14)
    assert fresh.exists()
    assert not stale.exists()


def test_partial_reap_age_default_is_six_hours():
    from app.backup.retention import _partial_reap_age

    assert _partial_reap_age() == 6 * 3600


def test_partial_reap_age_honors_env_override(monkeypatch):
    from app.backup.retention import _partial_reap_age

    monkeypatch.setenv("BACKUP_PARTIAL_REAP_AGE_SECONDS", "1800")
    assert _partial_reap_age() == 1800


def test_partial_reap_age_falls_back_on_malformed_env(monkeypatch):
    from app.backup.retention import _partial_reap_age

    monkeypatch.setenv("BACKUP_PARTIAL_REAP_AGE_SECONDS", "not-a-number")
    assert _partial_reap_age() == 6 * 3600


def test_prune_reaps_matching_sha256_sidecar(backup_dir):
    # Two backups, each with a sidecar; keep=1 should delete the older
    # archive AND its sidecar.
    older = backup_dir / "fasttak-backup-20260101T000000Z-v0.0.1.age"
    newer = backup_dir / "fasttak-backup-20260102T000000Z-v0.0.1.age"
    _mk(older, mtime_offset_seconds=0)
    _mk(newer, mtime_offset_seconds=10)
    (backup_dir / f"{older.name}.sha256").write_text("deadbeef  " + older.name + "\n")
    (backup_dir / f"{newer.name}.sha256").write_text("c0ffee  " + newer.name + "\n")

    removed = retention.prune(keep=1)
    assert removed == [older.name]
    assert not older.exists()
    assert not (backup_dir / f"{older.name}.sha256").exists()
    assert newer.exists()
    assert (backup_dir / f"{newer.name}.sha256").exists()


def test_prune_does_not_fail_when_sidecar_is_missing(backup_dir):
    # An archive without a sidecar (e.g. pre-existing backup from before the
    # sidecar feature) must still prune cleanly.
    older = backup_dir / "fasttak-backup-20260101T000000Z-v0.0.1.age"
    newer = backup_dir / "fasttak-backup-20260102T000000Z-v0.0.1.age"
    _mk(older, mtime_offset_seconds=0)
    _mk(newer, mtime_offset_seconds=10)
    removed = retention.prune(keep=1)
    assert removed == [older.name]


def test_prune_orders_by_the_timestamp_in_the_name_not_mtime(backup_dir):
    """A restored or copied archive gets a fresh mtime. Ordering by mtime
    kept it and deleted the genuinely newest backup instead (#64)."""
    oldest = backup_dir / "fasttak-backup-20260101T000000Z-v0.0.1.age"
    middle = backup_dir / "fasttak-backup-20260102T000000Z-v0.0.1.age"
    newest = backup_dir / "fasttak-backup-20260103T000000Z-v0.0.1.age"
    _mk(oldest, mtime_offset_seconds=100)  # copied in just now
    _mk(middle, mtime_offset_seconds=0)
    _mk(newest, mtime_offset_seconds=-100)  # untouched since it was taken

    removed = retention.prune(keep=2)
    assert removed == [oldest.name]
    assert newest.exists() and middle.exists()


def test_prune_leaves_unparseable_names_alone(backup_dir, caplog):
    """A file that matches the glob but not the name format cannot be placed
    in the order, so it is neither kept in the count nor deleted."""
    odd = backup_dir / "fasttak-backup-manual-copy.age"
    a = backup_dir / "fasttak-backup-20260101T000000Z-v0.0.1.age"
    b = backup_dir / "fasttak-backup-20260102T000000Z-v0.0.1.age"
    for p in (odd, a, b):
        _mk(p)

    removed = retention.prune(keep=1)
    assert removed == [a.name]
    assert odd.exists()
    assert "fasttak-backup-manual-copy.age" in caplog.text


def test_stamped_backups_orders_by_name_and_reports_when_taken(backup_dir):
    """One reader of the filename for prune and both listings: newest first
    by the timestamp in the name, with that timestamp as when it was taken.
    Names that do not fit the format are not in the list."""
    from datetime import UTC, datetime

    older = backup_dir / "fasttak-backup-20260101T000000Z-v0.0.1.age"
    newer = backup_dir / "fasttak-backup-20260102T120000Z-v0.0.1.age"
    odd = backup_dir / "fasttak-backup-manual-copy.age"
    _mk(older, mtime_offset_seconds=100)  # copied in just now
    _mk(newer, mtime_offset_seconds=-100)
    _mk(odd)

    stamped = retention.stamped_backups(backup_dir)

    assert [p for _, p in stamped] == [newer, older]
    assert stamped[0][0] == datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)


def test_prune_leaves_impossible_dates_alone(backup_dir, caplog):
    """A name that fits the format but not the calendar (day 00) is as
    unplaceable as a malformed one. It must not abort the retention pass."""
    odd = backup_dir / "fasttak-backup-20260100T000000Z-v0.0.1.age"
    a = backup_dir / "fasttak-backup-20260101T000000Z-v0.0.1.age"
    b = backup_dir / "fasttak-backup-20260102T000000Z-v0.0.1.age"
    for p in (odd, a, b):
        _mk(p)

    removed = retention.prune(keep=1)
    assert removed == [a.name]
    assert odd.exists()
    assert "20260100T000000Z" in caplog.text

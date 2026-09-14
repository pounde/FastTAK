"""Tests for monitor/app/backup/cli.py."""

import os
import time

import pytest
from app.backup import cli


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    return tmp_path


def _mk(path, mtime_offset_seconds: float = 0.0) -> None:
    path.write_bytes(b"x")
    t = time.time() + mtime_offset_seconds
    os.utime(path, (t, t))


def test_list_orders_and_ages_by_the_timestamp_in_the_name(backup_dir, capsys):
    """`just backup list` must agree with prune about which backup is newest.
    A restored copy has a fresh mtime; by mtime it showed at the top as
    "0.0m ago" while retention treated it as the oldest (#64)."""
    older = backup_dir / "fasttak-backup-20260101T000000Z-v0.0.1.age"
    newer = backup_dir / "fasttak-backup-20260102T000000Z-v0.0.1.age"
    _mk(older, mtime_offset_seconds=0)  # copied in just now
    _mk(newer, mtime_offset_seconds=-3600)

    assert cli.main(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()

    assert [ln.split("\t")[0] for ln in lines] == [newer.name, older.name]
    older_age_minutes = float(lines[1].split("\t")[2].removesuffix("m ago"))
    assert older_age_minutes > 24 * 60

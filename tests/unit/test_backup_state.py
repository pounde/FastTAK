"""Tests for monitor/app/backup/state.py."""

from datetime import UTC, datetime

import pytest
from app.backup import state


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    return tmp_path


def test_read_returns_defaults_when_file_missing(backup_dir):
    s = state.read()
    assert s == {"key_downloaded_at": None, "last_run": None}


def test_write_then_read_round_trips(backup_dir):
    payload = {
        "key_downloaded_at": "2026-05-11T12:00:00+00:00",
        "last_run": {"status": "success", "filename": "x.age"},
    }
    state.write(payload)
    assert state.read() == payload


def test_write_is_atomic(backup_dir):
    state.write({"key_downloaded_at": None, "last_run": {"status": "success"}})
    assert (backup_dir / ".state.json").exists()
    # The temp file used for the swap should not linger.
    leftovers = list(backup_dir.glob(".state.json.tmp*"))
    assert leftovers == []


def test_read_recovers_from_malformed_file(backup_dir):
    (backup_dir / ".state.json").write_text("{not valid json")
    s = state.read()
    assert s == {"key_downloaded_at": None, "last_run": None}


def test_mark_key_downloaded_sets_timestamp(backup_dir):
    state.mark_key_downloaded()
    s = state.read()
    assert s["key_downloaded_at"] is not None
    # Confirm it's a parseable ISO-8601 datetime.
    datetime.fromisoformat(s["key_downloaded_at"])


def test_mark_key_downloaded_preserves_last_run(backup_dir):
    state.write(
        {"key_downloaded_at": None, "last_run": {"status": "success", "filename": "x.age"}}
    )
    state.mark_key_downloaded()
    s = state.read()
    assert s["last_run"] == {"status": "success", "filename": "x.age"}


def test_write_last_run(backup_dir):
    now = datetime.now(UTC).isoformat()
    state.write_last_run({"status": "running", "started_at": now})
    s = state.read()
    assert s["last_run"] == {"status": "running", "started_at": now}
    assert s["key_downloaded_at"] is None


def test_write_last_run_preserves_key_downloaded_at(backup_dir):
    state.mark_key_downloaded()
    before = state.read()["key_downloaded_at"]
    state.write_last_run({"status": "success", "filename": "x.age"})
    after = state.read()
    assert after["key_downloaded_at"] == before
    assert after["last_run"] == {"status": "success", "filename": "x.age"}

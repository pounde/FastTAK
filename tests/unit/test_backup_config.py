"""Tests for monitor/app/backup/config.py."""

from pathlib import Path

import pytest
from app.backup import config as cfg


def test_backup_dir_default(monkeypatch):
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    # Inside the container path is always /backups; we test the resolution helper.
    assert cfg.backup_dir() == Path("/backups")


def test_backup_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    assert cfg.backup_dir() == tmp_path


def test_retention_keep_default(monkeypatch):
    monkeypatch.delenv("BACKUP_RETENTION_KEEP", raising=False)
    assert cfg.retention_keep() == 14


def test_retention_keep_override(monkeypatch):
    monkeypatch.setenv("BACKUP_RETENTION_KEEP", "5")
    assert cfg.retention_keep() == 5


def test_retention_keep_rejects_non_positive(monkeypatch):
    monkeypatch.setenv("BACKUP_RETENTION_KEEP", "0")
    with pytest.raises(ValueError):
        cfg.retention_keep()


def test_retention_keep_rejects_non_int(monkeypatch):
    monkeypatch.setenv("BACKUP_RETENTION_KEEP", "many")
    with pytest.raises(ValueError):
        cfg.retention_keep()


def test_version_and_commit(monkeypatch):
    monkeypatch.setenv("FASTTAK_VERSION", "v9.9.9")
    monkeypatch.setenv("FASTTAK_COMMIT", "deadbee")
    assert cfg.fasttak_version() == "v9.9.9"
    assert cfg.fasttak_commit() == "deadbee"


def test_version_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("FASTTAK_VERSION", raising=False)
    monkeypatch.delenv("FASTTAK_COMMIT", raising=False)
    assert cfg.fasttak_version() == "dev"
    assert cfg.fasttak_commit() == "unknown"


def test_admin_group_default(monkeypatch):
    monkeypatch.delenv("BACKUP_ADMIN_GROUP", raising=False)
    assert cfg.admin_group_default() == "monitor_admin"


def test_ensure_backup_dir_creates_with_700(monkeypatch, tmp_path):
    target = tmp_path / "backups"
    monkeypatch.setenv("BACKUP_DIR", str(target))
    cfg.ensure_backup_dir()
    assert target.exists()
    # On POSIX, mode should be 0o700 for the directory we created.
    mode = target.stat().st_mode & 0o777
    assert mode == 0o700

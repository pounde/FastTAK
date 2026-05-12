"""Dashboard-side tests for the backups page (the API router has its own file)."""

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("BACKUP_ADMIN_GROUP", "monitor_admin")
    return tmp_path


def test_dashboard_backups_403_for_non_admin(backup_dir):
    with TestClient(app) as client:
        r = client.get(
            "/dashboard/backups",
            headers={"Remote-User": "bob", "Remote-Groups": "tak_alpha"},
        )
        assert r.status_code == 403


def test_dashboard_backups_200_for_admin(backup_dir):
    with TestClient(app) as client:
        r = client.get(
            "/dashboard/backups",
            headers={"Remote-User": "alice", "Remote-Groups": "monitor_admin"},
        )
        assert r.status_code == 200
        assert b"Backups" in r.content


def test_dashboard_run_shows_already_running_when_lock_held(backup_dir, monkeypatch):
    from app.backup.exceptions import BackupAlreadyRunning

    def boom():
        raise BackupAlreadyRunning("a backup is already in progress")

    monkeypatch.setattr("app.dashboard.routes.run_backup_preflight", boom)
    with TestClient(app) as client:
        r = client.post(
            "/dashboard/backups/run",
            headers={"Remote-User": "alice", "Remote-Groups": "monitor_admin"},
        )
        assert r.status_code == 200
        assert b"already in progress" in r.content


def test_dashboard_run_no_banner_on_success(backup_dir, monkeypatch):
    # Preflight succeeds → no "already in progress" banner.
    monkeypatch.setattr("app.dashboard.routes.run_backup_preflight", lambda: None)
    monkeypatch.setattr("app.dashboard.routes.run_backup", lambda **_: None)
    with TestClient(app) as client:
        r = client.post(
            "/dashboard/backups/run",
            headers={"Remote-User": "alice", "Remote-Groups": "monitor_admin"},
        )
        assert r.status_code == 200
        assert b"already in progress" not in r.content

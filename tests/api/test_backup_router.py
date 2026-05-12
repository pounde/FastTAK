"""Tests for monitor/app/api/backup/router.py."""

from unittest.mock import MagicMock

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def admin_headers():
    return {"Remote-User": "alice", "Remote-Groups": "monitor_admin"}


@pytest.fixture
def nonadmin_headers():
    return {"Remote-User": "bob", "Remote-Groups": "tak_alpha"}


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("BACKUP_ADMIN_GROUP", "monitor_admin")
    return tmp_path


def test_list_requires_admin_group(backup_dir, nonadmin_headers):
    with TestClient(app) as client:
        r = client.get("/api/backup/", headers=nonadmin_headers)
        assert r.status_code == 403


def test_list_returns_state_and_backups(backup_dir, admin_headers):
    (backup_dir / "fasttak-backup-20260101T000000Z-test.age").write_bytes(b"abc")
    with TestClient(app) as client:
        r = client.get("/api/backup/", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        assert "state" in body
        assert any(
            b["filename"] == "fasttak-backup-20260101T000000Z-test.age" for b in body["backups"]
        )


def test_status_endpoint(backup_dir, admin_headers):
    with TestClient(app) as client:
        r = client.get("/api/backup/status", headers=admin_headers)
        assert r.status_code == 200
        assert r.json()["last_run"] is None


def test_run_returns_202_and_kicks_runner(backup_dir, admin_headers, monkeypatch):
    fake = MagicMock(
        return_value=MagicMock(filename="fasttak-backup-20260101T000000Z-test.age", size_bytes=1)
    )
    monkeypatch.setattr("app.api.backup.router.run_backup", fake)
    with TestClient(app) as client:
        r = client.post("/api/backup/run", headers=admin_headers)
        assert r.status_code == 202
        assert "job_id" in r.json()


def test_run_returns_409_when_lock_held(backup_dir, admin_headers, monkeypatch):
    from app.backup.exceptions import BackupAlreadyRunning

    def boom():
        raise BackupAlreadyRunning("a backup is already in progress")

    # Preflight is what surfaces 409 synchronously.
    monkeypatch.setattr("app.api.backup.router.run_backup_preflight", boom)
    with TestClient(app) as client:
        r = client.post("/api/backup/run", headers=admin_headers)
        assert r.status_code == 409


def test_download_returns_404_for_missing(backup_dir, admin_headers):
    with TestClient(app) as client:
        r = client.get(
            "/api/backup/files/fasttak-backup-20260101T000000Z-nope.age",
            headers=admin_headers,
        )
        assert r.status_code == 404


def test_download_rejects_path_traversal(backup_dir, admin_headers):
    with TestClient(app) as client:
        r = client.get("/api/backup/files/..%2Fetc%2Fpasswd", headers=admin_headers)
        assert r.status_code == 400


def test_download_rejects_consecutive_dots_in_version(backup_dir, admin_headers):
    with TestClient(app) as client:
        r = client.get(
            "/api/backup/files/fasttak-backup-20260101T000000Z-..age",
            headers=admin_headers,
        )
        assert r.status_code == 400


def test_download_rejects_leading_dot_in_version(backup_dir, admin_headers):
    with TestClient(app) as client:
        r = client.get(
            "/api/backup/files/fasttak-backup-20260101T000000Z-.bad.age",
            headers=admin_headers,
        )
        assert r.status_code == 400


def test_download_accepts_typical_release_version(backup_dir, admin_headers):
    # Real-world filename shape — should pass the regex (404 because file
    # doesn't exist, not 400 because of the regex).
    (backup_dir / "fasttak-backup-20260101T000000Z-v0.27.0-rc1.age").write_bytes(b"ok")
    with TestClient(app) as client:
        r = client.get(
            "/api/backup/files/fasttak-backup-20260101T000000Z-v0.27.0-rc1.age",
            headers=admin_headers,
        )
        assert r.status_code == 200


def test_download_streams_file(backup_dir, admin_headers):
    (backup_dir / "fasttak-backup-20260101T000000Z-test.age").write_bytes(b"hello")
    with TestClient(app) as client:
        r = client.get(
            "/api/backup/files/fasttak-backup-20260101T000000Z-test.age", headers=admin_headers
        )
        assert r.status_code == 200
        assert r.content == b"hello"
        assert "attachment" in r.headers["content-disposition"]


def test_key_returns_404_when_absent(backup_dir, admin_headers):
    with TestClient(app) as client:
        r = client.get("/api/backup/key", headers=admin_headers)
        assert r.status_code == 404


def test_key_marks_downloaded_and_audits(backup_dir, admin_headers, monkeypatch):
    (backup_dir / ".age-identity").write_text(
        "# created: 2026-05-11T12:00:00Z\n# public key: age1abc\nAGE-SECRET-KEY-1XYZ\n"
    )
    record = MagicMock()
    monkeypatch.setattr("app.api.backup.router.record_event", record)
    with TestClient(app) as client:
        r = client.get("/api/backup/key", headers=admin_headers)
        assert r.status_code == 200
        assert b"AGE-SECRET-KEY-1XYZ" in r.content
        actions = [c.kwargs.get("action") or c.args[2] for c in record.call_args_list]
        assert "backup.key_downloaded" in actions

    # State should now reflect the download.
    from app.backup import state

    assert state.read()["key_downloaded_at"] is not None


def test_delete_removes_file_and_audits(backup_dir, admin_headers, monkeypatch):
    target = backup_dir / "fasttak-backup-20260101T000000Z-test.age"
    target.write_bytes(b"hi")
    record = MagicMock()
    monkeypatch.setattr("app.api.backup.router.record_event", record)
    with TestClient(app) as client:
        r = client.delete(f"/api/backup/files/{target.name}", headers=admin_headers)
        assert r.status_code == 204
    assert not target.exists()
    actions = [c.kwargs.get("action") or c.args[2] for c in record.call_args_list]
    assert "backup.deleted" in actions

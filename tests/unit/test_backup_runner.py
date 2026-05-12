"""Tests for monitor/app/backup/runner.py.

We mock the heavy lifters (pg_dump, tar, age) and assert the orchestration
contract: lock ordering, state transitions, error cleanup, audit events.
The integration test (tests-integration/test_backup_restore.py) exercises
the real subprocess pipeline end-to-end.
"""

from unittest.mock import MagicMock

import pytest
from app.backup import exceptions, runner, state


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("FASTTAK_VERSION", "v9.9.9")
    monkeypatch.setenv("FASTTAK_COMMIT", "deadbee")
    return tmp_path


@pytest.fixture
def _stub_workers(monkeypatch):
    """Replace every subprocess-driven worker with a no-op stub.

    `_stream_encrypted_archive` touches the partial file so the runner's
    `os.replace(partial, final)` succeeds without invoking real tar/age.
    """

    def _fake_stream(staging, out, recipient):
        out.write_bytes(b"")

    monkeypatch.setattr(runner, "_dump_postgres", MagicMock())
    monkeypatch.setattr(runner, "_tar_certs", MagicMock())
    monkeypatch.setattr(runner, "_tar_tak_config", MagicMock())
    monkeypatch.setattr(runner, "_tar_nodered_data", MagicMock())
    monkeypatch.setattr(runner, "_copy_env", MagicMock())
    monkeypatch.setattr(runner, "_stream_encrypted_archive", MagicMock(side_effect=_fake_stream))
    monkeypatch.setattr(runner, "_collect_postgres_versions", MagicMock(return_value={}))


def test_run_writes_success_state(backup_dir, _stub_workers):
    result = runner.run(actor="testuser", client_ip="1.2.3.4")
    s = state.read()
    assert s["last_run"]["status"] == "success"
    assert s["last_run"]["filename"].startswith("fasttak-backup-")
    assert s["last_run"]["filename"].endswith("-v9.9.9.age")
    assert result.filename == s["last_run"]["filename"]


def test_run_writes_failed_state_on_error(backup_dir, _stub_workers, monkeypatch):
    monkeypatch.setattr(runner, "_dump_postgres", MagicMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        runner.run(actor="testuser", client_ip="1.2.3.4")
    s = state.read()
    assert s["last_run"]["status"] == "failed"
    # Only the exception type goes into persisted state (pg_dump / psycopg
    # stderr can echo DSN fragments and paths).
    assert s["last_run"]["error"] == "RuntimeError"
    assert "boom" not in s["last_run"]["error"]


def test_run_records_audit_events_on_success(backup_dir, _stub_workers, monkeypatch):
    record = MagicMock()
    monkeypatch.setattr(runner, "record_event", record)
    runner.run(actor="alice", client_ip="10.0.0.1")
    actions = [call.kwargs.get("action") or call.args[2] for call in record.call_args_list]
    assert "backup.started" in actions
    assert "backup.completed" in actions


def test_run_records_audit_event_on_failure(backup_dir, _stub_workers, monkeypatch):
    monkeypatch.setattr(runner, "_dump_postgres", MagicMock(side_effect=RuntimeError("boom")))
    record = MagicMock()
    monkeypatch.setattr(runner, "record_event", record)
    with pytest.raises(RuntimeError):
        runner.run(actor="alice", client_ip="10.0.0.1")
    actions = [call.kwargs.get("action") or call.args[2] for call in record.call_args_list]
    assert "backup.started" in actions
    assert "backup.failed" in actions


def test_run_rejects_when_lock_held(backup_dir, _stub_workers):
    # Acquire the lock manually.
    lock_path = backup_dir / ".backup.lock"
    fd = open(lock_path, "w")
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(exceptions.BackupAlreadyRunning):
            runner.run(actor="x", client_ip="1.1.1.1")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def test_run_runs_retention_after_success(backup_dir, _stub_workers, monkeypatch):
    prune = MagicMock(return_value=["old.age"])
    monkeypatch.setattr(runner, "prune", prune)
    runner.run(actor="x", client_ip="1.1.1.1")
    prune.assert_called_once_with(keep=14)


def test_run_emits_backup_pruned_per_deleted_file(backup_dir, _stub_workers, monkeypatch):
    monkeypatch.setattr(runner, "prune", MagicMock(return_value=["a.age", "b.age"]))
    record = MagicMock()
    monkeypatch.setattr(runner, "record_event", record)
    runner.run(actor="x", client_ip="1.1.1.1")
    pruned_actions = [
        c
        for c in record.call_args_list
        if (c.kwargs.get("action") or c.args[2]) == "backup.pruned"
    ]
    assert len(pruned_actions) == 2
    targets = {c.kwargs.get("target_id") for c in pruned_actions}
    assert targets == {"a.age", "b.age"}


def test_run_skips_retention_after_failure(backup_dir, _stub_workers, monkeypatch):
    prune = MagicMock()
    monkeypatch.setattr(runner, "prune", prune)
    monkeypatch.setattr(runner, "_dump_postgres", MagicMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        runner.run(actor="x", client_ip="1.1.1.1")
    prune.assert_not_called()


def test_tar_tak_config_raises_when_config_files_missing(tmp_path):
    src = tmp_path / "tak-src"
    src.mkdir()
    out = tmp_path / "tak-config.tar"
    with pytest.raises(RuntimeError, match="required file"):
        runner._tar_tak_config(out, source_dir=str(src))
    assert not out.exists() or out.stat().st_size == 0


def test_tar_tak_config_raises_when_only_one_file_present(tmp_path):
    src = tmp_path / "tak-src"
    src.mkdir()
    (src / "CoreConfig.xml").write_text("<CoreConfig/>")
    # TAKIgniteConfig.xml missing — should still raise.
    out = tmp_path / "tak-config.tar"
    with pytest.raises(RuntimeError, match="TAKIgniteConfig"):
        runner._tar_tak_config(out, source_dir=str(src))


def test_run_writes_sha256_sidecar(backup_dir, _stub_workers):
    import hashlib

    result = runner.run(actor="alice", client_ip="1.2.3.4")
    archive = backup_dir / result.filename
    sidecar = backup_dir / f"{result.filename}.sha256"
    assert sidecar.exists(), "expected .sha256 sidecar next to the archive"
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    text = sidecar.read_text()
    # sha256sum -c format: "<hex>  <filename>\n"
    assert text == f"{expected}  {result.filename}\n"


def test_sha256_sidecar_not_written_on_failure(backup_dir, _stub_workers, monkeypatch):
    monkeypatch.setattr(runner, "_dump_postgres", MagicMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        runner.run(actor="x", client_ip="1.1.1.1")
    sidecars = list(backup_dir.glob("fasttak-backup-*.sha256"))
    assert sidecars == []


def test_tar_tak_config_archives_both_files(tmp_path):
    import tarfile

    src = tmp_path / "tak-src"
    src.mkdir()
    (src / "CoreConfig.xml").write_text("<CoreConfig/>")
    (src / "TAKIgniteConfig.xml").write_text("<TAKIgniteConfig/>")
    out = tmp_path / "tak-config.tar"
    runner._tar_tak_config(out, source_dir=str(src))
    with tarfile.open(out) as tf:
        # Filter out macOS AppleDouble companion entries (._<name>) that
        # macOS tar includes — they're harmless on Linux extract but pollute
        # an exact-equality assertion.
        names = sorted(n for n in tf.getnames() if not n.startswith("._"))
    assert names == ["CoreConfig.xml", "TAKIgniteConfig.xml"]

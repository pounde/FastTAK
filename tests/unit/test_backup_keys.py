"""Tests for monitor/app/backup/keys.py."""

import shutil
import subprocess

import pytest
from app.backup import keys


@pytest.fixture
def backup_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _require_age():
    """Skip if `age-keygen` isn't installed (dev box missing age)."""
    if shutil.which("age-keygen") is None:
        pytest.skip("age-keygen not on PATH")


def test_read_identity_returns_none_when_absent(backup_dir):
    assert keys.read_identity() is None


def test_load_or_create_generates_when_missing(backup_dir):
    identity, recipient = keys.load_or_create()
    assert identity.startswith("AGE-SECRET-KEY-")
    assert recipient.startswith("age1")
    path = backup_dir / ".age-identity"
    assert path.exists()
    # 0600 file mode (owner read/write only).
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_load_or_create_is_idempotent(backup_dir):
    a_identity, a_recipient = keys.load_or_create()
    b_identity, b_recipient = keys.load_or_create()
    assert a_identity == b_identity
    assert a_recipient == b_recipient


def test_read_identity_returns_bytes_of_file(backup_dir):
    identity, _ = keys.load_or_create()
    raw = keys.read_identity()
    assert raw is not None
    # The file contains the identity line plus age-keygen's header comments.
    assert identity.encode() in raw


def test_recipient_for_existing_identity(backup_dir):
    identity, expected_recipient = keys.load_or_create()
    # Independently compute the recipient via age-keygen -y.
    proc = subprocess.run(
        ["age-keygen", "-y"], input=identity, text=True, capture_output=True, check=True
    )
    derived = proc.stdout.strip()
    assert derived == expected_recipient

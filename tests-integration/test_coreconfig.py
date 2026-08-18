"""CoreConfig.xml is patched correctly after init-config runs.

TAK 5.8 reflowed CoreConfig.example.xml — indentation changed on most lines
and new attributes appeared. init-config/start.sh patches it with sed, so a
reformat can silently stop a pattern from matching.

These assertions previously lived in start.sh's CHECKS section, where nothing
in CI ran them (see #96). They belong here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _state_dir() -> Path:
    matches = list(Path("/tmp").glob("fastak-test-*/.test-state"))
    assert matches, "no /tmp/fastak-test-*/.test-state — is `just test-up` running?"
    return max(matches, key=lambda p: p.stat().st_mtime).parent


def _tak_host_path() -> Path:
    for line in (_state_dir() / ".test-state").read_text().splitlines():
        if line.strip().startswith("TAK_HOST_PATH="):
            return Path(line.split("=", 1)[1].strip().strip('"').strip("'"))
    raise AssertionError("TAK_HOST_PATH not found in .test-state")


@pytest.fixture(scope="module")
def coreconfig() -> str:
    path = _tak_host_path() / "CoreConfig.xml"
    assert path.is_file(), f"{path} does not exist — did init-config run?"
    return path.read_text()


def test_db_connection_points_at_tak_database(coreconfig):
    assert "tak-database:5432" in coreconfig


def test_db_password_is_populated(coreconfig):
    """A sed that stopped matching would leave password="" and the stack
    would come up unable to reach its own database."""
    import re

    match = re.search(r'<connection[^>]*password="([^"]*)"', coreconfig)
    assert match, "no <connection> element with a password attribute"
    assert match.group(1) != "", "connection password is empty"


def test_admin_ui_enabled(coreconfig):
    assert 'enableAdminUI="true"' in coreconfig


def test_certificate_signing_configured(coreconfig):
    assert '<certificateSigning CA="TAKServer">' in coreconfig


def test_ldap_service_account_configured(coreconfig):
    assert "adm_ldapservice" in coreconfig


def test_role_admin_group(coreconfig):
    assert 'adminGroup="ROLE_ADMIN"' in coreconfig


def test_archive_mode_is_off_on_the_running_database():
    """WAL archiving must be disabled — the vendor's archive_command copies to
    a directory no TAK bundle creates, so Postgres would retain every segment
    until the disk fills."""
    project = None
    for line in (_state_dir() / ".test-state").read_text().splitlines():
        if line.strip().startswith("PROJECT="):
            project = line.split("=", 1)[1].strip().strip('"').strip("'")
    assert project, "PROJECT not found in .test-state"

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "exec",
            "-T",
            "tak-database",
            "sh",
            "-c",
            'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot '
            '-tAc "SHOW archive_mode;"',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "off"


@pytest.mark.parametrize(
    "setting,expected",
    [
        ("autovacuum_vacuum_scale_factor", "0.05"),
        ("autovacuum_vacuum_cost_limit", "1000"),
        ("maintenance_work_mem", "256MB"),
    ],
)
def test_fasttak_tuning_took_effect(setting, expected):
    project = None
    for line in (_state_dir() / ".test-state").read_text().splitlines():
        if line.strip().startswith("PROJECT="):
            project = line.split("=", 1)[1].strip().strip('"').strip("'")
    assert project, "PROJECT not found in .test-state"

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "exec",
            "-T",
            "tak-database",
            "sh",
            "-c",
            f'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot '
            f'-tAc "SHOW {setting};"',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == expected

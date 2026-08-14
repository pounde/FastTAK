# tests/test_tak_database_tuning.py
"""tak-database/start.sh appends FastTAK tuning to the vendor's postgresql.conf.

TAK 5.6 exposed tuning as pg_ctl -o flags and FastTAK sed-patched them in.
TAK 5.8 has no such flags, so that sed silently no-ops. These tests cover the
append: it must be idempotent across restarts (the file is bind-mounted from
the host and survives), it must win over the vendor's values, and a missing
vendor file must fail loudly rather than skip tuning.

Only the config-injection half is unit-tested. The post-startup SHOW
verification needs a live server and is covered in tests-integration.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
START = REPO / "tak-database" / "start.sh"

VENDOR_CONF = """\
listen_addresses = '*'
port = 5432
max_connections = 2100
shared_buffers = 2560MB
archive_mode = on
archive_command = 'cp "%p" "/var/lib/postgresql/archivedir/%f"'
"""


def _fake_tak_tree(tmp_path: Path) -> Path:
    """Minimal /opt/tak with the files start.sh touches."""
    tak = tmp_path / "opt" / "tak"
    (tak / "db-utils").mkdir(parents=True)
    (tak / "db-utils" / "postgresql.conf").write_text(VENDOR_CONF)
    (tak / "db-utils" / "configureInDocker.sh").write_text("#!/bin/sh\nexit 0\n")
    (tak / "db-utils" / "configureInDocker.sh").chmod(0o755)
    (tak / "CoreConfig.xml").write_text(
        '<Configuration><connection url="x" password="OLD"/></Configuration>\n'
    )
    return tak


def _inject_only(tak: Path, **env) -> subprocess.CompletedProcess:
    """Run start.sh with FASTAK_INJECT_ONLY=1 — apply config, then exit."""
    return subprocess.run(
        ["/bin/bash", str(START)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "FASTAK_INJECT_ONLY": "1",
            "FASTAK_TAK_DIR": str(tak),
            "TAK_DB_PASSWORD": "test-pw",
            **env,
        },
    )


def _conf(tak: Path) -> str:
    return (tak / "db-utils" / "postgresql.conf").read_text()


def test_defaults_are_appended(tmp_path):
    tak = _fake_tak_tree(tmp_path)
    result = _inject_only(tak)
    assert result.returncode == 0, result.stderr
    conf = _conf(tak)
    assert "autovacuum_vacuum_scale_factor = 0.05" in conf
    assert "autovacuum_vacuum_cost_limit = 1000" in conf
    assert "maintenance_work_mem = '256MB'" in conf


def test_archive_mode_is_disabled(tmp_path):
    """archive_command copies to a directory no TAK bundle creates, so every
    archive fails and Postgres never recycles WAL. FastTAK does logical
    backups (pg_dump) and consumes no archives."""
    tak = _fake_tak_tree(tmp_path)
    assert _inject_only(tak).returncode == 0
    conf = _conf(tak)
    assert "archive_mode = off" in conf
    # The vendor's `on` must still be present but overridden by position.
    assert conf.index("archive_mode = on") < conf.index("archive_mode = off")


def test_env_overrides_are_honoured(tmp_path):
    tak = _fake_tak_tree(tmp_path)
    result = _inject_only(
        tak,
        PG_AUTOVACUUM_SCALE_FACTOR="0.2",
        PG_AUTOVACUUM_COST_LIMIT="200",
        PG_MAINTENANCE_WORK_MEM="64MB",
    )
    assert result.returncode == 0, result.stderr
    conf = _conf(tak)
    assert "autovacuum_vacuum_scale_factor = 0.2" in conf
    assert "autovacuum_vacuum_cost_limit = 200" in conf
    assert "maintenance_work_mem = '64MB'" in conf


def test_append_is_idempotent(tmp_path):
    """/opt/tak is bind-mounted from the host, so the file survives restarts.
    Three starts must not produce three blocks."""
    tak = _fake_tak_tree(tmp_path)
    for _ in range(3):
        assert _inject_only(tak).returncode == 0
    assert _conf(tak).count("FASTTAK-TUNING-BEGIN") == 1
    assert _conf(tak).count("autovacuum_vacuum_cost_limit = 1000") == 1


def test_changed_values_replace_the_previous_block(tmp_path):
    tak = _fake_tak_tree(tmp_path)
    assert _inject_only(tak).returncode == 0
    assert _inject_only(tak, PG_AUTOVACUUM_COST_LIMIT="3000").returncode == 0
    conf = _conf(tak)
    assert "autovacuum_vacuum_cost_limit = 3000" in conf
    assert "autovacuum_vacuum_cost_limit = 1000" not in conf


def test_missing_vendor_conf_fails_loudly(tmp_path):
    """The failure mode this replaces was silent. A missing vendor file means
    the release layout changed and tuning would be dropped — so it must abort."""
    tak = _fake_tak_tree(tmp_path)
    (tak / "db-utils" / "postgresql.conf").unlink()
    result = _inject_only(tak)
    assert result.returncode != 0
    assert "postgresql.conf" in result.stderr


def test_coreconfig_password_is_still_patched(tmp_path):
    """Pre-existing behaviour that must survive the rewrite."""
    tak = _fake_tak_tree(tmp_path)
    assert _inject_only(tak).returncode == 0
    assert 'password="test-pw"' in (tak / "CoreConfig.xml").read_text()


def test_unverifiable_settings_are_fatal(tmp_path):
    """The failure this replaces was silent, so the replacement must not be.

    Runs the full path (no FASTAK_INJECT_ONLY) against a vendor entrypoint that
    starts no server, so every SHOW fails. FASTAK_VERIFY_TIMEOUT keeps it short.
    """
    tak = _fake_tak_tree(tmp_path)
    (tak / "db-utils" / "configureInDocker.sh").write_text("#!/bin/sh\nsleep 30\n")
    (tak / "db-utils" / "configureInDocker.sh").chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(START)],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "FASTAK_TAK_DIR": str(tak),
            "FASTAK_VERIFY_TIMEOUT": "5",
            "FASTAK_GUARD": "/nonexistent",
            "TAK_DB_PASSWORD": "test-pw",
        },
    )
    assert result.returncode == 1
    assert "did not take effect" in result.stderr

# tests/test_tak_database_tuning.py
"""tak-database/start.sh applies FastTAK tuning to PostgreSQL's config files.

TAK 5.6 exposed tuning as pg_ctl -o flags and FastTAK sed-patched them in.
TAK 5.8 has no such flags, so that sed silently no-ops. 5.8 moved tuning into
db-utils/postgresql.conf — but takserver-setup-db.sh only installs that file on
the first boot of a fresh database (on any later boot its `read -p "Type
'erase'"` guard hits EOF and the script exits before the copy). So start.sh
writes to both the vendor file and the live $PGDATA/postgresql.conf, and these
tests cover both targets: idempotent across restarts, last-wins over the
vendor's values, and loud on anything missing.

The second half exercises the post-startup SHOW verification with a stub `psql`
on PATH. Without one, every SHOW returns empty and the comparison logic has no
coverage in either direction — a wrong value and a missing binary look the same.
"""

import os
import shlex
import stat
import subprocess
import time
from pathlib import Path

import pytest

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

# What initdb leaves behind in PGDATA at image build time.
LIVE_CONF = """\
max_connections = 100
shared_buffers = 128MB
archive_mode = on
"""

CORRECT_SETTINGS = {
    "autovacuum_vacuum_scale_factor": "0.05",
    "autovacuum_vacuum_cost_limit": "1000",
    "maintenance_work_mem": "256MB",
    "archive_mode": "off",
}


def _pgdata(tak: Path) -> Path:
    """The live cluster directory — a sibling of the fake /opt tree."""
    return tak.parents[1] / "pgdata"


def _fake_tak_tree(tmp_path: Path) -> Path:
    """Minimal /opt/tak plus the PGDATA the image ships with."""
    tak = tmp_path / "opt" / "tak"
    (tak / "db-utils").mkdir(parents=True)
    (tak / "db-utils" / "postgresql.conf").write_text(VENDOR_CONF)
    (tak / "db-utils" / "configureInDocker.sh").write_text("#!/bin/sh\nexit 0\n")
    (tak / "db-utils" / "configureInDocker.sh").chmod(0o755)
    (tak / "CoreConfig.xml").write_text(
        '<Configuration><connection url="x" password="OLD"/></Configuration>\n'
    )
    pgdata = _pgdata(tak)
    pgdata.mkdir(parents=True)
    (pgdata / "postgresql.conf").write_text(LIVE_CONF)
    # What initdb also leaves behind — the persistence guard's PGDATA check
    # treats its presence as proof PGDATA points at a real cluster directory.
    (pgdata / "PG_VERSION").write_text("15\n")
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
            "PGDATA": str(_pgdata(tak)),
            "TAK_DB_PASSWORD": "test-pw",
            **env,
        },
    )


def _conf(tak: Path) -> str:
    return (tak / "db-utils" / "postgresql.conf").read_text()


def _live(tak: Path) -> str:
    return (_pgdata(tak) / "postgresql.conf").read_text()


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
    assert _live(tak).count("FASTTAK-TUNING-BEGIN") == 1


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_coreconfig_fails_loudly(tmp_path):
    """The failure this replaces was silent: `sed ... > tmp && mv tmp target`
    short-circuits when the temp file cannot be created. The old password
    stays in place, nothing is printed, the script exits 0 into the vendor
    start, and a stray .fastak.tmp is left in the bind-mounted tree. This
    must be fatal and must name the file."""
    tak = _fake_tak_tree(tmp_path)
    tak.chmod(0o500)
    try:
        result = _inject_only(tak)
    finally:
        tak.chmod(0o755)
    assert result.returncode != 0, result.stdout
    assert "CoreConfig.xml" in result.stderr
    assert 'password="OLD"' in (tak / "CoreConfig.xml").read_text()
    assert list(tak.rglob("*.fastak.tmp")) == []


# ── The live configuration ($PGDATA/postgresql.conf) ─────────────────────
#
# takserver-setup-db.sh copies db-utils/postgresql.conf over the live config
# only when the `cot` database does not yet exist. On every later boot it hits
# an interactive `read` guard, gets EOF, and exits 1 long before the copy — so
# writing only to the vendor file means a changed .env value never reaches the
# running server.


def test_tuning_is_written_to_the_live_config(tmp_path):
    tak = _fake_tak_tree(tmp_path)
    assert _inject_only(tak).returncode == 0
    live = _live(tak)
    assert "autovacuum_vacuum_scale_factor = 0.05" in live
    assert "autovacuum_vacuum_cost_limit = 1000" in live
    assert "maintenance_work_mem = '256MB'" in live
    assert "archive_mode = off" in live
    # initdb's own value must still be there, overridden by position.
    assert live.index("archive_mode = on") < live.index("archive_mode = off")


def test_changed_value_reaches_the_live_config_on_a_later_boot(tmp_path):
    """The operator edits PG_AUTOVACUUM_COST_LIMIT on an existing volume and
    restarts. The vendor will not reinstall its own file, so the new value has
    to be written to the live config directly or it is never applied."""
    tak = _fake_tak_tree(tmp_path)
    assert _inject_only(tak).returncode == 0
    assert _inject_only(tak, PG_AUTOVACUUM_COST_LIMIT="3000").returncode == 0
    live = _live(tak)
    assert "autovacuum_vacuum_cost_limit = 3000" in live
    assert "autovacuum_vacuum_cost_limit = 1000" not in live
    assert live.count("FASTTAK-TUNING-BEGIN") == 1


def test_missing_pgdata_env_is_fatal(tmp_path):
    """Without PGDATA the live-config write cannot happen, and a changed value
    would silently not apply — the failure mode this whole path removes."""
    tak = _fake_tak_tree(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "PGDATA"} | {
        "FASTAK_INJECT_ONLY": "1",
        "FASTAK_TAK_DIR": str(tak),
        "TAK_DB_PASSWORD": "test-pw",
    }
    result = subprocess.run(["/bin/bash", str(START)], capture_output=True, text=True, env=env)
    assert result.returncode == 1
    assert "PGDATA is not set" in result.stderr


def test_missing_live_config_is_fatal(tmp_path):
    """The image runs initdb at build time and Docker seeds an empty named
    volume from it, so the file is expected to exist. If it does not, an
    assumption has broken and continuing would be the silent failure again."""
    tak = _fake_tak_tree(tmp_path)
    (_pgdata(tak) / "postgresql.conf").unlink()
    result = _inject_only(tak)
    assert result.returncode == 1
    assert str(_pgdata(tak) / "postgresql.conf") in result.stderr


# ── Failure handling in the rewrite itself ───────────────────────────────


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_conf_fails_loudly(tmp_path):
    """An unchecked `awk > tmp && mv` short-circuits when the temp file cannot
    be created: the old block survives, the append adds a second one, and the
    script still reports success. Two blocks with conflicting values is exactly
    the ambiguity this rewrite exists to avoid."""
    tak = _fake_tak_tree(tmp_path)
    assert _inject_only(tak).returncode == 0
    db_utils = tak / "db-utils"
    db_utils.chmod(0o555)
    try:
        result = _inject_only(tak, PG_AUTOVACUUM_COST_LIMIT="3000")
    finally:
        db_utils.chmod(0o755)
    assert result.returncode != 0, result.stdout
    assert _conf(tak).count("FASTTAK-TUNING-BEGIN") == 1
    assert "Tuning applied" not in result.stdout


def test_no_temp_files_are_left_behind(tmp_path):
    tak = _fake_tak_tree(tmp_path)
    assert _inject_only(tak).returncode == 0
    assert list(tmp_path.rglob("*.fastak.tmp")) == []


def test_conf_mode_is_preserved(tmp_path):
    """`mv` stamps the temp file's mode onto the target. postgresql.conf is
    0600 in a real cluster and must not be widened to the umask."""
    tak = _fake_tak_tree(tmp_path)
    live = _pgdata(tak) / "postgresql.conf"
    live.chmod(0o600)
    assert _inject_only(tak).returncode == 0
    assert stat.S_IMODE(live.stat().st_mode) == 0o600


def test_content_after_the_block_survives(tmp_path):
    """Deleting from the start marker to EOF silently drops anything appended
    after the block. Delete between the markers instead."""
    tak = _fake_tak_tree(tmp_path)
    assert _inject_only(tak).returncode == 0
    live = _pgdata(tak) / "postgresql.conf"
    live.write_text(live.read_text() + "log_min_duration_statement = 500\n")
    assert _inject_only(tak).returncode == 0
    assert "log_min_duration_statement = 500" in _live(tak)
    assert _live(tak).count("FASTTAK-TUNING-BEGIN") == 1


@pytest.mark.parametrize("value", ["256MB'\narchive_mode = on", "256MB\nshared_buffers = 1MB"])
def test_values_that_could_inject_config_lines_are_rejected(tmp_path, value):
    tak = _fake_tak_tree(tmp_path)
    result = _inject_only(tak, PG_MAINTENANCE_WORK_MEM=value)
    assert result.returncode == 1
    assert "PG_MAINTENANCE_WORK_MEM" in result.stderr
    assert "FASTTAK-TUNING-BEGIN" not in _live(tak)


# ── Verification against a running server ────────────────────────────────
#
# There is no psql on the test host, and psql_show discards stderr, so a
# missing binary and a wrong setting both yield "". These stubs are what give
# the comparison coverage in either direction.


def _stub_psql(
    bin_dir: Path,
    values: dict,
    *,
    stderr: str = "",
    record_env_to: Path | None = None,
) -> None:
    """A fake psql on PATH mapping `SHOW <name>;` to a value."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    lines = ["#!/bin/bash"]
    if record_env_to is not None:
        lines.append(
            f'printf "%s\\n" "${{PGCONNECT_TIMEOUT:-unset}}" >> {shlex.quote(str(record_env_to))}'
        )
    if stderr:
        lines.append(f"printf '%s\\n' {shlex.quote(stderr)} >&2")
    lines.append('for a in "$@"; do q="$a"; done')
    lines.append('case "$q" in')
    for name, val in values.items():
        lines.append(f"  *{name}*) printf '%s\\n' {shlex.quote(val)} ;;")
    lines.append("  *) exit 1 ;;")
    lines.append("esac")
    psql = bin_dir / "psql"
    psql.write_text("\n".join(lines) + "\n")
    psql.chmod(0o755)


def _stub_guard(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    """A fake PGDATA guard that records the data directory it was handed."""
    calls = tmp_path / "guard-calls"
    guard = tmp_path / "guard.sh"
    guard.write_text(
        f'#!/bin/bash\nprintf "%s\\n" "$1" >> {shlex.quote(str(calls))}\nexit {exit_code}\n'
    )
    guard.chmod(0o755)
    return guard, calls


def _run_full(tak: Path, *, bin_dir: Path | None = None, timeout=120, **env):
    """Run start.sh through vendor startup and verification."""
    full_env = {
        **os.environ,
        "FASTAK_TAK_DIR": str(tak),
        "PGDATA": str(_pgdata(tak)),
        "FASTAK_VERIFY_TIMEOUT": "1",
        "FASTAK_GUARD": "/nonexistent",
        "TAK_DB_PASSWORD": "test-pw",
        **env,
    }
    if bin_dir is not None:
        full_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    return subprocess.run(
        ["/bin/bash", str(START)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
    )


def _vendor_runs_for(tak: Path, seconds: int) -> None:
    """A stand-in for configureInDocker.sh that stays alive.

    `exec` so the sleep *is* the vendor PID: a child sleep would survive the
    kill and hold the captured stdout pipe open for its full duration.
    """
    entry = tak / "db-utils" / "configureInDocker.sh"
    entry.write_text(f"#!/bin/sh\nexec sleep {seconds}\n")
    entry.chmod(0o755)


def test_unverifiable_settings_are_fatal(tmp_path):
    """The failure this replaces was silent, so the replacement must not be.

    No psql on PATH at all, so every SHOW fails. FASTAK_VERIFY_TIMEOUT keeps it
    short.
    """
    tak = _fake_tak_tree(tmp_path)
    _vendor_runs_for(tak, 30)
    result = _run_full(tak)
    assert result.returncode == 1
    assert "did not take effect" in result.stderr


def test_wrong_settings_report_what_the_server_returned(tmp_path):
    """A server answering with values other than FastTAK's must fail, and the
    diagnostic must name the values it actually got — an empty `got:` proves
    only that psql did not run."""
    tak = _fake_tak_tree(tmp_path)
    _vendor_runs_for(tak, 30)
    bin_dir = tmp_path / "stubbin"
    _stub_psql(
        bin_dir,
        {
            "autovacuum_vacuum_scale_factor": "0.2",
            "autovacuum_vacuum_cost_limit": "200",
            "maintenance_work_mem": "64MB",
            "archive_mode": "on",
        },
    )
    result = _run_full(tak, bin_dir=bin_dir)
    assert result.returncode == 1
    assert "did not take effect" in result.stderr
    got = result.stderr.split("got:", 1)[1]
    assert "autovacuum_vacuum_scale_factor=0.2" in got
    assert "autovacuum_vacuum_cost_limit=200" in got
    assert "maintenance_work_mem=64MB" in got
    assert "archive_mode=on" in got


def test_correct_settings_verify_and_run_the_guard(tmp_path):
    """The success path: a server reporting FastTAK's values must reach the
    'verified' line and then hand PGDATA to the persistence guard.

    The guard no longer asks the server for data_directory — that setting is
    restricted to roles with pg_read_all_settings, which martiuser is not, so
    SHOW silently returns nothing rather than erroring. PGDATA is what the
    guard is handed instead.
    """
    tak = _fake_tak_tree(tmp_path)
    _vendor_runs_for(tak, 2)
    bin_dir = tmp_path / "stubbin"
    _stub_psql(bin_dir, CORRECT_SETTINGS)
    guard, calls = _stub_guard(tmp_path)
    result = _run_full(tak, bin_dir=bin_dir, FASTAK_GUARD=str(guard))
    assert result.returncode == 0, result.stderr
    assert "Settings verified" in result.stdout
    assert calls.read_text().strip() == str(_pgdata(tak))
    assert "is on a mounted volume" in result.stdout


def test_guard_failure_is_fatal(tmp_path):
    tak = _fake_tak_tree(tmp_path)
    _vendor_runs_for(tak, 30)
    bin_dir = tmp_path / "stubbin"
    _stub_psql(bin_dir, CORRECT_SETTINGS)
    guard, _ = _stub_guard(tmp_path, exit_code=1)
    result = _run_full(tak, bin_dir=bin_dir, FASTAK_GUARD=str(guard))
    assert result.returncode == 1


def test_missing_pg_version_is_fatal(tmp_path):
    """PGDATA is trusted without a server round-trip, so before handing it to
    the guard the script confirms it looks like a real cluster directory. A
    PGDATA missing PG_VERSION must not reach 'is on a mounted volume' — a
    success claim for a check that never ran."""
    tak = _fake_tak_tree(tmp_path)
    (_pgdata(tak) / "PG_VERSION").unlink()
    _vendor_runs_for(tak, 30)
    bin_dir = tmp_path / "stubbin"
    _stub_psql(bin_dir, CORRECT_SETTINGS)
    guard, calls = _stub_guard(tmp_path)
    result = _run_full(tak, bin_dir=bin_dir, FASTAK_GUARD=str(guard))
    assert result.returncode == 1
    assert "PG_VERSION" in result.stderr
    assert "is on a mounted volume" not in result.stdout
    assert not calls.exists()


def test_vendor_exit_is_reported_as_a_vendor_failure(tmp_path):
    """If configureInDocker.sh dies the server will never come up. Burning the
    full timeout and then blaming FastTAK's tuning misdiagnoses it."""
    tak = _fake_tak_tree(tmp_path)  # vendor entrypoint exits 0 immediately
    started = time.monotonic()
    result = _run_full(tak, FASTAK_VERIFY_TIMEOUT="120", timeout=60)
    elapsed = time.monotonic() - started
    assert result.returncode == 1
    assert "TAK Server's own entrypoint exited" in result.stderr
    assert "did not take effect" not in result.stderr
    assert elapsed < 30, f"waited {elapsed:.0f}s for a dead vendor process"


def test_psql_failure_appears_in_the_diagnostic(tmp_path):
    """A missing binary, an auth failure and a wrong value all render as an
    empty `got:`. The stderr line is what tells them apart."""
    tak = _fake_tak_tree(tmp_path)
    _vendor_runs_for(tak, 30)
    bin_dir = tmp_path / "stubbin"
    _stub_psql(bin_dir, {}, stderr="psql: error: FATAL: password authentication failed")
    result = _run_full(tak, bin_dir=bin_dir)
    assert result.returncode == 1
    assert "psql stderr:" in result.stderr
    assert "password authentication failed" in result.stderr.split("psql stderr:", 1)[1]


def test_connect_timeout_is_set(tmp_path):
    """A wedged connection must not consume the verification deadline."""
    tak = _fake_tak_tree(tmp_path)
    _vendor_runs_for(tak, 2)
    bin_dir = tmp_path / "stubbin"
    seen = tmp_path / "connect-timeout"
    _stub_psql(bin_dir, CORRECT_SETTINGS, record_env_to=seen)
    result = _run_full(tak, bin_dir=bin_dir)
    assert result.returncode == 0, result.stderr
    assert set(seen.read_text().split()) == {"5"}

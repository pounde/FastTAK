"""app-db/start.sh fails closed when the PGDATA persistence guard is missing.

The guard is bind-mounted from the host (docker-compose.yml mounts
./tak-database/check-pgdata-persistent.sh into both database services), so a
mount that did not land or a stripped execute bit takes it out of the picture.
It used to warn and continue, which leaves the container reporting a normal
startup while the one check that catches a PGDATA moved off the volume never
ran — the defect class this guard exists for. tak-database/start.sh fails
closed on every neighbouring check; this mirrors that.

Everything here runs the real script against stubbed postgres binaries: no
image, no daemon.
"""

import os
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
START = REPO / "app-db" / "start.sh"
DATA_DIR = "/var/lib/postgresql/data"


def _stub_bin(tmp_path: Path) -> Path:
    """docker-entrypoint.sh, pg_isready, psql and pg_ctl, enough for start.sh
    to run.

    `exec sleep` so the sleep *is* the backgrounded PID the script waits on.
    """
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()

    # pg_ctl records its argv so a test can assert the fatal branches shut
    # PostgreSQL down instead of leaving Docker to kill it.
    pg_ctl_log = shlex.quote(str(tmp_path / "pg_ctl-calls"))
    pg_ctl = bin_dir / "pg_ctl"
    pg_ctl.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {pg_ctl_log}\nexit 0\n')
    pg_ctl.chmod(0o755)

    entry = bin_dir / "docker-entrypoint.sh"
    entry.write_text("#!/bin/sh\nexec sleep 1\n")

    ready = bin_dir / "pg_isready"
    ready.write_text("#!/bin/sh\nexit 0\n")

    # Only `SHOW data_directory;` needs a real answer; every other call is a
    # DDL statement whose output the script ignores.
    psql = bin_dir / "psql"
    psql.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do q="$a"; done\n'
        'case "$q" in\n'
        f"  *data_directory*) printf '%s\\n' {shlex.quote(DATA_DIR)} ;;\n"
        "esac\n"
        "exit 0\n"
    )

    for f in (entry, ready, psql):
        f.chmod(0o755)
    return bin_dir


def _stub_guard(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    """A fake guard that records the data directory it was handed."""
    calls = tmp_path / "guard-calls"
    guard = tmp_path / "guard.sh"
    guard.write_text(
        f'#!/bin/bash\nprintf "%s\\n" "$1" >> {shlex.quote(str(calls))}\nexit {exit_code}\n'
    )
    guard.chmod(0o755)
    return guard, calls


def _run(tmp_path: Path, guard: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(START)],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "PATH": f"{_stub_bin(tmp_path)}:{os.environ['PATH']}",
            "FASTAK_GUARD": guard,
            "POSTGRES_USER": "fastak",
            "POSTGRES_DB": "lldap",
            "POSTGRES_PASSWORD": "test-pw",
        },
    )


def test_guard_runs_on_the_servers_own_data_directory(tmp_path):
    guard, calls = _stub_guard(tmp_path)
    result = _run(tmp_path, str(guard))
    assert result.returncode == 0, result.stderr
    assert calls.read_text().strip() == DATA_DIR
    assert "is on a mounted volume" in result.stdout


def test_guard_failure_is_fatal(tmp_path):
    guard, _ = _stub_guard(tmp_path, exit_code=1)
    result = _run(tmp_path, str(guard))
    assert result.returncode == 1


def test_missing_guard_is_fatal(tmp_path):
    result = _run(tmp_path, "/nonexistent")
    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert "/nonexistent" in result.stderr
    assert "is on a mounted volume" not in result.stdout
    assert "unverified" not in result.stdout


def test_non_executable_guard_is_fatal(tmp_path):
    """Present but mode 0644: a `test -f` would pass and the script would run
    unverified."""
    guard, calls = _stub_guard(tmp_path)
    guard.chmod(0o644)
    result = _run(tmp_path, str(guard))
    assert result.returncode == 1
    assert not calls.exists()


@pytest.mark.parametrize("script", [START, REPO / "tak-database" / "start.sh"])
def test_neither_database_entrypoint_warns_and_continues(script):
    """The regression in one line: the string that used to follow a missing
    guard. Both entrypoints had it."""
    assert "persistence unverified" not in script.read_text()


# ── Shutting PostgreSQL down on the way out ──────────────────────────────
#
# The guard branches exit while the postgres backgrounded at the top of the
# script is still running. PID 1 leaving without stopping it means Docker
# SIGKILLs the postmaster — crash recovery on the next boot, and
# `restart: unless-stopped` loops the container through it. tak-database's
# entrypoint stops it on every fatal branch; this one used to just exit.


def _pg_ctl_calls(tmp_path: Path) -> str:
    log = tmp_path / "pg_ctl-calls"
    return log.read_text() if log.exists() else ""


def test_guard_failure_stops_postgres_first(tmp_path):
    guard, _ = _stub_guard(tmp_path, exit_code=1)
    result = _run(tmp_path, str(guard))
    assert result.returncode == 1
    assert "stop -m fast" in _pg_ctl_calls(tmp_path)
    assert DATA_DIR in _pg_ctl_calls(tmp_path)


def test_missing_guard_stops_postgres_first(tmp_path):
    result = _run(tmp_path, "/nonexistent")
    assert result.returncode == 1
    assert "stop -m fast" in _pg_ctl_calls(tmp_path)


def test_a_successful_start_does_not_stop_postgres(tmp_path):
    guard, _ = _stub_guard(tmp_path)
    result = _run(tmp_path, str(guard))
    assert result.returncode == 0, result.stderr
    assert "stop" not in _pg_ctl_calls(tmp_path)

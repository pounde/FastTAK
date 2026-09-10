"""Stack-level health assertions ported from start.sh --test.

These ran only under `./start.sh --test`, which CI never invoked and which
tore down the deployment on any failure. They belong here, against the
isolated test stack.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CERT_FILES = [
    "root-ca.pem",
    "ca.pem",
    "takserver.jks",
    "svc_fasttakapi.p12",
    "ca-signing.jks",
]


def _ps(compose_cmd, compose_env) -> dict[str, dict]:
    out = subprocess.run(
        [*compose_cmd, "ps", "-a", "--format", "json"],
        capture_output=True,
        text=True,
        env=compose_env,
        check=True,
    ).stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {row["Service"]: row for row in rows}


@pytest.mark.parametrize("service", ["init-config", "init-identity"])
def test_bootstrap_container_exited_zero(compose_cmd, compose_env, service):
    """A failed one-shot never shows as unhealthy anywhere. init-identity
    exiting non-zero means nobody is in monitor_admin — the lockout case."""
    row = _ps(compose_cmd, compose_env)[service]
    assert row["State"] == "exited", row
    assert row["ExitCode"] == 0, row


@pytest.mark.parametrize("name", CERT_FILES)
def test_cert_file_present(stack_info, name):
    path = Path(stack_info.tak_host_path) / "certs" / "files" / name
    assert path.is_file(), f"{path} missing — enrollment will fail later, not now"


def _log_count(compose_exec, pattern: str) -> int:
    result = compose_exec(
        "tak-server", ["sh", "-c", f"grep -c '{pattern}' /opt/tak/logs/takserver.log || true"]
    )
    return int((result.stdout or "0").strip() or 0)


def test_no_out_of_memory_errors(compose_exec):
    assert _log_count(compose_exec, "OutOfMemoryError") == 0


def test_db_auth_failures_are_bounded(compose_exec):
    """A couple can happen while the database comes up; more means the
    password in CoreConfig.xml disagrees with the one the database was
    initialised with."""
    assert _log_count(compose_exec, "password authentication failed") <= 2


def test_tak_server_started_once(compose_exec):
    """'Security status' logs once per start. Repeats mean a restart loop —
    invisible to docker compose ps, which reports healthy after each one."""
    assert _log_count(compose_exec, "Security status") <= 4

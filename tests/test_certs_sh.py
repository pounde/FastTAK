"""certs.sh resolves Compose the way every other entry point does (#113):
through FASTAK_ENV_FILE and the deploy mode's compose files."""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CERTS = REPO / "certs.sh"

STUB = r"""#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
case " $* " in
  *" ps "*)
    printf 'COMPOSE_FILE=%s\n' "${COMPOSE_FILE-<unset>}" >> "$DOCKER_LOG"
    if [ "${STUB_PS_FAIL-}" = "1" ]; then
      echo "Error: no such service: tak-server" >&2
      exit 1
    fi
    echo stubid
    ;;
esac
exit 0
"""


@pytest.fixture
def deployment(tmp_path):
    env = (REPO / ".env.example").read_text()
    env = env.replace("SERVER_ADDRESS=tak.example.com", "SERVER_ADDRESS=localhost")
    (tmp_path / ".env").write_text(env)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(0o755)
    return tmp_path


def run_certs(deployment: Path, *args: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    log = deployment / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{deployment / 'bin'}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "FASTAK_ENV_FILE": str(deployment / ".env"),
    }
    result = subprocess.run(
        ["/bin/bash", str(CERTS), *args], capture_output=True, text=True, env=env, timeout=30
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_compose_is_resolved_through_the_env_file(deployment):
    result, calls = run_certs(deployment, "list")
    assert result.returncode == 0, result.stderr
    ps = next(c for c in calls if " ps " in c)
    assert f"--env-file {deployment / '.env'}" in ps
    assert "-f " not in ps, (
        "the compose files come from COMPOSE_FILE, like every other entry point"
    )


def test_direct_mode_adds_the_direct_compose_file(deployment):
    env = (deployment / ".env").read_text().replace("DEPLOY_MODE=subdomain", "DEPLOY_MODE=direct")
    (deployment / ".env").write_text(env)
    result, calls = run_certs(deployment, "list")
    assert result.returncode == 0, result.stderr
    assert any(
        c.startswith("COMPOSE_FILE=docker-compose.yml:docker-compose.direct.yml") for c in calls
    )


def test_subdomain_mode_leaves_compose_file_unset(deployment):
    _, calls = run_certs(deployment, "list")
    assert "COMPOSE_FILE=<unset>" in calls


def test_ps_failure_reports_why(deployment):
    env = {
        **os.environ,
        "PATH": f"{deployment / 'bin'}:{os.environ['PATH']}",
        "DOCKER_LOG": str(deployment / "docker.log"),
        "FASTAK_ENV_FILE": str(deployment / ".env"),
        "STUB_PS_FAIL": "1",
    }
    result = subprocess.run(
        ["/bin/bash", str(CERTS), "list"], capture_output=True, text=True, env=env, timeout=30
    )
    assert result.returncode == 1
    assert "could not ask Compose" in result.stderr
    assert "no such service" in result.stderr

"""start.sh, driven end to end with a stub docker on PATH.

The stub records every docker invocation to a log and answers the few
queries start.sh makes (health status, container ids). Tests assert on the
recorded argv — which flags reached compose — not on the stack.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
START = REPO / "start.sh"

STUB = r"""#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
# Match the compose subcommand with its surrounding spaces: the --env-file
# path is a pytest tmp dir named after the test, so it can contain "up".
case " $* " in
  *" build "*|*" build")
    [ -n "${STUB_BUILD_ERR:-}" ] && { printf '%s\n' "$STUB_BUILD_ERR" >&2; exit 1; }
    ;;
  *" up "*)
    [ -n "${STUB_UP_ERR:-}" ] && { printf '%s\n' "$STUB_UP_ERR" >&2; exit 1; }
    ;;
  *" ps "*|*" port "*) echo stubid ;;
  "inspect "*|" inspect "*) echo healthy ;;
  " exec "*) echo "5/5" ;;
esac
exit 0
"""


@pytest.fixture
def deployment(tmp_path):
    """A scratch deployment: tak/, a valid .env, and the stub on PATH."""
    (tmp_path / "tak").mkdir()
    env = (REPO / ".env.example").read_text()
    env = env.replace("SERVER_ADDRESS=tak.example.com", "SERVER_ADDRESS=localhost")
    env = env.replace("TOKENS_API_SECRET=", "TOKENS_API_SECRET=" + "0" * 64)
    (tmp_path / ".env").write_text(env)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(STUB)
    stub.chmod(0o755)
    return tmp_path


def run_start(
    deployment: Path, *args: str, extra_env: dict | None = None
) -> tuple[subprocess.CompletedProcess, list[str]]:
    log = deployment / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{deployment / 'bin'}:{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "FASTAK_ENV_FILE": str(deployment / ".env"),
        **(extra_env or {}),
    }
    result = subprocess.run(
        ["/bin/bash", str(START), *args], capture_output=True, text=True, env=env, timeout=120
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_bare_start_succeeds_against_the_stub(deployment):
    result, calls = run_start(deployment)
    assert result.returncode == 0, result.stderr + result.stdout
    assert any(c.startswith("compose") and " build" in c for c in calls)
    assert any(c.startswith("compose") and " up " in c for c in calls)


def test_direct_mode_selects_the_direct_compose_file(deployment):
    env = (deployment / ".env").read_text().replace("DEPLOY_MODE=subdomain", "DEPLOY_MODE=direct")
    (deployment / ".env").write_text(env)
    # The stub sees COMPOSE_FILE via the environment; prove it reached the up.
    stub = deployment / "bin" / "docker"
    stub.write_text(
        STUB.replace(
            '*" up "*)',
            '*" up "*) printf "COMPOSE_FILE=%s\\n" "${COMPOSE_FILE-<unset>}" >> "$DOCKER_LOG";',
        )
    )
    result, calls = run_start(deployment)
    assert result.returncode == 0, result.stderr
    assert "COMPOSE_FILE=docker-compose.yml:docker-compose.direct.yml" in calls


def test_subdomain_mode_leaves_compose_file_unset(deployment):
    stub = deployment / "bin" / "docker"
    stub.write_text(
        STUB.replace(
            '*" up "*)',
            '*" up "*) printf "COMPOSE_FILE=%s\\n" "${COMPOSE_FILE-<unset>}" >> "$DOCKER_LOG";',
        )
    )
    result, calls = run_start(deployment)
    assert result.returncode == 0, result.stderr
    assert "COMPOSE_FILE=<unset>" in calls


def test_missing_tak_dir_fails_preflight(deployment):
    (deployment / "tak").rmdir()
    result, _ = run_start(deployment)
    assert result.returncode == 1
    assert "tak/ not found" in result.stderr

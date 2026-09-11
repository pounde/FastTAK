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
    log.unlink(missing_ok=True)  # tests calling run_start twice must not see the prior call's log
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
    # Prefix, not equality: a gitignored docker-compose.override.yml in the
    # repo root (an expected local dev artefact) gets appended by
    # stack_export_compose_file, which would fail an exact-match assertion
    # with no code change.
    assert any(
        c.startswith("COMPOSE_FILE=docker-compose.yml:docker-compose.direct.yml") for c in calls
    )


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


def _up_call(calls: list[str]) -> str:
    return next(c for c in calls if c.startswith("compose") and " up " in c)


def _build_call(calls: list[str]) -> str:
    return next(c for c in calls if c.startswith("compose") and " build" in c)


def test_bare_up_removes_orphans(deployment):
    _, calls = run_start(deployment)
    assert "--remove-orphans" in _up_call(calls)


def test_named_services_are_force_recreated_without_orphan_pruning(deployment):
    """A targeted rebuild has no business pruning the project — that is what
    silently removed the capture sidecars."""
    _, calls = run_start(deployment, "monitor", "nodered")
    up = _up_call(calls)
    assert "--force-recreate" in up
    assert up.endswith("monitor nodered")
    assert "--remove-orphans" not in up
    assert _build_call(calls).endswith("monitor nodered")


def test_capture_adds_the_overlay(deployment):
    stub = deployment / "bin" / "docker"
    stub.write_text(
        STUB.replace(
            '*" up "*)',
            '*" up "*) printf "COMPOSE_FILE=%s\\n" "${COMPOSE_FILE-<unset>}" >> "$DOCKER_LOG";',
        )
    )
    result, calls = run_start(deployment, "--capture")
    assert result.returncode == 0, result.stderr
    assert any(
        c.startswith("COMPOSE_FILE=docker-compose.yml:docker-compose.capture.yml") for c in calls
    )


def test_checks_run_on_a_bare_up(deployment):
    result, calls = run_start(deployment)
    assert any(c.startswith("exec") for c in calls), "the checks exec into tak-server"
    assert "checks" in result.stdout.lower()


def test_checks_skipped_when_services_are_named(deployment):
    result, calls = run_start(deployment, "monitor")
    assert not any(c.startswith("exec") for c in calls)
    assert "skipped" in result.stdout.lower()
    assert "https://localhost:8446" in result.stdout


def test_checks_flag_overrides_the_default(deployment):
    _, calls = run_start(deployment, "monitor", "--checks")
    assert any(c.startswith("exec") for c in calls)
    _, calls = run_start(deployment, "--no-checks")
    assert not any(c.startswith("exec") for c in calls)


def test_no_wait_skips_the_health_loop(deployment):
    _, calls = run_start(deployment, "--no-wait", "--no-checks")
    assert not any(c.startswith("inspect") for c in calls)


def test_no_wait_implies_no_checks_by_default(deployment):
    _, calls = run_start(deployment, "--no-wait")
    assert not any(c.startswith("exec") for c in calls)


def test_no_wait_with_explicit_checks_still_runs_them(deployment):
    _, calls = run_start(deployment, "--no-wait", "--checks")
    assert any(c.startswith("exec") for c in calls)


def test_unknown_option_is_rejected(deployment):
    result, _ = run_start(deployment, "--bogus")
    assert result.returncode == 2
    assert "--bogus" in result.stderr


def test_failed_build_stops_the_start_and_shows_why(deployment):
    """The old line was `docker compose build --quiet 2>/dev/null` with no
    exit check: a failed build was followed by up -d starting the previous
    image and a success summary."""
    result, calls = run_start(
        deployment, "--no-checks", extra_env={"STUB_BUILD_ERR": "ERROR: failed to solve: monitor"}
    )
    assert result.returncode == 1
    assert "build failed" in result.stderr.lower()
    assert "failed to solve: monitor" in result.stderr
    assert not any(" up " in c for c in calls), "must not start the old image"


def test_failed_up_stops_the_start_and_shows_why(deployment):
    result, calls = run_start(
        deployment,
        "--no-checks",
        extra_env={"STUB_UP_ERR": "Error response from daemon: port is already allocated"},
    )
    assert result.returncode == 1
    assert "port is already allocated" in result.stderr
    assert not any(c.startswith("inspect") for c in calls), (
        "must not wait on a stack that did not start"
    )

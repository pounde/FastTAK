"""Fixtures for integration tests against a running FastTAK stack.

The stack must be running before tests start (via `just test-up`).
These fixtures discover the stack and provide transport for API calls.
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass
from glob import glob
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Stack discovery
# ---------------------------------------------------------------------------


@dataclass
class StackInfo:
    project: str
    test_dir: str
    tak_host_path: str
    repo_dir: str
    env_file: str


@pytest.fixture(scope="session")
def stack_info() -> StackInfo:
    """Discover a running test stack from /tmp/fastak-test-*/.test-state."""
    project = os.environ.get("FASTAK_TEST_PROJECT")
    if project:
        state_file = f"/tmp/{project}/.test-state"
    else:
        candidates = sorted(glob("/tmp/fastak-test-*/.test-state"), reverse=True)
        state_file = candidates[0] if candidates else None

    if not state_file or not Path(state_file).exists():
        pytest.skip("No running test stack. Run 'just test-up' first.")

    # Source the state file (bash key=value format)
    info = {}
    for line in Path(state_file).read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            # Strip surrounding quotes
            value = value.strip("\"'")
            info[key] = value

    return StackInfo(
        project=info["PROJECT"],
        test_dir=info["TEST_DIR"],
        tak_host_path=info["TAK_HOST_PATH"],
        repo_dir=info["REPO_DIR"],
        env_file=info["ENV_FILE"],
    )


# ---------------------------------------------------------------------------
# Docker Compose exec transport
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def compose_cmd(stack_info) -> list[str]:
    """Return the base docker compose command for this test stack."""
    return [
        "docker",
        "compose",
        "-p",
        stack_info.project,
        "-f",
        f"{stack_info.repo_dir}/docker-compose.yml",
        "-f",
        f"{stack_info.repo_dir}/docker-compose.test.yml",
        "--env-file",
        stack_info.env_file,
    ]


@pytest.fixture(scope="session")
def compose_exec(compose_cmd):
    """Run a command inside a container. Returns subprocess.CompletedProcess.

    Usage: compose_exec("monitor", ["curl", "-sf", "http://localhost:8080/api/ping"])
    """

    def _exec(service: str, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
        full_cmd = [*compose_cmd, "exec", "-T", service, *cmd]
        result = subprocess.run(full_cmd, capture_output=True, timeout=30, **kwargs)
        # Decode lossily so binary responses (e.g. P12 certs) don't crash.
        for attr in ("stdout", "stderr"):
            val = getattr(result, attr)
            if isinstance(val, bytes):
                setattr(result, attr, val.decode("utf-8", errors="replace"))
        return result

    return _exec


# ---------------------------------------------------------------------------
# Monitor API client
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def api(compose_exec):
    """Call the Monitor API. Returns (status_code, parsed_json_or_None).

    Usage: status, data = api("GET", "/api/ping")
    """

    def _call(method: str, path: str, json_data: dict | None = None):
        cmd = [
            "curl",
            "-s",
            "-w",
            "\n%{http_code}",
            "-X",
            method,
            f"http://localhost:8080{path}",
        ]
        if json_data is not None:
            cmd += [
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps(json_data),
            ]
        result = compose_exec("monitor", cmd)
        lines = result.stdout.strip().rsplit("\n", 1)
        if len(lines) == 2:
            body_str, code_str = lines
        elif len(lines) == 1:
            body_str, code_str = "", lines[0]
        else:
            return 0, None

        try:
            status = int(code_str.strip())
        except ValueError:
            status = 0

        try:
            body = json.loads(body_str) if body_str else None
        except json.JSONDecodeError:
            body = None

        return status, body

    return _call


# ---------------------------------------------------------------------------
# Shared resource names (unique per test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def run_id():
    """Unique suffix for test resources, preventing Authentik name collisions."""
    return str(int(time.time()))


@pytest.fixture(scope="session")
def test_user_name(run_id):
    return f"tstu_{run_id}"


@pytest.fixture(scope="session")
def test_lifecycle_user_name(run_id):
    return f"tstl_{run_id}"


@pytest.fixture(scope="session")
def test_group_name(run_id):
    return f"TST_GRP_{run_id}"


@pytest.fixture(scope="session")
def test_cert_name(run_id):
    return f"tstcert_{run_id}"


@pytest.fixture(scope="session")
def test_dup_cert_name(run_id):
    return f"tstdup_{run_id}"


@pytest.fixture(scope="session")
def svc_test_group_name(run_id):
    return f"SVC_TST_{run_id}"


@pytest.fixture(scope="session")
def svc_data_name(run_id):
    return f"tstd_{run_id}"


@pytest.fixture(scope="session")
def svc_admin_name(run_id):
    return f"tsta_{run_id}"


@pytest.fixture(scope="session")
def webadmin_id(api):
    """Resolve the webadmin user ID."""
    status, data = api("GET", "/api/users?search=webadmin")
    assert status == 200
    results = data.get("results", [])
    assert len(results) > 0, "webadmin user not found"
    return results[0]["id"]


@pytest.fixture(scope="session")
def authentik_token(stack_info):
    """Read the Authentik API token from the test .env."""
    for line in Path(stack_info.env_file).read_text().splitlines():
        if line.startswith("AUTHENTIK_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    pytest.fail("AUTHENTIK_API_TOKEN not found in test .env")


# ---------------------------------------------------------------------------
# Shared mutable state for ordered lifecycle tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def created_resources():
    """Session-wide dict for sharing resource IDs between ordered tests.

    Prefer this over self.__class__ -- it's explicit, survives across
    test files, and doesn't break under parallel execution.

    Keys are set by test functions: created_resources["user_id"] = 123
    """
    return {}


# ---------------------------------------------------------------------------
# Cleanup (session teardown)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_resources(api, created_resources):
    """Clean up test resources after all tests complete.

    Runs in reverse creation order: service accounts -> groups -> users.
    Failures are logged but don't fail the test run.
    """
    yield
    # Teardown
    for key in ("svc_data_id", "svc_admin_id"):
        rid = created_resources.get(key)
        if rid:
            api("DELETE", f"/api/service-accounts/{rid}")

    for key in ("svc_group_id", "test_group_id"):
        rid = created_resources.get(key)
        if rid:
            api("DELETE", f"/api/groups/{rid}")

    for key in ("lifecycle_user_id",):
        rid = created_resources.get(key)
        if rid:
            api("DELETE", f"/api/users/{rid}")

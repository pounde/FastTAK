"""tak-server/healthcheck.sh, driven end to end outside the container.

The script's paths and probe URL come from the environment, so it runs
against a fake /proc tree, temp logs, and stub curl/nc on PATH. Tests
assert on the one line it prints and its exit status — what Docker sees.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HEALTHCHECK = REPO / "tak-server" / "healthcheck.sh"

# The five processes check 1 looks for, as they appear in /proc/<pid>/cmdline.
PROCESSES = [
    "spring.profiles.active=config",
    "spring.profiles.active=messaging",
    "spring.profiles.active=api",
    "takserver-retention.jar",
    "takserver-pm.jar",
]

CURL_STUB = "#!/bin/sh\nprintf '%s' \"${STUB_HTTP_CODE:-200}\"\n"
NC_STUB = '#!/bin/sh\nexit "${STUB_NC_RC:-0}"\n'


@pytest.fixture
def container(tmp_path):
    """A healthy TAK container in miniature: every process present, quiet
    logs, no certs (check 4 loops over nothing), stubs answering healthy."""
    proc = tmp_path / "proc"
    for pid, marker in enumerate(PROCESSES, start=100):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(f"java\0-D{marker}\0".encode())
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "takserver.log").write_text("INFO  Security status\n")
    (logs / "takserver-messaging.log").write_text("INFO  messaging up\n")
    (tmp_path / "certs").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("curl", CURL_STUB), ("nc", NC_STUB)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    return tmp_path


def run_hc(container: Path, **env_overrides: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{container / 'bin'}:{os.environ['PATH']}",
        "PROC_ROOT": str(container / "proc"),
        "LOGFILE": str(container / "logs" / "takserver.log"),
        "MSG_LOGFILE": str(container / "logs" / "takserver-messaging.log"),
        "CERT_DIR": str(container / "certs"),
        "INCIDENT_DIR": str(container / "logs" / "incident"),
        **env_overrides,
    }
    return subprocess.run(
        ["/bin/sh", str(HEALTHCHECK)], capture_output=True, text=True, env=env, timeout=30
    )


def test_healthy_container_passes(container):
    result = run_hc(container)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "HEALTHY: all processes running, ports ok, certs valid"
    assert result.stderr == ""


def test_missing_process_is_named(container):
    (container / "proc" / "102" / "cmdline").write_bytes(b"sleep\0")
    result = run_hc(container)
    assert result.returncode == 1
    assert result.stdout.strip() == "UNHEALTHY: missing processes: api"

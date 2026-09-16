"""tak-server/healthcheck.sh, driven end to end outside the container.

The script's paths and probe URL come from the environment, so it runs
against a fake /proc tree, temp logs, and stub curl/nc on PATH. Tests
assert on the one line it prints and its exit status — what Docker sees.
"""

import os
import shutil
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

CURL_STUB = '#!/bin/sh\nprintf %s "${STUB_HTTP_CODE-200}"\nexit "${STUB_CURL_RC:-0}"\n'
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


@pytest.mark.parametrize("code", ["200", "302", "401", "403", "404"])
def test_api_answering_with_any_non_5xx_is_healthy(container, code):
    result = run_hc(container, STUB_HTTP_CODE=code)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("code", ["500", "502", "503"])
def test_api_server_error_is_unhealthy(container, code):
    """The outage: TLS up, port open, every request a 500, container HEALTHY."""
    result = run_hc(container, STUB_HTTP_CODE=code)
    assert result.returncode == 1
    assert result.stdout.strip() == (
        f"UNHEALTHY: https://localhost:8446/Marti/api/version returned HTTP {code} "
        "(the API is up but failing)"
    )


@pytest.mark.parametrize("code", ["000", ""])
def test_no_tls_response_is_unhealthy(container, code):
    result = run_hc(container, STUB_HTTP_CODE=code)
    assert result.returncode == 1
    assert result.stdout.strip() == (
        "UNHEALTHY: no TLS response from https://localhost:8446/Marti/api/version"
    )


def test_probe_timeout_is_named(container):
    result = run_hc(container, STUB_HTTP_CODE="000", STUB_CURL_RC="28")
    assert result.returncode == 1
    assert result.stdout.strip() == (
        "UNHEALTHY: https://localhost:8446/Marti/api/version did not answer within 5s"
    )


def test_probe_url_is_overridable(container):
    result = run_hc(container, STUB_HTTP_CODE="500", PROBE_URL="https://localhost:8446/")
    assert "https://localhost:8446/ returned HTTP 500" in result.stdout


def _log_with(container: Path, *lines: str, padding: int = 0) -> None:
    """Write takserver.log as the given lines followed by `padding` filler lines."""
    body = "\n".join(lines) + "\n" + "INFO  filler\n" * padding
    (container / "logs" / "takserver.log").write_text(body)


def test_ignite_disconnect_outside_the_window_is_healthy(container):
    """The scan is bounded so an old incident cannot pin the container unhealthy."""
    _log_with(container, "ERROR IgniteClientDisconnectedException", padding=600)
    result = run_hc(container)
    assert result.returncode == 0, result.stdout


def test_recent_oom_is_unhealthy(container):
    _log_with(container, "java.lang.OutOfMemoryError: Java heap space", padding=10)
    result = run_hc(container)
    assert result.returncode == 1
    assert (
        result.stdout.strip()
        == "UNHEALTHY: OutOfMemoryError in the last 500 lines of takserver.log"
    )


def test_old_oom_is_no_longer_sticky(container):
    _log_with(container, "java.lang.OutOfMemoryError: Java heap space", padding=600)
    result = run_hc(container)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    ("line", "match"),
    [
        (
            "ERROR IgniteClientDisconnectedException: Client node disconnected",
            "IgniteClientDisconnected",
        ),
        ("ERROR ClusterTopologyException: topology changed", "ClusterTopologyException"),
        ("WARN  Failed to connect to node [id=1]", "Failed to connect to node"),
    ],
)
def test_ignite_disconnect_in_recent_log_is_unhealthy(container, line, match):
    _log_with(container, line, padding=100)
    result = run_hc(container)
    assert result.returncode == 1
    assert result.stdout.strip() == f"UNHEALTHY: {match} in the last 500 lines of takserver.log"


def _incidents(container: Path) -> list[Path]:
    d = container / "logs" / "incident"
    return sorted(d.glob("*.log")) if d.is_dir() else []


def test_first_failure_captures_the_log_tails_once(container):
    _log_with(container, "line one", "java.lang.OutOfMemoryError: heap", padding=5)
    (container / "logs" / "takserver-messaging.log").write_text("MSG line\n")

    first = run_hc(container)
    assert first.returncode == 1
    files = _incidents(container)
    assert len(files) == 1
    assert files[0].name.endswith("-oom.log")
    body = files[0].read_text()
    assert "OutOfMemoryError" in body and "MSG line" in body
    assert (container / "logs" / "incident" / ".tripped").exists()

    second = run_hc(container)
    assert second.returncode == 1
    assert _incidents(container) == files, "a second failure in the same incident captures nothing"


def test_marker_set_by_the_entrypoint_suppresses_the_boot_capture(container):
    """start.sh sets INCIDENT_DIR/.tripped before TAK starts (#79); a probe
    that lands during start_period must not snapshot that boot."""
    incident_dir = container / "logs" / "incident"
    incident_dir.mkdir(parents=True)
    (incident_dir / ".tripped").write_text("")
    (container / "proc" / "102" / "cmdline").write_bytes(b"sleep\0")

    result = run_hc(container)
    assert result.returncode == 1
    assert _incidents(container) == []

    (container / "proc" / "102" / "cmdline").write_bytes(f"java\0-D{PROCESSES[2]}\0".encode())
    healthy = run_hc(container)
    assert healthy.returncode == 0
    assert not (incident_dir / ".tripped").exists()


def test_healthy_pass_clears_the_marker_so_the_next_incident_captures(container):
    _log_with(container, "java.lang.OutOfMemoryError: heap")
    run_hc(container)
    _log_with(container, "INFO  recovered")
    healthy = run_hc(container)
    assert healthy.returncode == 0
    assert not (container / "logs" / "incident" / ".tripped").exists()
    _log_with(container, "java.lang.OutOfMemoryError: again")
    run_hc(container)
    assert len(_incidents(container)) == 2


def test_only_the_newest_five_incidents_are_kept(container):
    d = container / "logs" / "incident"
    d.mkdir(parents=True)
    for i in range(6):
        (d / f"2026010{i}T000000Z-oom.log").write_text("old\n")
    _log_with(container, "java.lang.OutOfMemoryError: heap")
    run_hc(container)
    names = [p.name for p in _incidents(container)]
    assert len(names) == 5
    assert "20260100T000000Z-oom.log" not in names and "20260101T000000Z-oom.log" not in names


def test_capture_names_the_check_that_tripped(container):
    result = run_hc(container, STUB_HTTP_CODE="503")
    assert result.returncode == 1
    assert _incidents(container)[0].name.endswith("-api-probe.log")


def test_unwritable_incident_dir_does_not_change_the_verdict(container):
    (container / "logs" / "incident").write_text("a file where the directory should be")
    result = run_hc(container, STUB_HTTP_CODE="503")
    assert result.returncode == 1
    assert result.stdout.strip().startswith(
        "UNHEALTHY: https://localhost:8446/Marti/api/version returned HTTP 503"
    )
    assert "cannot write" in result.stderr


def _bin_without(tool: str, container: Path) -> Path:
    """A PATH directory with the usual coreutils healthcheck.sh needs, minus
    `tool`, plus the curl/nc stubs (again minus `tool` if it is one of them).
    Used standalone as PATH — no fallback to the real PATH — so `tool` is
    genuinely unfindable, not just shadowed."""
    needed = ["mkdir", "date", "tr", "grep", "sed", "tail", "head", "ls", "sort", "rm", "basename"]
    bin_dir = container / "nopath-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in needed:
        if name == tool:
            continue
        found = shutil.which(name)
        if found:
            (bin_dir / name).symlink_to(found)
    for name, body in (("curl", CURL_STUB), ("nc", NC_STUB)):
        if name == tool:
            continue
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    return bin_dir


def test_missing_curl_says_so(container):
    bin_dir = _bin_without("curl", container)
    result = run_hc(container, PATH=str(bin_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "healthcheck: curl not found; API probe skipped" in result.stderr


def test_missing_nc_says_so(container):
    bin_dir = _bin_without("nc", container)
    result = run_hc(container, PATH=str(bin_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "healthcheck: nc not found; port 8089 check skipped" in result.stderr


def test_port_8089_refused_is_unhealthy(container):
    result = run_hc(container, STUB_NC_RC="1")
    assert result.returncode == 1
    assert result.stdout.strip() == "UNHEALTHY: port 8089 not accepting connections"
    assert _incidents(container)[0].name.endswith("-port-8089.log")


def test_expired_cert_is_unhealthy(container):
    # openssl argv shapes exercised here: `x509 -in <pem> -noout` (the
    # validity probe; exit 0 means "this is a cert") and
    # `x509 -enddate -noout -in <pem>` (prints the expiry the script parses).
    openssl_stub = (
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *-enddate*) printf "notAfter=Jan 01 00:00:00 2020 GMT\\n" ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    (container / "bin" / "openssl").write_text(openssl_stub)
    (container / "bin" / "openssl").chmod(0o755)
    (container / "certs" / "takserver.pem").write_bytes(b"stub cert bytes")
    result = run_hc(container)
    assert result.returncode == 1
    assert result.stdout.strip() == "UNHEALTHY: cert EXPIRED: takserver.pem"
    assert _incidents(container)[0].name.endswith("-cert-expired.log")


def test_capture_without_a_messaging_log_is_quiet_on_stderr(container):
    (container / "logs" / "takserver-messaging.log").unlink()
    _log_with(container, "java.lang.OutOfMemoryError: heap")
    result = run_hc(container)
    assert result.returncode == 1
    files = _incidents(container)
    assert len(files) == 1
    body = files[0].read_text()
    assert "OutOfMemoryError" in body
    assert "== tail -n 500 " in body and "takserver-messaging.log ==" in body
    assert result.stderr == ""

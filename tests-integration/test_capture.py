"""Capture overlay integration tests.

Verifies the mitmproxy capture sidecar end-to-end against the test stack:
  - init-capture materializes the cert bundles from TAK's cert store
  - tak-mitm reverse-proxies 8443 (HTTPS/Marti) and 8089 (CoT/TLS) to
    tak-server, presenting TAK's own cert downstream and a TAK-issued client
    cert upstream (mTLS)
  - decrypted request/response flows are written to the capture file

The test exercises the REAL init-capture + tak-mitm services defined in
docker-compose.capture.yml (layered over the test stack via
docker-compose.capture.test.yml), so a future change to the mitm command or
cert layout that breaks capture will fail this test.

Traffic is driven over the Docker network by service name (tak-mitm:8443 /
tak-mitm:8089) from the already-running monitor container, so no host ports
are published for mitm. Captured flows are read back by running mitmdump
against the capture file in an ephemeral container.

Requires the running test stack (`just test-up`). Skipped otherwise.
"""

import json
import os
import subprocess
import time
from glob import glob
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _mitm_image(env_file: str) -> str:
    """mitmproxy image at the version pinned in the stack's .env."""
    for line in Path(env_file).read_text().splitlines():
        if line.startswith("MITMPROXY_VERSION="):
            version = line.partition("=")[2].split("#")[0].strip()
            return f"mitmproxy/mitmproxy:{version}"
    raise RuntimeError(f"MITMPROXY_VERSION not set in {env_file}")


# A Marti endpoint any authenticated client cert can read. mitm authenticates
# upstream as the mitm-proxy user, so this proves the full downstream-TLS →
# upstream-mTLS → response chain through the proxy.
MARTI_PATH = "/Marti/api/version"


# ---------------------------------------------------------------------------
# Stack discovery (session-scoped; conftest's stack_info is function-scoped)
# ---------------------------------------------------------------------------


def _discover_state() -> dict | None:
    project = os.environ.get("FASTAK_TEST_PROJECT")
    state_file = None
    if project and Path(f"/tmp/{project}/.test-state").exists():
        state_file = f"/tmp/{project}/.test-state"
    else:
        candidates = sorted(glob("/tmp/fastak-test-*/.test-state"), reverse=True)
        state_file = candidates[0] if candidates else None
    if not state_file or not Path(state_file).exists():
        return None
    info: dict = {}
    for line in Path(state_file).read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            info[key] = value.strip("\"'")
    return info


class CaptureCtx:
    def __init__(self, compose_cmd, env, cap_dir, project, mitm_image):
        self.compose_cmd = compose_cmd
        self.env = env
        self.cap_dir = cap_dir
        self.project = project
        self.mitm_image = mitm_image

    @property
    def flow_file(self) -> Path:
        return Path(self.cap_dir) / "current.flow"

    def flow_size(self) -> int:
        return self.flow_file.stat().st_size if self.flow_file.exists() else 0

    def wait_flow_grows(self, baseline: int, deadline_seconds: int = 15) -> int:
        """Poll until the capture file grows past ``baseline`` bytes."""
        deadline = time.time() + deadline_seconds
        while time.time() < deadline:
            size = self.flow_size()
            if size > baseline:
                return size
            time.sleep(0.5)
        return self.flow_size()

    def dump_flows(self) -> str:
        """Return mitmdump's text rendering of the captured flows.

        Reads the (mitm-held, append-only) capture file from an ephemeral
        mitmproxy container — no mitmproxy install needed on the host.
        """
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{self.cap_dir}:/c:ro",
                self.mitm_image,
                "mitmdump",
                "-nr",
                "/c/current.flow",
                "--set",
                "flow_detail=1",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout + result.stderr

    def exec_monitor(self, script: str, timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a Python snippet inside the monitor container (has httpx + net)."""
        result = subprocess.run(
            [*self.compose_cmd, "exec", "-T", "monitor", "python", "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=self.env,
        )
        return result


def _service_state(compose_cmd, env, service) -> dict | None:
    result = subprocess.run(
        [*compose_cmd, "ps", "-a", "--format", "json", service],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("Service") == service:
            return obj
    return None


@pytest.fixture(scope="session")
def capture_stack():
    info = _discover_state()
    if not info:
        pytest.skip("No running test stack. Run 'just test-up' first.")

    project = info["PROJECT"]
    repo = info["REPO_DIR"]
    env_file = info["ENV_FILE"]
    test_dir = info["TEST_DIR"]
    tak_host = info["TAK_HOST_PATH"]

    cap_dir = f"{test_dir}/captures"
    cert_dir = f"{test_dir}/capture-mitm"
    os.makedirs(cap_dir, exist_ok=True)
    os.makedirs(cert_dir, exist_ok=True)

    # Compose needs every interpolated var defined even to `up` a subset of
    # services (docker-compose.test.yml references HOST_ENV_FILE/BACKUP_DIR with
    # no defaults). Mirror what test-setup.sh exports, plus the capture paths.
    env = {
        **os.environ,
        "TAK_HOST_PATH": tak_host,
        "BACKUP_DIR": f"{test_dir}/backups",
        "HOST_ENV_FILE": env_file,
        "CAPTURE_DIR": cap_dir,
        "CAPTURE_CERT_DIR": cert_dir,
    }

    compose_cmd = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        f"{repo}/docker-compose.yml",
        "-f",
        f"{repo}/docker-compose.test.yml",
        "-f",
        f"{repo}/docker-compose.capture.yml",
        "-f",
        f"{repo}/docker-compose.capture.test.yml",
        "--env-file",
        env_file,
    ]

    # Bring up ONLY the capture services. --no-recreate leaves the running
    # tak-server (and everything else) untouched; capture.test.yml restores
    # tak-server's test ports so its merged config matches what's running.
    up = subprocess.run(
        [*compose_cmd, "up", "-d", "--build", "--no-recreate", "init-capture", "tak-mitm"],
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    assert up.returncode == 0, f"capture bring-up failed:\n{up.stdout}\n{up.stderr}"

    # init-capture is one-shot; depends_on makes `up` wait for it, but confirm.
    ic = _service_state(compose_cmd, env, "init-capture")
    assert ic is not None, "init-capture container not found after up"
    assert ic.get("State") == "exited" and ic.get("ExitCode") == 0, (
        f"init-capture did not complete cleanly: {ic}"
    )

    # Wait for tak-mitm to be running.
    deadline = time.time() + 60
    while time.time() < deadline:
        mitm = _service_state(compose_cmd, env, "tak-mitm")
        if mitm and mitm.get("State") == "running":
            break
        time.sleep(1)
    else:
        logs = subprocess.run(
            [*compose_cmd, "logs", "tak-mitm"], capture_output=True, text=True, timeout=30, env=env
        )
        pytest.fail(f"tak-mitm never reached running state.\n{logs.stdout}\n{logs.stderr}")

    # Give mitmdump a moment to bind both reverse listeners.
    time.sleep(2)

    yield CaptureCtx(
        compose_cmd=compose_cmd,
        env=env,
        cap_dir=cap_dir,
        project=project,
        mitm_image=_mitm_image(env_file),
    )

    # Teardown: remove just the capture services (harness test-down removes the
    # rest). Leave the flow files under TEST_DIR for post-mortem; test-down
    # rm -rf's the whole TEST_DIR.
    subprocess.run(
        [*compose_cmd, "rm", "-sf", "tak-mitm", "init-capture"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_init_capture_materializes_certs(capture_stack):
    """init-capture writes the three cert bundles + ready sentinel."""
    cert_dir = Path(capture_stack.cap_dir).parent / "capture-mitm"
    for name in ("server-bundle.pem", "mitm-client.pem", "ca.pem", "ready"):
        assert (cert_dir / name).exists(), f"init-capture did not produce {name}"
    # Bundles must carry both a cert and a private key.
    bundle = (cert_dir / "server-bundle.pem").read_text()
    assert "BEGIN CERTIFICATE" in bundle and "PRIVATE KEY" in bundle


def test_8443_https_flow_is_captured(capture_stack):
    """An HTTPS request through mitm reaches TAK and is recorded as a flow."""
    baseline = capture_stack.flow_size()

    # Drive an HTTPS GET through mitm from the monitor container. mitmproxy
    # doesn't request a downstream client cert; it authenticates upstream as
    # mitm-proxy, so verify=False (host/cert mismatch is expected) is enough.
    script = (
        "import httpx;"
        f"r=httpx.get('https://tak-mitm:8443{MARTI_PATH}', verify=False, timeout=20);"
        "print('STATUS', r.status_code);"
        "print('BODY', r.text[:200])"
    )
    resp = capture_stack.exec_monitor(script)
    assert "STATUS 200" in resp.stdout, (
        f"request through mitm did not return 200:\n{resp.stdout}\n{resp.stderr}"
    )

    # The exchange must land in the capture file...
    grown = capture_stack.wait_flow_grows(baseline)
    assert grown > baseline, "capture file did not grow after the 8443 request"

    # ...and be readable as a flow for that path.
    dump = capture_stack.dump_flows()
    assert MARTI_PATH in dump, f"captured flows don't mention {MARTI_PATH}:\n{dump[:1000]}"


def test_8089_cot_stream_is_captured(capture_stack):
    """A TLS connection to the CoT port through mitm is recorded.

    Proves the reverse:tls:// path (raw TLS-over-TCP, not HTTP): mitm decrypts
    the client side and re-establishes mTLS upstream to tak-server:8089.
    """
    baseline = capture_stack.flow_size()

    # Open a TLS socket to mitm's 8089 listener, send a CoT ping, read any
    # reply, then close so mitm flushes the tcp flow.
    script = (
        "import socket, ssl, time;"
        "ctx=ssl._create_unverified_context();"
        "s=ctx.wrap_socket(socket.create_connection(('tak-mitm', 8089), timeout=15),"
        " server_hostname='tak-mitm');"
        "s.sendall(b'<?xml version=\\'1.0\\'?><event version=\\'2.0\\' uid=\\'capture-test\\'"
        " type=\\'a-f-G\\' how=\\'m-g\\' time=\\'2026-01-01T00:00:00Z\\'"
        " start=\\'2026-01-01T00:00:00Z\\' stale=\\'2026-01-01T00:05:00Z\\'>"
        "<point lat=\\'0\\' lon=\\'0\\' hae=\\'0\\' ce=\\'9\\' le=\\'9\\'/></event>');"
        "time.sleep(2);"
        "s.close();"
        "print('SENT')"
    )
    resp = capture_stack.exec_monitor(script)
    assert "SENT" in resp.stdout, f"could not open TLS to mitm 8089:\n{resp.stdout}\n{resp.stderr}"

    grown = capture_stack.wait_flow_grows(baseline)
    assert grown > baseline, "capture file did not grow after the 8089 TLS connection"

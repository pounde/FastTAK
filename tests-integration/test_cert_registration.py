"""Service account cert registration tests.

These tests verify that svc_fasttakapi.p12 exists and can authenticate
to the TAK Server API. Uses host-side curl with the P12 cert.
"""

import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(360)]


class TestCertRegistration:
    def test_svc_fasttakapi_p12_exists(self, stack_info):
        cert_path = Path(stack_info.tak_host_path) / "certs/files/svc_fasttakapi.p12"
        assert cert_path.exists(), f"svc_fasttakapi.p12 not found at {cert_path}"

    def test_svc_fasttakapi_authenticates(self, stack_info, compose_exec):
        """Wait for cert registration, then verify API access."""
        cert_path = f"{stack_info.tak_host_path}/certs/files/svc_fasttakapi.p12"

        # Wait for register-api-cert.sh to complete (runs in background
        # after TAK Server starts, may take several minutes)
        for waited in range(0, 300, 10):
            result = compose_exec(
                "tak-server",
                [
                    "java",
                    "-jar",
                    "/opt/tak/utils/UserManager.jar",
                    "certmod",
                    "-s",
                    "/opt/tak/certs/files/svc_fasttakapi.pem",
                ],
            )
            if "ROLE_ADMIN" in result.stdout:
                break
            time.sleep(10)

        # Test API access with the P12 cert
        result = subprocess.run(
            [
                "curl",
                "-sk",
                "--cert-type",
                "P12",
                "--cert",
                f"{cert_path}:atakatak",
                "-w",
                "%{http_code}",
                "-o",
                "/dev/null",
                "https://localhost:18443/Marti/api/plugins/info/all",
            ],
            capture_output=True,
            text=True,
        )
        http_code = result.stdout.strip()[-3:]
        assert http_code == "200", f"Expected 200, got {http_code}"

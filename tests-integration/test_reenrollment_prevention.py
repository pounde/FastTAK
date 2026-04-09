"""Re-enrollment prevention: revoking a cert deletes the enrollment token."""

import pytest

pytestmark = pytest.mark.integration


class TestReenrollmentPrevention:
    @pytest.mark.dependency(name="reenroll_token")
    def test_create_enrollment_token(self, api, webadmin_id, run_id, created_resources):
        """Create an enrollment token via monitor API."""
        status, data = api("POST", f"/api/users/{webadmin_id}/enroll")
        assert status == 200, f"Failed to create enrollment token: {data}"
        url = data.get("enrollment_url", "")
        assert "token=" in url, f"No token in enrollment URL: {url}"
        token = url.split("token=")[1]
        assert token, "Empty token in enrollment URL"
        created_resources["reenroll_token"] = token

    @pytest.mark.dependency(depends=["reenroll_token"])
    def test_token_authenticates_before_revocation(self, compose_exec, created_resources):
        """Token should authenticate via Basic Auth on TAK Server 8446."""
        result = compose_exec(
            "tak-server",
            [
                "curl",
                "-sk",
                "-u",
                f"webadmin:{created_resources['reenroll_token']}",
                "-w",
                "%{http_code}",
                "-o",
                "/dev/null",
                "https://localhost:8446/Marti/api/tls/config",
            ],
        )
        code = result.stdout.strip()[-3:]
        assert code == "200", f"Expected 200, got {code}"

    @pytest.mark.dependency(name="reenroll_revoke", depends=["reenroll_token"])
    def test_generate_and_revoke_cert(self, api, webadmin_id, run_id):
        """Generate a cert then revoke it -- triggers token deletion."""
        cert_name = f"reenroll_{run_id}"
        api(
            "POST",
            f"/api/users/{webadmin_id}/certs/generate",
            {"name": cert_name},
        )
        api(
            "POST",
            f"/api/users/{webadmin_id}/certs/revoke",
            {"cert_name": cert_name},
        )

    @pytest.mark.dependency(depends=["reenroll_revoke"])
    def test_token_rejected_after_revocation(self, compose_exec, created_resources):
        """Same token should now be rejected (HTTP 401)."""
        result = compose_exec(
            "tak-server",
            [
                "curl",
                "-sk",
                "-u",
                f"webadmin:{created_resources['reenroll_token']}",
                "-w",
                "%{http_code}",
                "-o",
                "/dev/null",
                "https://localhost:8446/Marti/api/tls/config",
            ],
        )
        code = result.stdout.strip()[-3:]
        assert code == "401", f"Expected 401, got {code}"

"""User certificate management integration tests.

Generate, list, download, duplicate-reject, revoke, CRL check, revoked flag.
Uses the webadmin user which always exists.
"""

import pytest

pytestmark = pytest.mark.integration


class TestCertManagement:
    @pytest.mark.dependency(name="cert_generate")
    def test_generate_cert(self, api, webadmin_id, test_cert_name, created_resources):
        status, data = api(
            "POST",
            f"/api/users/{webadmin_id}/certs/generate",
            {"name": test_cert_name},
        )
        assert status == 201
        assert data["name"] == test_cert_name

    @pytest.mark.dependency(name="cert_list", depends=["cert_generate"])
    def test_cert_in_list(self, api, webadmin_id, test_cert_name):
        status, data = api("GET", f"/api/users/{webadmin_id}/certs")
        assert status == 200
        cert = next((c for c in data if c["name"] == test_cert_name), None)
        assert cert is not None, f"{test_cert_name} not in cert list"
        assert cert["downloadable"] is True
        assert cert["revoked"] is not True
        assert "cert_id" in cert

    @pytest.mark.dependency(name="cert_download", depends=["cert_generate"])
    def test_download_cert(self, api, webadmin_id, test_cert_name):
        status, _data = api(
            "GET",
            f"/api/users/{webadmin_id}/certs/download/{test_cert_name}",
        )
        assert status == 200

    @pytest.mark.dependency(name="cert_dup_409", depends=["cert_generate"])
    def test_duplicate_cert_409(self, api, webadmin_id, test_cert_name):
        status, _data = api(
            "POST",
            f"/api/users/{webadmin_id}/certs/generate",
            {"name": test_cert_name},
        )
        assert status == 409

    @pytest.mark.dependency(name="cert_revoke", depends=["cert_list"])
    def test_revoke_cert(self, api, webadmin_id, test_cert_name):
        status, data = api(
            "POST",
            f"/api/users/{webadmin_id}/certs/revoke",
            {"cert_name": test_cert_name},
        )
        assert status == 200
        assert data.get("success") is True

    @pytest.mark.dependency(name="cert_crl", depends=["cert_revoke"])
    def test_crl_updated(self, compose_exec):
        result = compose_exec(
            "tak-server",
            [
                "openssl",
                "crl",
                "-in",
                "/opt/tak/certs/files/ca.crl",
                "-text",
                "-noout",
            ],
        )
        # Count revoked serial entries in CRL output
        serial_count = result.stdout.count("Serial Number")
        assert serial_count > 0, "CRL is empty after revocation"

    @pytest.mark.dependency(depends=["cert_revoke"])
    def test_revoked_flag_in_list(self, api, webadmin_id, test_cert_name):
        status, data = api("GET", f"/api/users/{webadmin_id}/certs")
        assert status == 200
        cert = next((c for c in data if c["name"] == test_cert_name), None)
        assert cert is not None
        assert cert["revoked"] is True

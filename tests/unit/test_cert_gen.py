"""Tests for service account certificate generation."""

from unittest.mock import MagicMock, patch

from app.api.service_accounts.cert_gen import (
    generate_client_cert,
    parse_ca_subject,
    register_admin_cert,
)


class TestParseCaSubject:
    def test_extracts_fields_from_subject(self):
        subject = "C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"
        result = parse_ca_subject(subject)
        assert result == {
            "state": "XX",
            "city": "Default",
            "org_unit": "FastTAK",
        }

    def test_handles_missing_fields(self):
        subject = "C=US, CN=FastTAK-CA"
        result = parse_ca_subject(subject)
        assert result["state"] == "XX"
        assert result["city"] == "Default"
        assert result["org_unit"] == "FastTAK"

    def test_handles_spaces_around_equals(self):
        subject = "C = US, ST = Texas, L = Austin, O = TAK, OU = MyUnit, CN = Test"
        result = parse_ca_subject(subject)
        assert result["state"] == "Texas"
        assert result["city"] == "Austin"
        assert result["org_unit"] == "MyUnit"


class TestGenerateClientCert:
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_generates_cert_with_default_validity(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.side_effect = [
            (0, b"subject=C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"),
            (0, b"cert generated"),
            (0, b"signed"),
            (0, b"bundled"),
            (0, b"registered"),  # certmod registration
        ]

        result = generate_client_cert("svc_test", validity_days=365)
        assert result["success"] is True

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_when_container_missing(self, mock_find):
        mock_find.return_value = None
        result = generate_client_cert("svc_test")
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_validates_name(self, mock_find):
        result = generate_client_cert("invalid name!")
        assert result["success"] is False
        assert "Name must" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_csr_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.side_effect = [
            (0, b"subject=C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"),
            (1, b"openssl error"),
        ]

        result = generate_client_cert("svc_test")
        assert result["success"] is False
        assert "CSR generation failed" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_signing_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.side_effect = [
            (0, b"subject=C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"),
            (0, b"ok"),
            (1, b"signing error"),
        ]

        result = generate_client_cert("svc_test")
        assert result["success"] is False
        assert "Signing failed" in result["error"]

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_p12_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.side_effect = [
            (0, b"subject=C=US, ST=XX, L=Default, O=TAK, OU=FastTAK, CN=FastTAK-CA"),
            (0, b"ok"),
            (0, b"ok"),
            (1, b"p12 error"),
        ]

        result = generate_client_cert("svc_test")
        assert result["success"] is False
        assert "P12 creation failed" in result["error"]


class TestRegisterAdminCert:
    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_runs_certmod(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (0, b"ROLE_ADMIN granted")

        result = register_admin_cert("svc_admin")
        assert result["success"] is True

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_on_failure(self, mock_find):
        container = MagicMock()
        mock_find.return_value = container
        container.exec_run.return_value = (1, b"InvocationTargetException")

        result = register_admin_cert("svc_admin")
        assert result["success"] is False

    @patch("app.api.service_accounts.cert_gen.find_container")
    def test_returns_error_when_container_missing(self, mock_find):
        mock_find.return_value = None
        result = register_admin_cert("svc_admin")
        assert result["success"] is False
        assert "not found" in result["error"]

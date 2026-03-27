"""Tests for TAK Server certadmin/groups API client."""

from unittest.mock import patch

import pytest
from app.api.users.tak_server import TakServerClient


@pytest.fixture
def client():
    """Client with mocked SSL — no actual mTLS in tests."""
    with patch.object(TakServerClient, "_init_ssl", return_value=None):
        c = TakServerClient(
            base_url="https://tak-test:8443",
            cert_path="/tmp/test.p12",
            cert_password="testpass",
        )
    return c


@pytest.fixture
def mock_http(client):
    """Patch the client's internal HTTP methods."""
    with patch.object(client, "_get") as mock_get, patch.object(client, "_delete") as mock_delete:
        yield {"get": mock_get, "delete": mock_delete}


class TestListUserCerts:
    def test_returns_certs_for_user(self, client, mock_http):
        mock_http["get"].return_value = {
            "version": "3",
            "data": [
                {
                    "id": 42,
                    "subjectDn": "CN=jsmith",
                    "hash": "abc123",
                    "issuanceDate": "2026-03-27T12:00:00Z",
                    "expirationDate": "2027-03-27T12:00:00Z",
                    "revocationDate": None,
                    "serialNumber": "1234567890",
                },
            ],
        }
        certs = client.list_user_certs("jsmith")
        assert len(certs) == 1
        assert certs[0]["id"] == 42
        assert certs[0]["hash"] == "abc123"
        assert certs[0]["issuance_date"] == "2026-03-27T12:00:00Z"

    def test_returns_empty_for_unknown_user(self, client, mock_http):
        mock_http["get"].return_value = {"version": "3", "data": []}
        certs = client.list_user_certs("nobody")
        assert certs == []

    def test_returns_empty_on_http_error(self, client, mock_http):
        import httpx

        mock_http["get"].side_effect = httpx.HTTPError("connection failed")
        certs = client.list_user_certs("jsmith")
        assert certs == []


class TestRevokeCert:
    def test_revokes_by_id(self, client, mock_http):
        mock_http["delete"].return_value = None
        result = client.revoke_cert(42)
        assert result is True
        mock_http["delete"].assert_called_once_with("/Marti/api/certadmin/cert/revoke/42")

    def test_returns_false_on_failure(self, client, mock_http):
        import httpx

        mock_http["delete"].side_effect = httpx.HTTPError("failed")
        result = client.revoke_cert(42)
        assert result is False


class TestRevokeAllUserCerts:
    def test_revokes_all_active_certs(self, client, mock_http):
        mock_http["get"].return_value = {
            "data": [
                {
                    "id": 1,
                    "hash": "h1",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": None,
                    "serialNumber": "1",
                },
                {
                    "id": 2,
                    "hash": "h2",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": None,
                    "serialNumber": "2",
                },
            ],
        }
        mock_http["delete"].return_value = None
        result = client.revoke_all_user_certs("jsmith")
        assert result is True
        assert mock_http["delete"].call_count == 2

    def test_skips_already_revoked(self, client, mock_http):
        mock_http["get"].return_value = {
            "data": [
                {
                    "id": 1,
                    "hash": "h1",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": "2026-03-27T00:00:00Z",
                    "serialNumber": "1",
                },
            ],
        }
        result = client.revoke_all_user_certs("jsmith")
        assert result is True
        mock_http["delete"].assert_not_called()

    def test_returns_false_on_partial_failure(self, client, mock_http):
        import httpx

        mock_http["get"].return_value = {
            "data": [
                {
                    "id": 1,
                    "hash": "h1",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": None,
                    "serialNumber": "1",
                },
                {
                    "id": 2,
                    "hash": "h2",
                    "issuanceDate": None,
                    "expirationDate": None,
                    "revocationDate": None,
                    "serialNumber": "2",
                },
            ],
        }
        mock_http["delete"].side_effect = [None, httpx.HTTPError("fail")]
        result = client.revoke_all_user_certs("jsmith")
        assert result is False

    def test_returns_true_when_no_certs(self, client, mock_http):
        mock_http["get"].return_value = {"data": []}
        result = client.revoke_all_user_certs("jsmith")
        assert result is True

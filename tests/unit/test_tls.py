"""Tests for app.api.health.tls — TLS endpoint probing."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


class TestProbeTlsExpiry:
    @patch("app.api.health.tls.socket.create_connection")
    @patch("app.api.health.tls.ssl.create_default_context")
    def test_parses_cert(self, mock_ctx, mock_conn):
        future = datetime.now(UTC) + timedelta(days=90)
        expiry_str = future.strftime("%b %d %H:%M:%S %Y GMT")

        mock_ssock = MagicMock()
        mock_ssock.getpeercert.return_value = {
            "notAfter": expiry_str,
            "subject": ((("commonName", "*.example.com"),),),
        }
        mock_ctx.return_value.wrap_socket.return_value.__enter__ = MagicMock(
            return_value=mock_ssock
        )
        mock_ctx.return_value.wrap_socket.return_value.__exit__ = MagicMock(
            return_value=False
        )
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        from app.api.health.tls import _probe_tls_expiry

        result = _probe_tls_expiry("test.example.com")
        assert result is not None
        assert result["days_left"] >= 89
        assert result["status"] == "ok"

    @patch(
        "app.api.health.tls.socket.create_connection",
        side_effect=ConnectionRefusedError,
    )
    def test_returns_none_on_connection_error(self, mock_conn):
        from app.api.health.tls import _probe_tls_expiry

        assert _probe_tls_expiry("unreachable.example.com") is None


class TestGetTlsStatus:
    def test_returns_empty_for_localhost(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.tls.settings", mock_settings)
        mock_settings.fqdn = "localhost"

        from app.api.health.tls import get_tls_status

        assert get_tls_status() == []

    @patch("app.api.health.tls._probe_tls_expiry")
    def test_deduplicates_by_expiry(self, mock_probe, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.tls.settings", mock_settings)
        mock_settings.fqdn = "example.com"

        # All subdomains return the same wildcard cert expiry
        mock_probe.return_value = {
            "hostname": "takserver.example.com",
            "cn": "*.example.com",
            "expires": "2027-03-23T12:00:00+00:00",
            "days_left": 365,
            "status": "ok",
        }

        from app.api.health.tls import get_tls_status

        result = get_tls_status()
        # Should be deduplicated to 1 despite 5 endpoints probed
        assert len(result) == 1

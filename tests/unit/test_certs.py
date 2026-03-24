"""Tests for app.api.health.certs — certificate expiry parsing."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestParseCertExpiry:
    @patch("app.api.health.certs.subprocess.run")
    def test_parses_valid_cert(self, mock_run, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.certs.settings", mock_settings)
        future = datetime.now(UTC) + timedelta(days=365)
        expiry_str = future.strftime("%b %d %H:%M:%S %Y GMT")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"subject= CN = tak-server\nnotAfter={expiry_str}",
        )

        from app.api.health.certs import _parse_cert_expiry

        result = _parse_cert_expiry(Path("/fake/cert.pem"))
        assert result is not None
        assert result["days_left"] >= 364
        assert result["status"] == "ok"

    @patch("app.api.health.certs.subprocess.run")
    def test_expired_cert(self, mock_run, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.certs.settings", mock_settings)
        past = datetime.now(UTC) - timedelta(days=1)
        expiry_str = past.strftime("%b %d %H:%M:%S %Y GMT")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"subject= CN = test\nnotAfter={expiry_str}",
        )

        from app.api.health.certs import _parse_cert_expiry

        result = _parse_cert_expiry(Path("/fake/cert.pem"))
        assert result["status"] == "expired"

    @patch("app.api.health.certs.subprocess.run")
    def test_warning_threshold(self, mock_run, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.certs.settings", mock_settings)
        mock_settings.cert_warn_days = 30
        future = datetime.now(UTC) + timedelta(days=20)
        expiry_str = future.strftime("%b %d %H:%M:%S %Y GMT")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"subject= CN = test\nnotAfter={expiry_str}",
        )

        from app.api.health.certs import _parse_cert_expiry

        result = _parse_cert_expiry(Path("/fake/cert.pem"))
        assert result["status"] == "warning"

    @patch("app.api.health.certs.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        from app.api.health.certs import _parse_cert_expiry

        assert _parse_cert_expiry(Path("/fake/cert.pem")) is None


class TestGetCertStatus:
    @patch("app.api.health.certs.CERT_DIR", Path("/nonexistent/certs"))
    def test_returns_empty_when_dir_missing(self):
        from app.api.health.certs import get_cert_status

        assert get_cert_status() == []

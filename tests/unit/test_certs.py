"""Tests for app.api.health.certs — certificate expiry parsing."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestParseCertExpiry:
    @patch("app.api.health.certs.subprocess.run")
    def test_parses_valid_cert(self, mock_run):
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
        assert "status" not in result
        # expires is date-only: YYYY-MM-DD
        assert re.match(r"\d{4}-\d{2}-\d{2}$", result["expires"])

    @patch("app.api.health.certs.subprocess.run")
    def test_expired_cert_has_negative_days_left(self, mock_run):
        past = datetime.now(UTC) - timedelta(days=1)
        expiry_str = past.strftime("%b %d %H:%M:%S %Y GMT")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=f"subject= CN = test\nnotAfter={expiry_str}",
        )

        from app.api.health.certs import _parse_cert_expiry

        result = _parse_cert_expiry(Path("/fake/cert.pem"))
        assert result["days_left"] < 0

    @patch("app.api.health.certs.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        from app.api.health.certs import _parse_cert_expiry

        assert _parse_cert_expiry(Path("/fake/cert.pem")) is None


class TestGetCertStatus:
    @patch("app.api.health.certs.CERT_DIR", Path("/nonexistent/certs"))
    def test_returns_empty_when_dir_missing(self):
        from app.api.health.certs import get_cert_status

        result = get_cert_status()
        assert result == {"items": []}

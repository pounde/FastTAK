"""Tests for app.api.alerts.sms — Twilio/Brevo SMS sending."""

from unittest.mock import MagicMock, patch

from app.api.alerts.sms import send_alert_sms


class TestSendAlertSms:
    def test_returns_false_when_not_configured(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.alerts.sms.settings", mock_settings)
        mock_settings.sms_provider = ""
        mock_settings.sms_to = ""
        assert send_alert_sms("test") is False

    def test_returns_false_for_unknown_provider(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.alerts.sms.settings", mock_settings)
        mock_settings.sms_provider = "unknown"
        mock_settings.sms_to = "+15551234567"
        assert send_alert_sms("test") is False

    def test_twilio_sends_request(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.alerts.sms.settings", mock_settings)
        mock_settings.sms_provider = "twilio"
        mock_settings.sms_api_key = "ACXXX:authtoken"
        mock_settings.sms_from = "+15550001111"
        mock_settings.sms_to = "+15559876543"

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("app.api.alerts.sms.httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

            result = send_alert_sms("test message")
            assert result is True
            mock_client.post.assert_called_once()

    def test_twilio_bad_api_key_format(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.alerts.sms.settings", mock_settings)
        mock_settings.sms_provider = "twilio"
        mock_settings.sms_api_key = "no-colon-here"
        mock_settings.sms_to = "+15559876543"
        assert send_alert_sms("test") is False

    def test_brevo_sends_request(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.alerts.sms.settings", mock_settings)
        mock_settings.sms_provider = "brevo"
        mock_settings.sms_api_key = "brevo-api-key"
        mock_settings.sms_from = "FastTAK"
        mock_settings.sms_to = "+15559876543"

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("app.api.alerts.sms.httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

            result = send_alert_sms("test message")
            assert result is True
            mock_client.post.assert_called_once()
            # Verify Brevo-specific payload
            call_kwargs = mock_client.post.call_args
            assert call_kwargs.kwargs["headers"]["api-key"] == "brevo-api-key"

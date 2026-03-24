"""Tests for app.api.alerts.email — SMTP alert construction."""

from unittest.mock import MagicMock, patch

from app.api.alerts.email import send_alert_email


class TestSendAlertEmail:
    def test_returns_false_when_not_configured(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.alerts.email.settings", mock_settings)
        mock_settings.smtp_host = ""
        mock_settings.alert_email = ""
        assert send_alert_email("test", "body") is False

    def test_returns_false_when_no_recipient(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.alerts.email.settings", mock_settings)
        mock_settings.smtp_host = "smtp.example.com"
        mock_settings.alert_email = ""
        assert send_alert_email("test", "body") is False

    @patch("app.api.alerts.email.smtplib.SMTP")
    def test_sends_email(self, mock_smtp_class, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.alerts.email.settings", mock_settings)
        mock_settings.smtp_host = "smtp.example.com"
        mock_settings.smtp_port = 587
        mock_settings.smtp_user = "user"
        mock_settings.smtp_password = "pass"
        mock_settings.smtp_from = "from@example.com"
        mock_settings.alert_email = "ops@example.com"

        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = send_alert_email("Test Subject", "Test Body")
        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user", "pass")
        mock_server.send_message.assert_called_once()

    @patch("app.api.alerts.email.smtplib.SMTP", side_effect=ConnectionRefusedError)
    def test_returns_false_on_connection_error(
        self, mock_smtp, mock_settings, monkeypatch
    ):
        monkeypatch.setattr("app.api.alerts.email.settings", mock_settings)
        mock_settings.smtp_host = "smtp.example.com"
        mock_settings.alert_email = "ops@example.com"
        assert send_alert_email("test", "body") is False

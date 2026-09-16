"""Tests for /api/ops/* endpoints."""

from unittest.mock import MagicMock, patch


class TestOpsLogs:
    @patch("app.api.ops.router.discover_services", return_value=["tak-server"])
    @patch("app.api.ops.router.find_container")
    def test_returns_logs(self, mock_find, mock_discover, client):
        container = MagicMock()
        container.logs.return_value = b"2026-03-24T10:00:00Z some log line\n"
        mock_find.return_value = container

        resp = client.get("/api/ops/service/tak-server/logs?tail=10")
        assert resp.status_code == 200
        assert "logs" in resp.json()

    @patch("app.api.ops.router.discover_services", return_value=["tak-server"])
    def test_rejects_unknown_container(self, mock_discover, client):
        resp = client.get("/api/ops/service/evil-container/logs")
        assert resp.status_code == 400

    @patch("app.api.ops.router.discover_services", return_value=["tak-server"])
    @patch("app.api.ops.router.find_container", return_value=None)
    def test_returns_404_when_not_found(self, mock_find, mock_discover, client):
        resp = client.get("/api/ops/service/tak-server/logs")
        assert resp.status_code == 404


class TestOpsVacuum:
    @patch(
        "app.api.ops.router.vacuum_database",
        return_value={"success": True, "command": "VACUUM ANALYZE", "output": "VACUUM"},
    )
    def test_vacuum(self, mock_vacuum, client):
        resp = client.post("/api/ops/database/vacuum")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @patch(
        "app.api.ops.router.vacuum_database",
        return_value={
            "success": True,
            "command": "VACUUM FULL ANALYZE",
            "output": "VACUUM",
        },
    )
    def test_vacuum_full(self, mock_vacuum, client):
        resp = client.post("/api/ops/database/vacuum?full=true")
        assert resp.status_code == 200


class TestOpsAlertTest:
    @patch("app.api.ops.router.send_alert_email", return_value=True)
    def test_test_email(self, mock_email, client):
        resp = client.post("/api/ops/alerts/test-email")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @patch("app.api.ops.router.send_alert_sms", return_value=True)
    def test_test_sms(self, mock_sms, client):
        """A plain MagicMock, not an AsyncMock: send_alert_sms is sync and the
        AsyncMock was what let `await send_alert_sms(...)` pass in tests (#58)."""
        resp = client.post("/api/ops/alerts/test-sms")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_sms.assert_called_once()

    def test_test_sms_unconfigured_returns_false_not_500(self, client, mock_settings, monkeypatch):
        """No provider configured: the real function returns False without
        touching the network, and the endpoint must say so.

        Forces sms_provider = "" on the settings the SMS module reads so an
        SMS_PROVIDER in the runner's environment cannot turn this into a live
        call (matches tests/unit/test_sms.py's pattern)."""
        monkeypatch.setattr("app.api.alerts.sms.settings", mock_settings)
        mock_settings.sms_provider = ""
        resp = client.post("/api/ops/alerts/test-sms")
        assert resp.status_code == 200
        assert resp.json() == {"success": False}

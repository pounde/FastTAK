"""Tests for /api/ops/* endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch


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

    @patch("app.api.ops.router.send_alert_sms", new_callable=AsyncMock, return_value=True)
    def test_test_sms(self, mock_sms, client):
        resp = client.post("/api/ops/alerts/test-sms")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestOpsCerts:
    @patch("app.api.ops.certs.find_container")
    def test_list_certs(self, mock_find, client):
        container = MagicMock()
        container.exec_run.return_value = (
            0,
            b"-rw-r--r-- 1 root root 1234 admin.pem\n",
        )
        mock_find.return_value = container

        resp = client.get("/api/ops/certs/list")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @patch("app.api.ops.certs.find_container")
    def test_create_client_cert(self, mock_find, client):
        container = MagicMock()
        container.exec_run.return_value = (0, b"Certificate created")
        mock_find.return_value = container

        resp = client.post("/api/ops/certs/create-client/testuser")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_create_client_cert_invalid_name(self, client):
        resp = client.post("/api/ops/certs/create-client/; rm -rf /")
        assert resp.status_code == 400

    @patch("app.api.ops.certs.find_container")
    def test_revoke_cert(self, mock_find, client):
        container = MagicMock()
        container.exec_run.return_value = (0, b"Certificate revoked")
        mock_find.return_value = container

        resp = client.post("/api/ops/certs/revoke/testuser")
        assert resp.status_code == 200

    @patch("app.api.ops.certs.find_container", return_value=None)
    def test_cert_ops_fail_without_container(self, mock_find, client):
        resp = client.get("/api/ops/certs/list")
        assert resp.status_code == 500

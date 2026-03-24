"""Tests for /api/health/* endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestPing:
    def test_ping(self, client):
        resp = client.get("/api/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestDashboardSmoke:
    @patch("app.dashboard.services.get_service_links", return_value=[])
    def test_dashboard_loads(self, mock_links, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestHealthContainers:
    @patch("app.api.health.containers.discover_services", return_value=["tak-server"])
    @patch("app.api.health.containers.find_container")
    def test_returns_container_list(self, mock_find, mock_discover, client):
        from tests.conftest import make_fake_container

        mock_find.return_value = make_fake_container("tak-server")

        resp = client.get("/api/health/containers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["name"] == "tak-server"


class TestHealthResources:
    @patch("app.api.health.containers.find_container")
    @patch("app.api.health.router.discover_running_services", return_value=["tak-server"])
    def test_returns_stats(self, mock_discover, mock_find, client):
        container = MagicMock()
        container.status = "running"
        container.stats.return_value = {
            "cpu_stats": {
                "cpu_usage": {"total_usage": 100},
                "system_cpu_usage": 2000,
                "online_cpus": 1,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": 50},
                "system_cpu_usage": 1000,
            },
            "memory_stats": {"usage": 1000000, "limit": 5000000},
        }
        mock_find.return_value = container

        resp = client.get("/api/health/resources")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestHealthConfig:
    @patch(
        "app.api.health.router.check_config_drift",
        return_value={"status": "ok", "message": "unchanged"},
    )
    def test_returns_config_status(self, mock_drift, client):
        resp = client.get("/api/health/config")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestHealthCerts:
    @patch("app.api.health.certs.get_cert_status", return_value=[])
    def test_returns_cert_list(self, mock_certs, client):
        resp = client.get("/api/health/certs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestHealthDatabase:
    @patch(
        "app.api.health.router.get_cot_db_size",
        return_value={"size_bytes": 1000, "size_human": "1.0 KB", "status": "ok"},
    )
    def test_returns_db_size(self, mock_db, client):
        resp = client.get("/api/health/database")
        assert resp.status_code == 200
        assert "size_bytes" in resp.json()


class TestHealthDisk:
    @patch("app.api.health.disk.get_disk_usage", return_value=[])
    def test_returns_disk_usage(self, mock_disk, client):
        resp = client.get("/api/health/disk")
        assert resp.status_code == 200


class TestHealthUpdates:
    @patch("app.api.health.updates.check_updates", new_callable=AsyncMock, return_value=[])
    def test_returns_update_list(self, mock_updates, client):
        resp = client.get("/api/health/updates")
        assert resp.status_code == 200


class TestHealthTls:
    @patch("app.api.health.tls.get_tls_status", return_value=[])
    def test_returns_tls_status(self, mock_tls, client):
        resp = client.get("/api/health/tls")
        assert resp.status_code == 200

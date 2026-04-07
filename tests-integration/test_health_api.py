"""Health endpoint integration tests."""

import pytest

pytestmark = pytest.mark.integration


class TestHealthAPI:
    def test_ping(self, api):
        status, data = api("GET", "/api/ping")
        assert status == 200
        assert data["status"] == "ok"

    def test_containers(self, api):
        status, data = api("GET", "/api/health/containers")
        assert status == 200
        assert isinstance(data["items"], list)
        assert len(data["items"]) > 0

    def test_resources(self, api):
        status, data = api("GET", "/api/health/resources")
        assert status == 200
        assert isinstance(data, list)

    def test_certs(self, api):
        status, data = api("GET", "/api/health/certs")
        assert status == 200
        assert isinstance(data["items"], list)

    def test_database(self, api):
        status, data = api("GET", "/api/health/database")
        assert status == 200
        assert "size_bytes" in data
        assert "live_bytes" in data

    def test_disk(self, api):
        status, data = api("GET", "/api/health/disk")
        assert status == 200
        assert isinstance(data["items"], list)

    def test_config(self, api):
        status, data = api("GET", "/api/health/config")
        assert status == 200
        assert "changed" in data

    def test_logs(self, api):
        status, data = api("GET", "/api/ops/service/tak-server/logs?tail=10")
        assert status == 200
        assert "logs" in data
        assert len(data["logs"]) > 0

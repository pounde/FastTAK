"""Tests for app.api.health.containers — health aggregation and stats."""

from unittest.mock import MagicMock, patch

from tests.conftest import make_fake_container


class TestGetAllContainerHealth:
    @patch(
        "app.api.health.containers.discover_services",
        return_value=["tak-server", "tak-database"],
    )
    @patch("app.api.health.containers.find_container")
    def test_returns_health_for_all_services(self, mock_find, mock_discover):
        mock_find.side_effect = [
            make_fake_container("tak-server", status="running", health="healthy"),
            make_fake_container("tak-database", status="running", health="healthy"),
        ]
        from app.api.health.containers import get_all_container_health

        result = get_all_container_health()
        assert len(result) == 2
        assert result[0]["name"] == "tak-server"
        assert result[0]["status"] == "running"
        assert result[0]["health"] == "healthy"

    @patch("app.api.health.containers.discover_services", return_value=["missing-svc"])
    @patch("app.api.health.containers.find_container", return_value=None)
    def test_handles_missing_container(self, mock_find, mock_discover):
        from app.api.health.containers import get_all_container_health

        result = get_all_container_health()
        assert result[0]["status"] == "not_found"
        assert result[0]["health"] == "unknown"

    @patch("app.api.health.containers.discover_services", return_value=["mediamtx"])
    @patch("app.api.health.containers.find_container")
    def test_handles_no_healthcheck(self, mock_find, mock_discover):
        mock_find.return_value = make_fake_container("mediamtx", status="running", health="none")
        from app.api.health.containers import get_all_container_health

        result = get_all_container_health()
        assert result[0]["health"] == "unknown"


class TestGetContainerStats:
    def _make_stats(
        self,
        cpu_total=100000,
        precpu_total=50000,
        sys_cpu=2000000,
        presys_cpu=1000000,
        online_cpus=2,
        mem_usage=100_000_000,
        mem_limit=500_000_000,
    ):
        return {
            "cpu_stats": {
                "cpu_usage": {"total_usage": cpu_total},
                "system_cpu_usage": sys_cpu,
                "online_cpus": online_cpus,
            },
            "precpu_stats": {
                "cpu_usage": {"total_usage": precpu_total},
                "system_cpu_usage": presys_cpu,
            },
            "memory_stats": {
                "usage": mem_usage,
                "limit": mem_limit,
            },
        }

    @patch("app.api.health.containers.find_container")
    def test_calculates_cpu_and_memory(self, mock_find):
        container = MagicMock()
        container.status = "running"
        container.stats.return_value = self._make_stats()
        mock_find.return_value = container

        from app.api.health.containers import get_container_stats

        result = get_container_stats("tak-server")
        assert result is not None
        assert result["name"] == "tak-server"
        assert result["cpu_percent"] > 0
        assert result["memory_mb"] > 0
        assert result["memory_percent"] == 20.0

    @patch("app.api.health.containers.find_container", return_value=None)
    def test_returns_none_when_not_found(self, mock_find):
        from app.api.health.containers import get_container_stats

        assert get_container_stats("nonexistent") is None

    @patch("app.api.health.containers.find_container")
    def test_returns_none_for_stopped_container(self, mock_find):
        container = MagicMock()
        container.status = "exited"
        mock_find.return_value = container

        from app.api.health.containers import get_container_stats

        assert get_container_stats("stopped") is None

    @patch("app.api.health.containers.find_container")
    def test_handles_zero_system_delta(self, mock_find):
        container = MagicMock()
        container.status = "running"
        container.stats.return_value = self._make_stats(sys_cpu=1000000, presys_cpu=1000000)
        mock_find.return_value = container

        from app.api.health.containers import get_container_stats

        result = get_container_stats("tak-server")
        assert result["cpu_percent"] == 0.0

    @patch("app.api.health.containers.find_container")
    def test_returns_none_on_missing_stats_keys(self, mock_find):
        container = MagicMock()
        container.status = "running"
        container.stats.return_value = {"cpu_stats": {}}
        mock_find.return_value = container

        from app.api.health.containers import get_container_stats

        assert get_container_stats("tak-server") is None

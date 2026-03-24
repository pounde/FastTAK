"""Tests for app.docker_client — service discovery and caching."""

import time
from unittest.mock import MagicMock

from app import docker_client


def _reset_cache():
    docker_client._cached_services = []
    docker_client._cache_time = 0


def _make_container(service, project="fastak", status="running"):
    c = MagicMock()
    c.labels = {
        "com.docker.compose.service": service,
        "com.docker.compose.project": project,
    }
    c.status = status
    return c


class TestDiscoverServices:
    def setup_method(self):
        _reset_cache()

    def test_discovers_from_labels(self, mock_docker_client):
        monitor = _make_container("monitor")
        tak = _make_container("tak-server")
        db = _make_container("tak-database")

        mock_docker_client.containers.list.side_effect = [
            [monitor],
            [monitor, tak, db],
        ]

        result = docker_client.discover_services()
        assert "monitor" in result
        assert "tak-server" in result
        assert "tak-database" in result
        assert result == sorted(result)

    def test_cache_returns_within_ttl(self, mock_docker_client):
        docker_client._cached_services = ["cached-svc"]
        docker_client._cache_time = time.monotonic()

        result = docker_client.discover_services()
        assert result == ["cached-svc"]
        mock_docker_client.containers.list.assert_not_called()

    def test_cache_expires_after_ttl(self, mock_docker_client):
        docker_client._cached_services = ["old-svc"]
        docker_client._cache_time = time.monotonic() - docker_client._CACHE_TTL - 1

        monitor = _make_container("monitor")
        mock_docker_client.containers.list.side_effect = [
            [monitor],
            [monitor, _make_container("new-svc")],
        ]

        result = docker_client.discover_services()
        assert "new-svc" in result
        assert "old-svc" not in result

    def test_returns_empty_when_monitor_not_found(self, mock_docker_client):
        mock_docker_client.containers.list.return_value = []
        result = docker_client.discover_services()
        assert result == []


class TestDiscoverRunningServices:
    def setup_method(self):
        _reset_cache()

    def test_filters_to_running(self, mock_docker_client, monkeypatch):
        docker_client._cached_services = ["running-svc", "stopped-svc"]
        docker_client._cache_time = time.monotonic()

        running = MagicMock()
        running.status = "running"
        stopped = MagicMock()
        stopped.status = "exited"

        def fake_find(name):
            if name == "running-svc":
                return running
            return stopped

        monkeypatch.setattr(docker_client, "find_container", fake_find)

        result = docker_client.discover_running_services()
        assert result == ["running-svc"]


class TestFindContainer:
    def test_returns_container(self, mock_docker_client):
        container = MagicMock()
        mock_docker_client.containers.list.return_value = [container]
        result = docker_client.find_container("tak-server")
        assert result is container

    def test_returns_none_when_not_found(self, mock_docker_client):
        mock_docker_client.containers.list.return_value = []
        result = docker_client.find_container("nonexistent")
        assert result is None

"""Tests for app.scheduler — health check job logic."""

from unittest.mock import AsyncMock, patch


class TestCheckContainers:
    @patch("app.scheduler.check_and_alert", new_callable=AsyncMock)
    @patch("app.scheduler.get_all_container_health")
    def test_maps_health_to_state(self, mock_health, mock_alert):
        mock_health.return_value = [
            {"name": "tak-server", "status": "running", "health": "healthy"},
            {"name": "mediamtx", "status": "running", "health": "unknown"},
        ]

        from app.scheduler import _check_containers

        _check_containers()

        calls = mock_alert.call_args_list
        assert len(calls) == 2
        # tak-server: health is not "unknown", so state = "healthy"
        assert calls[0].kwargs["new_state"] == "healthy"
        # mediamtx: health is "unknown", so state falls back to "running"
        assert calls[1].kwargs["new_state"] == "running"

    @patch("app.scheduler.check_and_alert", new_callable=AsyncMock)
    @patch("app.scheduler.get_all_container_health")
    def test_passes_service_names(self, mock_health, mock_alert):
        mock_health.return_value = [
            {"name": "caddy", "status": "running", "health": "healthy"},
        ]

        from app.scheduler import _check_containers

        _check_containers()

        mock_alert.assert_called_once()
        assert mock_alert.call_args.kwargs["service"] == "caddy"

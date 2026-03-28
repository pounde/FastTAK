"""Tests for app.scheduler — health check job logic."""

from unittest.mock import MagicMock, patch


class TestPoll:
    """Tests for the generic _poll function."""

    def test_calls_health_fn_and_updates_store(self):
        raw = {"size_bytes": 1000}
        health_fn = MagicMock(return_value=raw)
        service_config = {
            "thresholds": {"size_bytes": {"warning": 5000, "critical": 10000}},
            "alert_min_level": "warning",
        }

        with (
            patch("app.scheduler.store") as mock_store,
            patch("app.scheduler.evaluate", return_value={"status": "ok"}) as mock_eval,
            patch("app.scheduler.check_and_alert") as mock_alert,
        ):
            from app.scheduler import _poll

            _poll("database", health_fn, service_config)

        health_fn.assert_called_once()
        mock_eval.assert_called_once_with("database", raw, service_config)
        mock_store.update.assert_called_once()
        # evaluated["should_alert"] is absent/False, so no alert
        mock_alert.assert_not_called()

    def test_handles_health_fn_exception(self):
        health_fn = MagicMock(side_effect=RuntimeError("connection refused"))
        service_config = {"thresholds": {}, "alert_min_level": "warning"}

        with (
            patch("app.scheduler.store") as mock_store,
            patch("app.scheduler.evaluate") as mock_eval,
        ):
            from app.scheduler import _poll

            _poll("database", health_fn, service_config)

        # Should store critical status with the error message
        mock_store.update.assert_called_once()
        call_args = mock_store.update.call_args
        assert call_args[0][2]["status"] == "critical"
        assert "connection refused" in call_args[0][2]["message"]
        mock_eval.assert_not_called()

    def test_handles_raw_error_key(self):
        health_fn = MagicMock(return_value={"error": "db unavailable"})
        service_config = {"thresholds": {}, "alert_min_level": "warning"}

        with (
            patch("app.scheduler.store") as mock_store,
            patch("app.scheduler.evaluate") as mock_eval,
        ):
            from app.scheduler import _poll

            _poll("database", health_fn, service_config)

        mock_store.update.assert_called_once()
        call_args = mock_store.update.call_args
        assert call_args[0][2]["status"] == "critical"
        mock_eval.assert_not_called()

    def test_alerts_when_status_meets_min_level(self):
        raw = {"size_bytes": 30000000000}
        health_fn = MagicMock(return_value=raw)
        service_config = {
            "thresholds": {"size_bytes": {"warning": 25000000000, "critical": 40000000000}},
            "alert_min_level": "warning",
        }

        with (
            patch("app.scheduler.store"),
            patch(
                "app.scheduler.evaluate",
                return_value={
                    "status": "warning",
                    "message": "size_bytes is high",
                    "should_alert": True,
                },
            ),
            patch("app.scheduler.check_and_alert") as mock_alert,
        ):
            from app.scheduler import _poll

            _poll("database", health_fn, service_config)

        mock_alert.assert_called_once_with("database", "warning", "size_bytes is high")

    def test_no_alert_below_min_level(self):
        raw = {"update_available": False}
        health_fn = MagicMock(return_value=raw)
        # alert_min_level is "note" but evaluated status is "ok"
        service_config = {
            "thresholds": {},
            "alert_min_level": "note",
        }

        with (
            patch("app.scheduler.store"),
            patch("app.scheduler.evaluate", return_value={"status": "ok", "should_alert": False}),
            patch("app.scheduler.check_and_alert") as mock_alert,
        ):
            from app.scheduler import _poll

            _poll("updates", health_fn, service_config)

        mock_alert.assert_not_called()


class TestStartScheduler:
    def test_registers_jobs_for_all_health_services(self):
        mock_scheduler = MagicMock()
        config = {
            "database": {"interval": 60, "alert_min_level": "warning", "thresholds": {}},
            "disk": {"interval": 300, "alert_min_level": "warning", "thresholds": {}},
        }

        with (
            patch("app.scheduler.scheduler", mock_scheduler),
            patch("app.scheduler.load_config", return_value=config),
            patch("app.scheduler._poll"),
            patch("app.scheduler.settings") as mock_settings,
        ):
            mock_settings.authentik_api_token = None
            from app.scheduler import _HEALTH_FUNCTIONS, start_scheduler

            start_scheduler()

        # One job per health service in _HEALTH_FUNCTIONS
        add_job_ids = [call.kwargs["id"] for call in mock_scheduler.add_job.call_args_list]
        for name in _HEALTH_FUNCTIONS:
            assert name in add_job_ids

    def test_adds_user_expiry_when_authentik_configured(self):
        mock_scheduler = MagicMock()

        with (
            patch("app.scheduler.scheduler", mock_scheduler),
            patch("app.scheduler.load_config", return_value={}),
            patch("app.scheduler._poll"),
            patch("app.scheduler.settings") as mock_settings,
        ):
            mock_settings.authentik_api_token = "some-token"
            mock_settings.user_expiry_check_interval = 300
            from app.scheduler import start_scheduler

            start_scheduler()

        add_job_ids = [call.kwargs["id"] for call in mock_scheduler.add_job.call_args_list]
        assert "user_expiry" in add_job_ids

    def test_skips_user_expiry_without_authentik(self):
        mock_scheduler = MagicMock()

        with (
            patch("app.scheduler.scheduler", mock_scheduler),
            patch("app.scheduler.load_config", return_value={}),
            patch("app.scheduler._poll"),
            patch("app.scheduler.settings") as mock_settings,
        ):
            mock_settings.authentik_api_token = None
            from app.scheduler import start_scheduler

            start_scheduler()

        add_job_ids = [call.kwargs["id"] for call in mock_scheduler.add_job.call_args_list]
        assert "user_expiry" not in add_job_ids

    def test_does_not_crash(self):
        """Smoke test: start_scheduler completes without raising."""
        mock_scheduler = MagicMock()

        with (
            patch("app.scheduler.scheduler", mock_scheduler),
            patch("app.scheduler.load_config", return_value={}),
            patch("app.scheduler._poll"),
            patch("app.scheduler.settings") as mock_settings,
        ):
            mock_settings.authentik_api_token = None
            from app.scheduler import start_scheduler

            start_scheduler()  # Should not raise

"""Tests for app.scheduler — health check job logic."""

import time
from unittest.mock import MagicMock, patch

from app.api.alerts import engine


class TestPollRealEvaluatorAndEngine:
    """Regression test for #77 through the real evaluator -> real engine path."""

    def setup_method(self):
        with engine._lock:
            engine._last_state.clear()
            engine._last_alert_time.clear()

    def test_real_evaluator_and_engine_record_a_recovery_and_a_repeat(self):
        """Drives _poll with the real evaluator and the real alert engine.

        This is the test that would have caught #77 as a class: with the old
        call pattern (engine invoked only when alerting), the second warning
        below would have been deduplicated against a _last_state pinned at
        "warning" by the first, and the intervening recovery would never
        have been recorded.
        """
        from app.monitoring_config import load_config
        from app.scheduler import _poll

        config = load_config()
        service_config = config.get("tls", {})

        def _tls_raw(days_left):
            return {
                "items": [
                    {
                        "domain": "takserver.example.com",
                        "expires": "2026-12-01",
                        "days_left": days_left,
                    }
                ]
            }

        health_fn = MagicMock(
            side_effect=[_tls_raw(90), _tls_raw(30), _tls_raw(30), _tls_raw(90), _tls_raw(30)]
        )

        with (
            patch("app.scheduler.store"),
            patch("app.audit.record_event") as mock_audit,
            patch("app.api.alerts.engine.send_alert_email", return_value=True) as mock_email,
            patch("app.api.alerts.engine.send_alert_sms", return_value=True),
        ):
            for _ in range(4):
                _poll("tls", health_fn, service_config)

            with engine._lock:
                engine._last_alert_time["tls"] = time.time() - engine.alert_cooldown - 1

            _poll("tls", health_fn, service_config)

        actions = [c.kwargs["action"] for c in mock_audit.call_args_list]
        assert actions == ["warning", "ok", "recovered", "warning"]
        assert mock_email.call_count == 2


class TestPoll:
    """Tests for the generic _poll function."""

    def test_calls_health_fn_and_updates_store(self):
        """The engine now sees every poll (#77): evaluated["should_alert"]
        absent/False still reaches check_and_alert, just with should_alert=False."""
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
        mock_alert.assert_called_once_with("database", "ok", "", should_alert=False)

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

        mock_alert.assert_called_once_with(
            "database", "warning", "size_bytes is high", should_alert=True
        )

    def test_below_min_level_still_reaches_the_engine(self):
        """The engine tracks state on every poll (#77): a below-threshold
        status is passed through with should_alert=False, not withheld."""
        raw = {"update_available": False}
        health_fn = MagicMock(return_value=raw)
        service_config = {"thresholds": {}, "alert_min_level": "note"}

        with (
            patch("app.scheduler.store"),
            patch("app.scheduler.evaluate", return_value={"status": "ok", "should_alert": False}),
            patch("app.scheduler.check_and_alert") as mock_alert,
        ):
            from app.scheduler import _poll

            _poll("updates", health_fn, service_config)

        mock_alert.assert_called_once_with("updates", "ok", "", should_alert=False)

    def test_error_branch_alerts_explicitly(self):
        health_fn = MagicMock(return_value={"error": "connection refused"})
        with (
            patch("app.scheduler.store"),
            patch("app.scheduler.check_and_alert") as mock_alert,
        ):
            from app.scheduler import _poll

            _poll("database", health_fn, {"thresholds": {}})
        mock_alert.assert_called_once_with(
            "database", "critical", "connection refused", should_alert=True
        )


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
            mock_settings.ldap_admin_password = None
            from app.scheduler import _HEALTH_FUNCTIONS, start_scheduler

            start_scheduler()

        # One job per health service in _HEALTH_FUNCTIONS
        add_job_ids = [call.kwargs["id"] for call in mock_scheduler.add_job.call_args_list]
        for name in _HEALTH_FUNCTIONS:
            assert name in add_job_ids

    def test_adds_user_expiry_when_identity_configured(self):
        mock_scheduler = MagicMock()

        with (
            patch("app.scheduler.scheduler", mock_scheduler),
            patch("app.scheduler.load_config", return_value={}),
            patch("app.scheduler._poll"),
            patch("app.scheduler.settings") as mock_settings,
        ):
            mock_settings.ldap_admin_password = "some-token"
            mock_settings.user_expiry_check_interval = 300
            from app.scheduler import start_scheduler

            start_scheduler()

        add_job_ids = [call.kwargs["id"] for call in mock_scheduler.add_job.call_args_list]
        assert "user_expiry" in add_job_ids

    def test_skips_user_expiry_without_identity(self):
        mock_scheduler = MagicMock()

        with (
            patch("app.scheduler.scheduler", mock_scheduler),
            patch("app.scheduler.load_config", return_value={}),
            patch("app.scheduler._poll"),
            patch("app.scheduler.settings") as mock_settings,
        ):
            mock_settings.ldap_admin_password = None
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
            mock_settings.ldap_admin_password = None
            from app.scheduler import start_scheduler

            start_scheduler()  # Should not raise

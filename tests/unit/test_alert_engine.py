"""Tests for app.api.alerts.engine — state transitions and deduplication."""

import time
from unittest.mock import patch

from app.api.alerts import engine


def _reset_engine():
    """Clear engine state between tests."""
    with engine._lock:
        engine._last_state.clear()
        engine._last_alert_time.clear()
        engine._activity_log.clear()


class TestRecordEvent:
    def setup_method(self):
        _reset_engine()

    def test_records_event(self):
        engine.record_event("test-svc", "critical", "something broke")
        log = engine.get_activity_log()
        assert len(log) == 1
        assert log[0]["source"] == "test-svc"
        assert log[0]["level"] == "critical"

    def test_caps_at_max(self):
        for i in range(engine.MAX_LOG_ENTRIES + 50):
            engine.record_event("svc", "note", f"event {i}")
        assert len(engine.get_activity_log(limit=1000)) == engine.MAX_LOG_ENTRIES

    def test_newest_first(self):
        engine.record_event("svc", "note", "first")
        engine.record_event("svc", "note", "second")
        log = engine.get_activity_log()
        assert log[0]["message"] == "second"

    def test_limit(self):
        for i in range(10):
            engine.record_event("svc", "note", f"event {i}")
        assert len(engine.get_activity_log(limit=3)) == 3


class TestCheckAndAlert:
    def setup_method(self):
        _reset_engine()

    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_no_alert_on_same_state(self, mock_email, mock_sms):
        engine.check_and_alert("svc", "ok", "all good")
        engine.check_and_alert("svc", "ok", "still good")
        mock_email.assert_not_called()
        mock_sms.assert_not_called()

    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_alerts_on_transition_to_critical(self, mock_email, mock_sms):
        engine.check_and_alert("svc", "ok")
        engine.check_and_alert("svc", "critical", "container down")
        mock_email.assert_called_once()
        mock_sms.assert_called_once()

    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_alerts_on_transition_to_warning(self, mock_email, mock_sms):
        engine.check_and_alert("svc", "ok")
        engine.check_and_alert("svc", "warning", "degraded")
        mock_email.assert_called_once()
        mock_sms.assert_called_once()

    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_no_alert_on_note(self, mock_email, mock_sms):
        engine.check_and_alert("svc", "ok")
        engine.check_and_alert("svc", "note", "minor info")
        mock_email.assert_not_called()
        mock_sms.assert_not_called()

    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_recovery_logged_not_alerted(self, mock_email, mock_sms):
        engine.check_and_alert("svc", "critical")
        mock_email.reset_mock()
        mock_sms.reset_mock()

        engine.check_and_alert("svc", "ok")
        mock_email.assert_not_called()
        log = engine.get_activity_log()
        assert any("recovered" in e["level"] for e in log)

    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_cooldown_prevents_repeat_alert(self, mock_email, mock_sms):
        engine.check_and_alert("svc", "critical")
        mock_email.reset_mock()

        # Transition back and forth within cooldown
        engine.check_and_alert("svc", "ok")
        engine.check_and_alert("svc", "critical")
        mock_email.assert_not_called()

    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_alert_after_cooldown_expires(self, mock_email, mock_sms):
        engine.check_and_alert("svc", "critical")
        mock_email.reset_mock()

        # Expire the cooldown
        with engine._lock:
            engine._last_alert_time["svc"] = time.time() - engine.ALERT_COOLDOWN - 1

        engine.check_and_alert("svc", "ok")
        engine.check_and_alert("svc", "critical")
        mock_email.assert_called_once()

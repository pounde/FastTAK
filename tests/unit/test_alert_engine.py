"""Tests for app.api.alerts.engine — state transitions and deduplication."""

import time
from datetime import UTC, datetime
from unittest.mock import patch

from app.api.alerts import engine


def _reset_engine():
    """Clear engine state between tests."""
    with engine._lock:
        engine._last_state.clear()
        engine._last_alert_time.clear()


class TestRecordEvent:
    def setup_method(self):
        _reset_engine()

    def test_records_event(self):
        with patch("app.audit.record_event") as mock_audit:
            engine.record_event("test-svc", "critical", "something broke")
        mock_audit.assert_called_once_with(
            source="health",
            actor="system",
            action="critical",
            target_type="service",
            target_id="test-svc",
            detail={"message": "something broke"},
        )

    def test_limit_passed_to_fetch(self):
        fake_rows = [
            {
                "timestamp": datetime(2026, 4, 27, 12, tzinfo=UTC),
                "source": "svc",
                "level": "note",
                "detail": {"message": f"event {i}"},
            }
            for i in range(3)
        ]
        with patch("app.fastak_db.fetch", return_value=fake_rows) as mock_fetch:
            result = engine.get_activity_log(limit=3)
        assert mock_fetch.call_args.args[1] == (3,)
        assert len(result) == 3


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
    def test_alerts_on_note_transition(self, mock_email, mock_sms):
        # Engine fires on any non-ok transition — alert_min_level filtering is
        # the scheduler's job (via evaluator.should_alert), not the engine's.
        engine.check_and_alert("svc", "ok")
        engine.check_and_alert("svc", "note", "minor info")
        mock_email.assert_called_once()
        mock_sms.assert_called_once()

    @patch("app.audit.record_event")
    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_recovery_logged_not_alerted(self, mock_email, mock_sms, mock_audit):
        engine.check_and_alert("svc", "critical")
        mock_email.reset_mock()
        mock_sms.reset_mock()
        mock_audit.reset_mock()

        engine.check_and_alert("svc", "ok")
        mock_email.assert_not_called()
        # Recovery transition emits two audit calls: one for the new "ok" state,
        # then one with action="recovered".
        actions = [c.kwargs["action"] for c in mock_audit.call_args_list]
        assert "recovered" in actions

    @patch("app.audit.record_event")
    @patch("app.api.alerts.engine.send_alert_sms", return_value=True)
    @patch("app.api.alerts.engine.send_alert_email", return_value=True)
    def test_recovery_no_alert_but_logged(self, mock_email, mock_sms, mock_audit):
        """Recovery (elevated → ok) must not send alerts but must appear in the log."""
        engine.check_and_alert("svc", "critical", "went down")
        mock_email.reset_mock()
        mock_sms.reset_mock()
        mock_audit.reset_mock()

        engine.check_and_alert("svc", "ok")
        mock_email.assert_not_called()
        mock_sms.assert_not_called()
        actions = [c.kwargs["action"] for c in mock_audit.call_args_list]
        assert "recovered" in actions

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
            engine._last_alert_time["svc"] = time.time() - engine.alert_cooldown - 1

        engine.check_and_alert("svc", "ok")
        engine.check_and_alert("svc", "critical")
        mock_email.assert_called_once()

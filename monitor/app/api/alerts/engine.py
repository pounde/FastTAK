"""Alert engine — detects state transitions and deduplicates alerts.

Called from scheduler threads and potentially from API endpoints.
All alert sending is synchronous (no async I/O).
"""

import threading
import time
from collections import defaultdict

from app.api.alerts.email import send_alert_email
from app.api.alerts.sms import send_alert_sms

_lock = threading.Lock()

# Track last known state per service
_last_state: dict[str, str] = {}
# Debounce: don't re-alert within this window (seconds).
# Default 300s, overridden by alert_cooldown in thresholds.yml at startup.
alert_cooldown: int = 300
_last_alert_time: dict[str, float] = defaultdict(float)

# Activity log (in-memory, capped)
_activity_log: list[dict] = []
MAX_LOG_ENTRIES = 500


def record_event(source: str, level: str, message: str):
    """Record an event to the activity log."""
    entry = {"time": time.time(), "source": source, "level": level, "message": message}
    with _lock:
        _activity_log.insert(0, entry)
        if len(_activity_log) > MAX_LOG_ENTRIES:
            _activity_log.pop()


def get_activity_log(limit: int = 50) -> list[dict]:
    with _lock:
        return _activity_log[:limit]


def check_and_alert(service: str, new_state: str, detail: str = ""):
    """Alert on state transitions. Handles deduplication and cooldown only.

    The evaluator decides whether a state warrants alerting (via should_alert
    and alert_min_level in thresholds.yml). The scheduler only calls this
    function when the evaluator says to. This function does not filter by
    severity — if called, it sends (subject to deduplication and cooldown).

    Recovery (transition from elevated state back to ok) is logged but does
    not send alerts.

    Thread-safe: all shared state access is under _lock for the full
    read-compare-update cycle (no TOCTOU gap).
    """
    now = time.time()

    with _lock:
        old_state = _last_state.get(service)
        _last_state[service] = new_state

        if old_state == new_state:
            return  # No change — deduplication

        should_alert = False
        is_recovery = False

        if new_state != "ok":
            # State transition to a non-ok level — alert if not in cooldown
            if (now - _last_alert_time[service]) >= alert_cooldown:
                _last_alert_time[service] = now
                should_alert = True
        elif old_state is not None and old_state != "ok":
            is_recovery = True

    # Record and send outside the lock (IO operations)
    record_event(service, new_state, detail or f"{service}: {old_state} → {new_state}")

    if should_alert:
        subject = f"{service} is {new_state}"
        body = f"Service: {service}\nState: {old_state} → {new_state}\n{detail}"
        send_alert_email(subject, body)
        send_alert_sms(f"[FastTAK] {subject}")
    elif is_recovery:
        record_event(service, "recovered", f"{service} recovered: {old_state} → {new_state}")

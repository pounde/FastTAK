"""Alert engine — detects state transitions and deduplicates alerts.

Called from scheduler threads and potentially from API endpoints.
All alert sending is synchronous (no async I/O).
"""

import threading
import time
from collections import defaultdict

from app.api.alerts.email import send_alert_email
from app.api.alerts.sms import send_alert_sms
from app.status import Status

_lock = threading.Lock()

# Track last known state per service
_last_state: dict[str, str] = {}
# Debounce: don't re-alert within this window (seconds)
ALERT_COOLDOWN = 300
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
    """Compare against last known state. Alert on transition to warning or above.

    Thread-safe: all shared state access is under _lock for the full
    read-compare-update cycle (no TOCTOU gap).
    """
    now = time.time()

    with _lock:
        old_state = _last_state.get(service)
        _last_state[service] = new_state

        if old_state == new_state:
            return  # No change

        # Determine if we should alert (inside the lock to prevent races)
        should_alert = False
        is_recovery = False

        # Alert on any state that is warning or above
        if Status[new_state] >= Status.warning:
            if (now - _last_alert_time[service]) >= ALERT_COOLDOWN:
                _last_alert_time[service] = now
                should_alert = True
        elif old_state is not None and Status[old_state] >= Status.warning:
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

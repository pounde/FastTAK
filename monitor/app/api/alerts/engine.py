"""Alert engine — detects state transitions and deduplicates alerts.

Called from scheduler threads and potentially from API endpoints. The
scheduler calls this on every poll, passing the evaluator's should_alert
verdict as a keyword argument, so state tracking (and recovery detection)
stays accurate even for polls the evaluator judges below alert_min_level.
All alert sending is synchronous (no async I/O).
"""

import logging
import threading
import time
from collections import defaultdict

from app.api.alerts.email import send_alert_email
from app.api.alerts.sms import send_alert_sms

log = logging.getLogger(__name__)

_lock = threading.Lock()

# Track last known state per service
_last_state: dict[str, str] = {}
# Debounce: don't re-alert within this window (seconds).
# Default 300s, overridden by alert_cooldown in thresholds.yml at startup.
alert_cooldown: int = 300
_last_alert_time: dict[str, float] = defaultdict(float)


def record_event(source: str, level: str, message: str):
    """Record a health-event entry to fastak_events.

    Fire-and-forget: a DB hiccup never breaks the alerter (audit.record_event
    swallows exceptions internally).
    """
    from app.audit import record_event as _audit_record

    _audit_record(
        source="health",
        actor="system",
        action=level,
        target_type="service",
        target_id=source,
        detail={"message": message},
    )


def get_activity_log(limit: int = 50) -> list[dict]:
    """Return recent health events from fastak_events.

    Matches the prior shape: list of {time, source, level, message}.
    """
    from app.fastak_db import fetch

    try:
        rows = fetch(
            """
            SELECT timestamp, target_id AS source, action AS level, detail
            FROM fastak_events
            WHERE source = 'health'
            ORDER BY timestamp DESC
            LIMIT %s
            """,
            (limit,),
        )
    except Exception:
        log.exception("Failed to read health activity log from fastak_events")
        return []
    return [
        {
            "time": r["timestamp"].timestamp(),
            "source": r["source"],
            "level": r["level"],
            "message": (r["detail"] or {}).get("message", ""),
        }
        for r in rows
    ]


def check_and_alert(service: str, new_state: str, detail: str = "", *, should_alert: bool = True):
    """Track state on every call; notify only on a transition worth alerting.

    The scheduler calls this on every poll and passes the evaluator's verdict
    as `should_alert`, so `_last_state` follows reality: a recovery (elevated
    → ok) is recorded even though ok is below alert_min_level, and a
    condition that recurs after clearing alerts again (#77). Notifications
    go out only on a transition to a non-ok state with `should_alert` true
    and the cooldown elapsed. Recovery is logged, never sent.

    The first observation of a healthy service (no prior state, new state ok)
    is not an event — every poll reaches here now, and a monitor restart
    must not log one 'ok' row per service.

    Thread-safe: all shared state access is under _lock for the full
    read-compare-update cycle (no TOCTOU gap).
    """
    now = time.time()

    with _lock:
        old_state = _last_state.get(service)
        _last_state[service] = new_state

        if old_state == new_state:
            return  # No change — deduplication
        if old_state is None and new_state == "ok":
            return  # First sight of a healthy service: nothing happened

        notify = False
        is_recovery = False

        if new_state != "ok":
            if should_alert and (now - _last_alert_time[service]) >= alert_cooldown:
                _last_alert_time[service] = now
                notify = True
        elif old_state is not None and old_state != "ok":
            is_recovery = True

    # Record and send outside the lock (IO operations)
    record_event(service, new_state, detail or f"{service}: {old_state} → {new_state}")

    if notify:
        subject = f"{service} is {new_state}"
        body = f"Service: {service}\nState: {old_state} → {new_state}\n{detail}"
        send_alert_email(subject, body)
        send_alert_sms(f"[FastTAK] {subject}")
    elif is_recovery:
        record_event(service, "recovered", f"{service} recovered: {old_state} → {new_state}")

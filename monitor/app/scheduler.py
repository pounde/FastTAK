"""Background scheduler for periodic health checks and alerting.

Uses BackgroundScheduler (thread-based), not AsyncIOScheduler, because the
health check functions call blocking Docker SDK / subprocess operations.
AsyncIOScheduler would run these on the event loop and stall all async
endpoints. BackgroundScheduler runs each job in its own thread.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app import store
from app.api.alerts.engine import check_and_alert
from app.api.health.autovacuum import get_autovacuum_health
from app.api.health.certs import get_cert_status
from app.api.health.config_drift import check_config_drift
from app.api.health.containers import get_all_container_health
from app.api.health.database import get_cot_db_size
from app.api.health.disk import get_disk_usage
from app.api.health.tls import get_tls_status
from app.api.health.updates import check_updates
from app.api.users.authentik import AuthentikClient
from app.api.users.tak_server import TakServerClient
from app.config import settings
from app.evaluator import evaluate
from app.monitoring_config import load_config
from app.status import Status

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

_HEALTH_FUNCTIONS = {
    "database": get_cot_db_size,
    "autovacuum": get_autovacuum_health,
    "disk": get_disk_usage,
    "certs": get_cert_status,
    "tls": get_tls_status,
    "containers": get_all_container_health,
    "config": check_config_drift,
    "updates": check_updates,
}

# ── Lazy singletons for scheduler-owned clients ───────────────────────────────
# Created once on first use and reused across ticks to avoid the overhead of
# re-initialising SSL contexts / HTTP connections every check interval.

_scheduler_ak: AuthentikClient | None = None
_scheduler_tak: TakServerClient | None = None


def _get_scheduler_ak() -> AuthentikClient | None:
    global _scheduler_ak
    if _scheduler_ak is None and settings.authentik_api_token:
        _scheduler_ak = AuthentikClient(
            base_url=settings.authentik_url,
            token=settings.authentik_api_token,
            hidden_prefixes=settings.users_hidden_prefixes.split(","),
        )
    return _scheduler_ak


def _get_scheduler_tak() -> TakServerClient | None:
    global _scheduler_tak
    if _scheduler_tak is None and settings.tak_api_cert_path:
        _scheduler_tak = TakServerClient(
            base_url=settings.tak_server_url,
            cert_path=settings.tak_api_cert_path,
            cert_password=settings.tak_api_cert_password,
        )
    return _scheduler_tak


def _poll(service_name, health_fn, service_config):
    """Generic health poll: run health_fn, evaluate against thresholds, alert."""
    try:
        raw = health_fn()
    except Exception as e:
        raw = {"error": str(e)[:200]}

    if "error" in raw:
        error_eval = {"status": "critical", "message": raw["error"]}
        store.update(service_name, raw, error_eval, None)
        check_and_alert(service_name, "critical", raw["error"])
        return

    evaluated = evaluate(service_name, raw, service_config.get("thresholds", {}))
    store.update(service_name, raw, evaluated, service_config.get("thresholds"))

    if Status[evaluated["status"]] >= Status[service_config.get("alert_min_level", "warning")]:
        check_and_alert(service_name, evaluated["status"], evaluated.get("message", ""))


def _check_user_expiry(
    ak: AuthentikClient | None = None,
    tak: TakServerClient | None = None,
):
    """Check for expired TTL users and deactivate + revoke certs.

    When called by the scheduler (no args), module-level singletons are reused
    across ticks. Explicit arguments are accepted for test injection.
    """
    if ak is None:
        ak = _get_scheduler_ak()
        if ak is None:
            return
    if tak is None:
        tak = _get_scheduler_tak()

    try:
        pending = ak.get_users_pending_expiry()
    except Exception:
        log.warning("TTL check: failed to query Authentik for expired users")
        return

    for user in pending:
        pk = user["pk"]
        username = user["username"]
        is_active = user.get("is_active", True)

        try:
            if is_active:
                ak.deactivate_user(pk)
                log.info("TTL expired: deactivated user %s (pk=%d)", username, pk)

            if tak:
                all_revoked = tak.revoke_all_user_certs(username)
            else:
                all_revoked = True

            if all_revoked:
                ak.mark_certs_revoked(pk)
                log.info("TTL expired: revoked certs for %s (pk=%d)", username, pk)
            else:
                log.warning("TTL expired: cert revocation incomplete for %s, will retry", username)

        except Exception:
            log.exception("TTL check: error processing user %s (pk=%d)", username, pk)
            continue


def start_scheduler():
    config = load_config()

    for service_name, health_fn in _HEALTH_FUNCTIONS.items():
        service_config = config.get(service_name, {})
        interval = service_config.get("interval", 60)
        scheduler.add_job(
            _poll,
            "interval",
            seconds=interval,
            id=service_name,
            args=[service_name, health_fn, service_config],
        )

    # TTL enforcement (only if Authentik is configured)
    if settings.authentik_api_token:
        scheduler.add_job(
            _check_user_expiry,
            "interval",
            seconds=settings.user_expiry_check_interval,
            id="user_expiry",
        )

    scheduler.start()

    # Populate cache immediately — best-effort, one failure doesn't block the rest
    for service_name, health_fn in _HEALTH_FUNCTIONS.items():
        service_config = config.get(service_name, {})
        try:
            _poll(service_name, health_fn, service_config)
        except Exception:
            log.warning("Initial health poll failed for %s", service_name, exc_info=True)


def stop_scheduler():
    scheduler.shutdown(wait=False)

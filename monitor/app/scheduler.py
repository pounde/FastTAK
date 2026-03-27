"""Background scheduler for periodic health checks and alerting.

Uses BackgroundScheduler (thread-based), not AsyncIOScheduler, because the
health check functions call blocking Docker SDK / subprocess operations.
AsyncIOScheduler would run these on the event loop and stall all async
endpoints. BackgroundScheduler runs each job in its own thread.

The alert engine's check_and_alert() is async (for SMS via httpx), so we
use a single asyncio.run() per job to batch all alert calls in one event loop.
"""

import asyncio
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.api.alerts.engine import check_and_alert
from app.api.health.certs import get_cert_status
from app.api.health.config_drift import check_config_drift
from app.api.health.containers import get_all_container_health
from app.api.health.database import get_cot_db_size
from app.api.health.disk import get_disk_usage
from app.api.users.authentik import AuthentikClient
from app.api.users.tak_server import TakServerClient
from app.config import settings

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

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


def _check_containers():
    """Poll container health and alert on state changes."""

    async def _run():
        for c in get_all_container_health():
            state = c["health"] if c["health"] != "unknown" else c["status"]
            await check_and_alert(
                service=c["name"],
                new_state=state,
                detail=f"Container {c['name']}: status={c['status']}, health={c['health']}",
            )

    asyncio.run(_run())


def _check_certs():
    """Check certificate expiry and alert on warning/critical."""

    async def _run():
        for cert in get_cert_status():
            await check_and_alert(
                service=f"cert:{cert['file']}",
                new_state=cert["status"],
                detail=f"{cert['file']}: {cert['days_left']} days remaining",
            )

    asyncio.run(_run())


def _check_database():
    """Check CoT database size and alert on thresholds."""
    info = get_cot_db_size()
    if "error" not in info:
        asyncio.run(
            check_and_alert(
                service="cot-database-size",
                new_state=info["status"],
                detail=f"CoT DB size: {info['size_human']}",
            )
        )


def _check_disk():
    """Check disk usage and alert on thresholds."""

    async def _run():
        for d in get_disk_usage():
            await check_and_alert(
                service=f"disk:{d['mount']}",
                new_state=d["status"],
                detail=f"{d['mount']}: {d['percent']}% used ({d['used_gb']}/{d['total_gb']} GB)",
            )

    asyncio.run(_run())


def _check_config():
    """Check if .env has changed and alert."""
    info = check_config_drift()
    if info["status"] == "changed":
        asyncio.run(
            check_and_alert(
                service="config:.env",
                new_state="warning",
                detail=info["message"],
            )
        )


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
    scheduler.add_job(
        _check_containers, "interval", seconds=settings.health_check_interval, id="containers"
    )
    scheduler.add_job(_check_certs, "interval", seconds=3600, id="certs")  # Hourly
    scheduler.add_job(_check_database, "interval", seconds=3600, id="database")  # Hourly
    scheduler.add_job(_check_disk, "interval", seconds=300, id="disk")  # Every 5 min
    scheduler.add_job(_check_config, "interval", seconds=60, id="config")  # Every minute
    # TTL enforcement (only if Authentik is configured)
    if settings.authentik_api_token:
        scheduler.add_job(
            _check_user_expiry,
            "interval",
            seconds=settings.user_expiry_check_interval,
            id="user_expiry",
        )
    scheduler.start()


def stop_scheduler():
    scheduler.shutdown(wait=False)

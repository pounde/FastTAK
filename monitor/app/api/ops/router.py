"""Operations API — DB maintenance, alert testing, and log viewing.

Note: All endpoints use sync `def` (not `async def`) because the underlying
Docker SDK calls are blocking. FastAPI runs sync endpoints in a threadpool
automatically, preventing the event loop from blocking.

Container lifecycle management (restart/stop/start) is intentionally excluded.
Certificate management is handled through the Users and Service Accounts APIs.
"""

from fastapi import APIRouter, HTTPException, Query

from app.api.alerts.email import send_alert_email
from app.api.alerts.sms import send_alert_sms
from app.api.ops.database import vacuum_database
from app.docker_client import discover_services, find_container

router = APIRouter(prefix="/api/ops", tags=["operations"])


def _validate_container(name: str):
    if name not in discover_services():
        raise HTTPException(400, f"Unknown container: {name}")


# ── Logs ────────────────────────────────────────────────────────────────


@router.get("/service/{name}/logs", summary="Service container logs")
def svc_logs(name: str, tail: int = Query(default=200, ge=1, le=5000)):
    """Fetch raw container logs for a known Compose service.

    Only services discovered in the Compose project are allowed; unknown
    names are rejected with 400 to prevent container enumeration.

    Args:
        name: Compose service name (e.g., ``takserver``, ``caddy``).
        tail: Number of log lines to return (1-5000, default 200).

    Returns:
        Dict with service name and timestamped log text.

    Raises:
        HTTPException(400): If the service name is not in the Compose project.
        HTTPException(404): If the container exists in Compose but is not running.
    """
    _validate_container(name)
    container = find_container(name)
    if container is None:
        raise HTTPException(404, f"Container '{name}' not found")
    try:
        logs = container.logs(tail=tail, timestamps=True).decode(errors="replace")
        return {"name": name, "logs": logs}
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


# ── Database ────────────────────────────────────────────────────────────


@router.post("/database/vacuum", summary="VACUUM FULL the CoT database")
def db_vacuum():
    """Run VACUUM FULL on the CoT database to reclaim disk space.

    **WARNING**: VACUUM FULL takes an exclusive lock on the database. All TAK
    clients will lose connectivity for the duration of the operation. Only
    run this during a planned maintenance window.

    Returns:
        Dict with success flag and reclaimed-space info.

    Raises:
        HTTPException(500): If the vacuum operation fails.
    """
    result = vacuum_database()
    if not result.get("success", True):
        raise HTTPException(500, result.get("error", "Unknown error"))
    return result


# ── Alert Testing ────────────────────────────────────────────────────────────


@router.post("/alerts/test-email", summary="Send test email alert")
def test_email():
    """Send a test alert email to the configured recipient.

    Uses the SMTP settings from the monitor's .env configuration. If email
    alerting is misconfigured, this will return ``success: false``.

    Returns:
        Dict with ``success`` boolean.
    """
    ok = send_alert_email(
        "Test Alert",
        "This is a test alert from FastTAK Monitor."
        " If you received this, email alerting is working.",
    )
    return {"success": ok}


@router.post("/alerts/test-sms", summary="Send test SMS alert")
async def test_sms():
    """Send a test SMS alert to the configured phone number.

    Uses the SMS provider settings from the monitor's .env configuration.
    If SMS alerting is misconfigured, this will return ``success: false``.

    Returns:
        Dict with ``success`` boolean.
    """
    ok = await send_alert_sms("[FastTAK] Test alert. SMS alerting is working.")
    return {"success": ok}

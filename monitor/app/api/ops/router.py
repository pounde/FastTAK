"""Operations API — cert management, DB maintenance, and log viewing.

Note: All endpoints use sync `def` (not `async def`) because the underlying
Docker SDK calls are blocking. FastAPI runs sync endpoints in a threadpool
automatically, preventing the event loop from blocking.

Container lifecycle management (restart/stop/start) is intentionally excluded.
"""

from fastapi import APIRouter, HTTPException, Query

from app.api.alerts.email import send_alert_email
from app.api.alerts.sms import send_alert_sms
from app.api.ops.certs import (
    create_client_cert,
    create_server_cert,
    list_certs,
    revoke_cert,
)
from app.api.ops.database import vacuum_database
from app.docker_client import discover_services, find_container

router = APIRouter(prefix="/api/ops", tags=["operations"])


def _validate_container(name: str):
    if name not in discover_services():
        raise HTTPException(400, f"Unknown container: {name}")


# ── Logs ────────────────────────────────────────────────────────────────


@router.get("/service/{name}/logs")
def svc_logs(name: str, tail: int = Query(default=200, ge=1, le=5000)):
    _validate_container(name)
    container = find_container(name)
    if container is None:
        raise HTTPException(404, f"Container '{name}' not found")
    try:
        logs = container.logs(tail=tail, timestamps=True).decode(errors="replace")
        return {"name": name, "logs": logs}
    except Exception as e:
        raise HTTPException(500, str(e)[:300])


# ── Certs ───────────────────────────────────────────────────────────────


@router.get("/certs/list")
def certs_list():
    result = list_certs()
    if not result.get("success", True):
        raise HTTPException(500, result.get("error", "Unknown error"))
    return result


@router.post("/certs/create-client/{name}")
def certs_create_client(name: str):
    result = create_client_cert(name)
    if not result.get("success", True):
        raise HTTPException(
            400 if "Name must" in result.get("error", "") else 500,
            result.get("error", "Unknown error"),
        )
    return result


@router.post("/certs/create-server/{name}")
def certs_create_server(name: str):
    result = create_server_cert(name)
    if not result.get("success", True):
        raise HTTPException(
            400 if "Name must" in result.get("error", "") else 500,
            result.get("error", "Unknown error"),
        )
    return result


@router.post("/certs/revoke/{name}")
def certs_revoke(name: str):
    result = revoke_cert(name)
    if not result.get("success", True):
        raise HTTPException(
            400 if "Name must" in result.get("error", "") else 500,
            result.get("error", "Unknown error"),
        )
    return result


# ── Database ────────────────────────────────────────────────────────────


@router.post("/database/vacuum")
def db_vacuum():
    result = vacuum_database()
    if not result.get("success", True):
        raise HTTPException(500, result.get("error", "Unknown error"))
    return result


# ── Alert Testing ────────────────────────────────────────────────────────────


@router.post("/alerts/test-email")
def test_email():
    ok = send_alert_email(
        "Test Alert",
        "This is a test alert from FastTAK Monitor."
        " If you received this, email alerting is working.",
    )
    return {"success": ok}


@router.post("/alerts/test-sms")
async def test_sms():
    ok = await send_alert_sms("[FastTAK] Test alert. SMS alerting is working.")
    return {"success": ok}

"""Health check API endpoints.

Note: These are sync `def` (not `async def`) because the Docker SDK is
synchronous. FastAPI automatically runs sync endpoints in a threadpool,
preventing the event loop from blocking.
"""

from fastapi import APIRouter

import app.store as store
from app.api.health.autovacuum import get_autovacuum_health
from app.api.health.certs import get_cert_status
from app.api.health.config_drift import check_config_drift
from app.api.health.containers import get_all_container_health, get_container_stats
from app.api.health.database import get_cot_db_size
from app.api.health.disk import get_disk_usage
from app.api.health.tls import get_tls_status
from app.api.health.updates import check_updates
from app.docker_client import discover_running_services
from app.status import Status

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health_summary(view: str | None = None):
    """Full health cache, or ?view=status for summary."""
    if view == "status":
        all_data = store.get_all()
        services = {}
        worst = Status.ok
        for name, entry in all_data.items():
            svc = {"status": entry["status"]}
            if entry.get("message"):
                svc["message"] = entry["message"]
            services[name] = svc
            level = Status[entry["status"]]
            if level > Status.note and level > worst:
                worst = level
        return {"overall": worst.name, "services": services}
    all_data = store.get_all()
    worst = Status.ok
    for entry in all_data.values():
        level = Status[entry["status"]]
        if level > Status.note and level > worst:
            worst = level
    return {"overall": worst.name, **all_data}


@router.get("/containers")
def containers():
    """Health status for all FastTAK containers."""
    return get_all_container_health()


@router.get("/resources")
def resources():
    """CPU/memory stats for all running containers."""
    results = []
    for name in discover_running_services():
        stats = get_container_stats(name)
        if stats:
            results.append(stats)
    return results


@router.get("/certs")
def certs():
    """Certificate expiry status for all TAK certs."""
    return get_cert_status()


@router.get("/database")
def database():
    """CoT database size and status."""
    return get_cot_db_size()


@router.get("/updates")
def updates():
    """Check for available updates across stack components."""
    return check_updates()


@router.get("/disk")
def disk():
    """Disk usage for key mount points."""
    return get_disk_usage()


@router.get("/tls")
def tls():
    """TLS certificate expiry for Caddy-served endpoints."""
    return get_tls_status()


@router.get("/config")
def config():
    """Check if .env has changed since monitor startup."""
    return check_config_drift()


@router.get("/autovacuum")
def autovacuum():
    """Autovacuum health — dead tuple ratios and last vacuum times."""
    return get_autovacuum_health()

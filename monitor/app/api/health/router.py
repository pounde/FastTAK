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


@router.get("", summary="Health summary")
def health_summary(view: str | None = None):
    """Aggregated health status for all monitored services.

    Returns the full scheduler cache by default. Use ``?view=status`` for a
    compact summary with per-service status and an overall roll-up.

    Data comes from the background scheduler cache, not live queries, so
    results may be up to one polling interval stale.

    Args:
        view: Set to ``"status"`` for a compact per-service summary.

    Returns:
        Full cache dict keyed by service name, or compact status dict when
        ``view=status``.
    """
    if view == "status":
        all_data = store.fetch_all()
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
    all_data = store.fetch_all()
    worst = Status.ok
    for entry in all_data.values():
        level = Status[entry["status"]]
        if level > Status.note and level > worst:
            worst = level
    return {"overall": worst.name, **all_data}


@router.get("/containers", summary="Container health")
def containers():
    """Health status for all FastTAK Docker Compose containers.

    Returns:
        List of container health entries with status and metadata.
    """
    return get_all_container_health()


@router.get("/resources", summary="Container resource usage")
def resources():
    """Live CPU and memory stats for all running containers.

    Unlike most health endpoints, this fetches stats per-request via the
    Docker API rather than reading from the scheduler cache. Expect higher
    latency proportional to the number of running containers.

    Returns:
        List of resource-usage dicts (CPU %, memory bytes/limit) per container.
    """
    results = []
    for name in discover_running_services():
        stats = get_container_stats(name)
        if stats:
            results.append(stats)
    return results


@router.get("/certs", summary="TAK certificate expiry")
def certs():
    """Certificate expiry status for infrastructure and service certs.

    Reads .pem files from the TAK cert directory and reports days until
    expiry. User certs are excluded — their expiry is managed through the
    user detail panel.

    Returns:
        List of cert entries with name, expiry date, days remaining, and category.
    """
    return get_cert_status()


@router.get("/database", summary="CoT database size")
def database():
    """CoT (Cursor on Target) database size and status.

    Reports total and live row counts so operators can gauge database growth
    and decide when maintenance (e.g., VACUUM) is warranted.

    Returns:
        Dict with total size, live size, and status.
    """
    return get_cot_db_size()


@router.get("/updates", summary="Available updates")
def updates():
    """Check for available updates across stack components.

    Returns:
        Update availability info per component.
    """
    return check_updates()


@router.get("/disk", summary="Disk usage")
def disk():
    """Disk usage for key mount points.

    Returns:
        List of mount-point entries with used/total bytes and percentage.
    """
    return get_disk_usage()


@router.get("/tls", summary="TLS certificate expiry")
def tls():
    """TLS certificate expiry for Caddy-served endpoints.

    Returns:
        TLS status with expiry dates for each served domain.
    """
    return get_tls_status()


@router.get("/config", summary="Config drift detection")
def config():
    """Detect .env configuration drift since monitor startup.

    Computes a SHA-256 hash of the current .env file and compares it to the
    baseline captured at startup. A mismatch indicates someone edited .env
    without restarting the monitor.

    Returns:
        Dict with drift status, current hash, and baseline hash.
    """
    return check_config_drift()


@router.get("/autovacuum", summary="Autovacuum health")
def autovacuum():
    """Autovacuum health for the CoT database.

    Reports dead-tuple ratios and last vacuum timestamps per table. High
    dead-tuple ratios indicate autovacuum may be falling behind, which
    degrades query performance.

    Returns:
        List of per-table entries with dead tuple count, ratio, and last
        vacuum time.
    """
    return get_autovacuum_health()

"""Dashboard UI routes — page routes and HTMX partial routes.

The dashboard is a consumer of the API modules. It imports data functions
from app.api.* and renders them as HTML via Jinja2 templates.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.dashboard.services import get_service_links

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["dashboard"])


def _page_context(request: Request, **extra) -> dict:
    """Common template context for all pages."""
    return {"request": request, "service_links": get_service_links(), **extra}


# --- Page routes ---

@router.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", _page_context(request))


@router.get("/ops")
async def ops_page(request: Request):
    return templates.TemplateResponse("ops.html", _page_context(request))


@router.get("/logs")
async def logs_page(request: Request):
    from app.docker_client import FASTTAK_CONTAINERS
    return templates.TemplateResponse("logs.html",
                                      _page_context(request, containers=FASTTAK_CONTAINERS))


# --- UI Partials (HTMX fragments) ---

@router.get("/ui/partials/health-grid")
def ui_health_grid(request: Request):
    from app.api.health.containers import get_all_container_health
    data = get_all_container_health()
    return templates.TemplateResponse("partials/health_grid.html",
                                      {"request": request, "containers": data})


@router.get("/ui/partials/cert-status")
def ui_cert_status(request: Request):
    from app.api.health.certs import get_cert_status
    data = get_cert_status()
    return templates.TemplateResponse("partials/cert_status.html",
                                      {"request": request, "certs": data})


@router.get("/ui/partials/update-status")
async def ui_update_status(request: Request):
    from app.api.health.updates import check_updates
    data = await check_updates()
    return templates.TemplateResponse("partials/update_status.html",
                                      {"request": request, "updates": data})


@router.get("/ui/partials/resources")
def ui_resources(request: Request):
    from app.api.health.containers import get_container_stats
    from app.docker_client import FASTTAK_CONTAINERS
    results = []
    for name in FASTTAK_CONTAINERS:
        stats = get_container_stats(name)
        if stats:
            results.append(stats)
    return templates.TemplateResponse("partials/resources.html",
                                      {"request": request, "resources": results})


@router.get("/ui/partials/activity-log")
async def ui_activity_log(request: Request):
    from app.api.alerts.engine import get_activity_log
    data = get_activity_log()
    return templates.TemplateResponse("partials/activity_log.html",
                                      {"request": request, "events": data})


@router.get("/ui/partials/disk-usage")
def ui_disk_usage(request: Request):
    from app.api.health.disk import get_disk_usage
    data = get_disk_usage()
    return templates.TemplateResponse("partials/disk_usage.html",
                                      {"request": request, "disks": data})


@router.get("/ui/partials/tls-status")
def ui_tls_status(request: Request):
    from app.api.health.tls import get_tls_status
    data = get_tls_status()
    return templates.TemplateResponse("partials/tls_status.html",
                                      {"request": request, "tls_certs": data})


@router.get("/ui/partials/config-status")
def ui_config_status(request: Request):
    from app.api.health.config_drift import check_config_drift
    data = check_config_drift()
    return templates.TemplateResponse("partials/config_status.html",
                                      {"request": request, "config": data})

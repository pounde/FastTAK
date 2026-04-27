"""Dashboard UI routes — page routes and HTMX partial routes.

The dashboard is a consumer of the store cache. Partial routes read from the
in-memory store populated by the scheduler, rather than calling health
functions directly. This avoids double-work on every page load.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app import store
from app.dashboard.services import get_service_links

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(tags=["dashboard"])


def _page_context(**extra) -> dict:
    """Common template context for all pages."""
    return {"service_links": get_service_links(), **extra}


# --- Page routes ---


@router.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", _page_context())


@router.get("/ops")
async def ops_page(request: Request):
    return templates.TemplateResponse(request, "ops.html", _page_context())


@router.get("/logs")
async def logs_page(request: Request):
    from app.docker_client import discover_services

    return templates.TemplateResponse(
        request, "logs.html", _page_context(containers=discover_services())
    )


@router.get("/users")
async def users_page(request: Request):
    return templates.TemplateResponse(request, "users.html", _page_context())


@router.get("/service-accounts")
async def service_accounts_page(request: Request):
    return templates.TemplateResponse(request, "service_accounts.html", _page_context())


# --- UI Partials (HTMX fragments) ---


@router.get("/ui/partials/health-grid")
def ui_health_grid(request: Request):
    entry = store.fetch("containers")
    data = entry["data"] if entry else {}
    return templates.TemplateResponse(
        request, "partials/health_grid.html", {"containers": data.get("items", [])}
    )


@router.get("/ui/partials/cert-status")
def ui_cert_status(request: Request):
    entry = store.fetch("certs")
    data = entry["data"] if entry else {}
    return templates.TemplateResponse(
        request, "partials/cert_status.html", {"certs": data.get("items", [])}
    )


@router.get("/ui/partials/update-status")
def ui_update_status(request: Request):
    entry = store.fetch("updates")
    data = entry["data"] if entry else {}
    return templates.TemplateResponse(
        request, "partials/update_status.html", {"updates": data.get("items", [])}
    )


@router.get("/ui/partials/resources")
def ui_resources(request: Request):
    from app.api.health.containers import get_container_stats
    from app.docker_client import discover_running_services

    results = []
    for name in discover_running_services():
        stats = get_container_stats(name)
        if stats:
            results.append(stats)
    return templates.TemplateResponse(request, "partials/resources.html", {"resources": results})


@router.get("/ui/partials/activity-log")
async def ui_activity_log(request: Request):
    from app.api.alerts.engine import get_activity_log

    data = get_activity_log()
    return templates.TemplateResponse(request, "partials/activity_log.html", {"events": data})


@router.get("/ui/partials/disk-usage")
def ui_disk_usage(request: Request):
    entry = store.fetch("disk")
    data = entry["data"] if entry else {}
    return templates.TemplateResponse(
        request, "partials/disk_usage.html", {"disks": data.get("items", [])}
    )


@router.get("/ui/partials/tls-status")
def ui_tls_status(request: Request):
    entry = store.fetch("tls")
    data = entry["data"] if entry else {}
    return templates.TemplateResponse(
        request, "partials/tls_status.html", {"tls_certs": data.get("items", [])}
    )


@router.get("/ui/partials/config-status")
def ui_config_status(request: Request):
    entry = store.fetch("config")
    data = entry["data"] if entry else {}
    status = entry["status"] if entry else "ok"
    return templates.TemplateResponse(
        request, "partials/config_status.html", {"config": data, "status": status}
    )


@router.get("/ui/partials/user-list")
async def ui_user_list(request: Request):
    from fastapi import HTTPException as _HTTPException

    from app.api.users.router import _get_identity

    search = request.query_params.get("search", "")
    page = int(request.query_params.get("page", "1"))
    page_size = 25

    try:
        ak = _get_identity()
    except _HTTPException:
        return templates.TemplateResponse(
            request,
            "partials/user_list.html",
            {
                "users": [],
                "total": 0,
                "page": 1,
                "page_size": page_size,
                "search": search,
                "error": "Identity provider not configured",
            },
        )

    all_users = ak.list_users(search=search or None)
    total = len(all_users)
    start = (page - 1) * page_size
    page_users = all_users[start : start + page_size]

    return templates.TemplateResponse(
        request,
        "partials/user_list.html",
        {
            "users": page_users,
            "total": total,
            "page": page,
            "page_size": page_size,
            "search": search,
        },
    )


@router.get("/ui/partials/service-account-list")
async def ui_service_account_list(request: Request):
    from fastapi import HTTPException as _HTTPException

    from app.api.service_accounts.router import _get_identity

    try:
        ak = _get_identity()
    except _HTTPException:
        return templates.TemplateResponse(
            request,
            "partials/service_account_list.html",
            {"accounts": [], "error": "Identity provider not configured"},
        )

    accounts = ak.list_users(search="svc_")
    return templates.TemplateResponse(
        request,
        "partials/service_account_list.html",
        {"accounts": accounts},
    )


@router.get("/ui/partials/database-health")
def ui_database_health(request: Request):
    from app.status import Status

    db_entry = store.fetch("database")
    av_entry = store.fetch("autovacuum")
    db_data = db_entry["data"] if db_entry else {}
    db_status = db_entry["status"] if db_entry else "ok"
    av_status = av_entry["status"] if av_entry else "ok"
    combined_status = max(Status[db_status], Status[av_status]).name
    return templates.TemplateResponse(
        request,
        "partials/database_health.html",
        {
            "db": db_data,
            "status": combined_status,
            "av_status": av_status,
        },
    )


@router.get("/ui/partials/connected-clients")
def ui_connected_clients(request: Request):
    from app.api.tak.router import _build_clients_response

    try:
        clients = _build_clients_response(include_lkp=True)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "partials/connected_clients.html",
            {"clients": [], "error": str(exc)[:200]},
        )
    return templates.TemplateResponse(
        request,
        "partials/connected_clients.html",
        {"clients": clients, "error": None},
    )


@router.get("/ui/partials/recent-contacts")
def ui_recent_contacts(request: Request):
    from app.api.tak.router import _build_recent_contacts_response

    max_age_param = request.query_params.get("max_age")
    max_age = int(max_age_param) if max_age_param else None
    try:
        contacts = _build_recent_contacts_response(max_age=max_age)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "partials/recent_contacts.html",
            {"contacts": [], "error": str(exc)[:200], "max_age": max_age},
        )
    return templates.TemplateResponse(
        request,
        "partials/recent_contacts.html",
        {"contacts": contacts, "error": None, "max_age": max_age},
    )

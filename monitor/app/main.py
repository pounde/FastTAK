"""FastTAK Monitor — health monitoring, alerting, and operations console.

This is the application entrypoint. It wires together:
- API routers (app.api) — JSON endpoints for health, ops, alerts
- Dashboard router (app.dashboard) — HTML UI that consumes the API
- Background scheduler — periodic health checks and alerting
"""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from app.api.events.router import router as events_router
from app.api.health.config_drift import init_config_hash
from app.api.health.router import router as health_router
from app.api.ops.router import router as ops_router
from app.api.service_accounts.router import router as service_accounts_router
from app.api.tak.router import router as tak_router
from app.api.users.router import router as users_router
from app.audit import AuditMiddleware, AuthContextMiddleware, init_schema
from app.dashboard.routes import router as dashboard_router
from app.dashboard.routes import templates
from app.scheduler import start_scheduler, stop_scheduler

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_config_hash()
    try:
        init_schema()
    except Exception:
        log.exception("Could not initialise fastak_events schema")
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="FastTAK Monitor", docs_url="/api/docs", lifespan=lifespan)


def _add_middleware_in_execution_order(app: FastAPI, *middlewares: type) -> None:
    """Register middlewares so they run in the listed order at request time.

    Starlette runs middleware in reverse-of-registration order: the last
    `add_middleware` call wraps everything and runs first. This helper hides
    that footgun — pass middlewares in the conceptual execution order
    (outer-most first) and registration order is reversed for you.
    """
    for mw in reversed(middlewares):
        app.add_middleware(mw)


# Execution order: AuthContext sets request.state.username/groups/client_ip,
# then Audit reads them to record the row, then the route handler runs.
_add_middleware_in_execution_order(app, AuthContextMiddleware, AuditMiddleware)

# API (JSON)
app.include_router(health_router)
app.include_router(ops_router)
app.include_router(users_router)
app.include_router(service_accounts_router)
app.include_router(tak_router)
app.include_router(events_router)

# Dashboard (HTML)
app.include_router(dashboard_router)


# Jinja2 filters
def _format_timestamp(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


templates.env.filters["ts"] = _format_timestamp


@app.get("/api/ping")
async def ping():
    return {"status": "ok"}

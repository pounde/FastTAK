"""FastTAK Monitor — health monitoring, alerting, and operations console.

This is the application entrypoint. It wires together:
- API routers (app.api) — JSON endpoints for health, ops, alerts
- Dashboard router (app.dashboard) — HTML UI that consumes the API
- Background scheduler — periodic health checks and alerting
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from app.api.health.config_drift import init_config_hash
from app.api.health.router import router as health_router
from app.api.ops.router import router as ops_router
from app.api.service_accounts.router import router as service_accounts_router
from app.api.tak.router import router as tak_router
from app.api.users.router import router as users_router
from app.dashboard.routes import router as dashboard_router
from app.dashboard.routes import templates
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_config_hash()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="FastTAK Monitor", docs_url="/api/docs", lifespan=lifespan)

# API (JSON)
app.include_router(health_router)
app.include_router(ops_router)
app.include_router(users_router)
app.include_router(service_accounts_router)
app.include_router(tak_router)

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

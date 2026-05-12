"""Tests for the require_group FastAPI dependency factory."""

from app.api.auth_deps import require_group
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware


class _FakeAuthMiddleware(BaseHTTPMiddleware):
    """Mimics AuthContextMiddleware: populates request.state.groups."""

    async def dispatch(self, request, call_next):
        groups_header = request.headers.get("X-Test-Groups", "")
        request.state.groups = [g for g in groups_header.split(",") if g]
        return await call_next(request)


def _build_app(env_var: str, default: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(_FakeAuthMiddleware)
    dep = require_group(env_var, default=default)

    @app.get("/needs-group")
    def needs_group(_=Depends(dep)):
        return {"ok": True}

    return app


def test_allows_when_group_present(monkeypatch):
    monkeypatch.setenv("TEST_GROUP", "monitor_admin")
    app = _build_app("TEST_GROUP", default="monitor_admin")
    client = TestClient(app)
    r = client.get("/needs-group", headers={"X-Test-Groups": "tak_alpha,monitor_admin,tak_beta"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_rejects_when_group_missing(monkeypatch):
    monkeypatch.setenv("TEST_GROUP", "monitor_admin")
    app = _build_app("TEST_GROUP", default="monitor_admin")
    client = TestClient(app)
    r = client.get("/needs-group", headers={"X-Test-Groups": "tak_alpha,tak_beta"})
    assert r.status_code == 403
    assert "monitor_admin" in r.json()["detail"]


def test_uses_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("TEST_GROUP", raising=False)
    app = _build_app("TEST_GROUP", default="monitor_admin")
    client = TestClient(app)
    r = client.get("/needs-group", headers={"X-Test-Groups": "monitor_admin"})
    assert r.status_code == 200


def test_reads_env_at_request_time(monkeypatch):
    """Group name changes between requests should take effect."""
    monkeypatch.setenv("TEST_GROUP", "groupA")
    app = _build_app("TEST_GROUP", default="fallback")
    client = TestClient(app)

    r = client.get("/needs-group", headers={"X-Test-Groups": "groupA"})
    assert r.status_code == 200

    monkeypatch.setenv("TEST_GROUP", "groupB")
    r = client.get("/needs-group", headers={"X-Test-Groups": "groupA"})
    assert r.status_code == 403

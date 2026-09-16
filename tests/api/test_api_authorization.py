"""Authorization gate on the monitor (issue #52, DD-047).

The monitor is admin-only end to end: every JSON API and every dashboard page
requires the admin group, `/api/ping` excepted. Authentication (a valid LDAP
bind) is handled upstream by Caddy `forward_auth` -> ldap-proxy, which sets
`Remote-Groups` — but it passes *every* TAK user, so each route must
additionally *authorize* on that group.

Gating the JSON APIs alone is not enough, which is why the dashboard is covered
here too: `/ui/partials/user-list` calls the identity client directly and would
otherwise hand the full user roster to any authenticated caller.

`require_group` itself (env override, multi-group headers, request-time env
reads) is unit-tested in tests/unit/test_auth_deps.py. What these tests cover
is the *wiring* — which routes carry the gate, on the real app, through the
real AuthContextMiddleware.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


@pytest.fixture
def anon_client():
    """TestClient that sends no auth headers of its own.

    Deliberately not tests/api/conftest.py's `client`, which sends an admin
    group on every request — these tests supply their own headers per case.
    """
    with (
        patch("app.main.init_config_hash"),
        patch("app.main.start_scheduler"),
        patch("app.main.stop_scheduler"),
    ):
        from app.main import app

        with TestClient(app) as c:
            yield c


@pytest.fixture(autouse=True)
def _stub_external_deps():
    """Stub the Docker socket (`/logs`) and the event store (`/api/events`).

    Only the admin path reaches either — a rejected caller never gets that far —
    but without these the admin-path assertions blow up on the environment
    instead of reporting what the gate did.
    """
    with (
        patch("app.docker_client.discover_services", return_value=[]),
        patch("app.api.events.router.fetch", return_value=[]),
    ):
        yield


NONADMIN = {"Remote-User": "bob", "Remote-Groups": "tak_alpha"}
ADMIN = {"Remote-User": "alice", "Remote-Groups": "monitor_admin"}
# Caddy sends the caller's full group list; admin membership has to be found
# among the others, and the header may carry whitespace after the separator.
ADMIN_AMONG_GROUPS = {"Remote-User": "alice", "Remote-Groups": "tak_alpha, monitor_admin,tak_beta"}

# (method, path) pairs that must be admin-gated. The JSON routers take the gate
# at include time, so one route per router covers every route in that router;
# the dashboard router mixes admin pages with the open index, so each gated
# dashboard route is listed individually.
GATED_ROUTES = [
    ("get", "/api/docs"),  # the route inventory itself (issue #82)
    ("get", "/api/openapi.json"),
    ("get", "/api/users"),  # users_router (also /api/groups)
    ("post", "/api/service-accounts"),  # service_accounts_router
    ("post", "/api/ops/database/vacuum"),  # ops_router
    ("get", "/api/health"),  # health_router
    ("get", "/api/events"),  # events_router
    ("get", "/api/tak/clients"),  # tak_router
    ("get", "/"),  # dashboard pages
    ("get", "/users"),
    ("get", "/service-accounts"),
    ("get", "/ops"),
    ("get", "/logs"),
    ("get", "/ui/partials/user-list"),  # same roster as GET /api/users
    ("get", "/ui/partials/service-account-list"),
    ("get", "/ui/partials/activity-log"),  # the only route onto the health event feed
    ("get", "/ui/partials/health-grid"),
]


def test_ping_stays_open(anon_client):
    """The liveness probe must answer without credentials — everything else on
    the monitor is admin-only, so this is the one deliberate exception."""
    r = anon_client.get("/api/ping")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_rejects_missing_admin_group(anon_client, method, path):
    r = getattr(anon_client, method)(path, headers=NONADMIN)
    assert r.status_code == 403, f"{method} {path} was not gated (got {r.status_code})"


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_rejects_no_groups_at_all(anon_client, method, path):
    r = getattr(anon_client, method)(path)
    assert r.status_code == 403, f"{method} {path} allowed an unauthenticated caller"


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_admin_group_passes_the_gate(anon_client, method, path):
    """Admin must get *past* the auth gate (not 403). Downstream may 4xx/5xx on
    unmocked dependencies — we only assert the gate itself does not reject."""
    r = getattr(anon_client, method)(path, headers=ADMIN)
    assert r.status_code != 403, f"{method} {path} rejected a legitimate admin"


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_admin_among_other_groups_passes_the_gate(anon_client, method, path):
    """Real callers arrive with several groups in one header, not just the admin one."""
    r = getattr(anon_client, method)(path, headers=ADMIN_AMONG_GROUPS)
    assert r.status_code != 403, f"{method} {path} rejected an admin with extra groups"


def test_admin_reaches_the_handler(anon_client, monkeypatch):
    """The '!= 403' assertions above cannot tell a passed gate from a failure
    further down. Prove the admin path end-to-end on one route."""
    mock_ak = MagicMock()
    mock_ak.list_users.return_value = [
        {"id": 1, "username": "alice", "name": "Alice", "is_active": True, "groups": []}
    ]
    monkeypatch.setattr("app.api.users.router._identity", mock_ak)

    r = anon_client.get("/api/users", headers=ADMIN)

    assert r.status_code == 200
    assert r.json()["results"][0]["username"] == "alice"


def test_admin_group_is_renameable_via_env(anon_client, monkeypatch):
    """Operators rename the group via ADMIN_GROUP; the app must follow it."""
    monkeypatch.setenv("ADMIN_GROUP", "fastak_operators")

    assert anon_client.get("/api/users", headers=ADMIN).status_code == 403
    r = anon_client.get("/api/users", headers={"Remote-Groups": "fastak_operators"})
    assert r.status_code != 403


# ── Structural coverage ───────────────────────────────────────────
#
# The behavioural tests above only see routes someone remembered to list. These
# two walk the real app so a newly mounted route cannot quietly land ungated.


def _gate_env_vars(route) -> set[str]:
    """Env-var names of every `require_group` dependency attached to `route`.

    Identifies the dependency by the closure `require_group` returns and reads
    `env_var` out of its cells — the gate is a closure, so there is no attribute
    to inspect and no marker to go stale.
    """

    def _walk(dependant) -> set[str]:
        found = set()
        for dep in dependant.dependencies:
            call = dep.call
            if getattr(call, "__qualname__", "") == "require_group.<locals>._dep":
                cells = dict(
                    zip(
                        call.__code__.co_freevars,
                        (c.cell_contents for c in call.__closure__ or ()),
                    )
                )
                found.add(cells["env_var"])
            found |= _walk(dep)
        return found

    return _walk(route.dependant)


# Prefix -> env var the routes under it must be gated on. `/api/events.csv` is
# listed separately because it is a sibling of `/api/events`, not a child.
GATED_PREFIXES = {
    "/api/users": "ADMIN_GROUP",
    "/api/groups": "ADMIN_GROUP",
    "/api/service-accounts": "ADMIN_GROUP",
    "/api/ops": "ADMIN_GROUP",
    "/api/health": "ADMIN_GROUP",
    "/api/events": "ADMIN_GROUP",
    "/api/events.csv": "ADMIN_GROUP",
    "/api/tak": "ADMIN_GROUP",
    "/ui/partials": "ADMIN_GROUP",
    "/api/backup": "BACKUP_ADMIN_GROUP",
    "/dashboard/backups": "BACKUP_ADMIN_GROUP",
}

# The monitor is an admin console end to end, so the baseline is just the
# liveness probe. Adding a route means gating it or recording it here — a new
# path fails one of the two tests below either way.
UNGATED_BASELINE = {
    "/api/ping",
}


def _api_routes():
    from app.main import app

    # APIRoutes carry a dependant the gate walk can read. Everything else is
    # covered by test_every_route_the_gate_cannot_see_is_recorded.
    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_admin_prefixes_are_gated():
    """Every route under an admin prefix carries the right gate — including
    routes added to those routers after this test was written."""
    for route in _api_routes():
        for prefix, env_var in GATED_PREFIXES.items():
            if route.path == prefix or route.path.startswith(prefix + "/"):
                assert env_var in _gate_env_vars(route), (
                    f"{route.path} is under {prefix} but is not gated on {env_var}"
                )


def test_ungated_routes_match_the_reviewed_baseline():
    """Fails on any newly ungated route, so leaving a gate off is a decision
    someone records here rather than an oversight."""
    open_now = {r.path for r in _api_routes() if not _gate_env_vars(r)}

    newly_open = sorted(open_now - UNGATED_BASELINE)
    newly_gated = sorted(UNGATED_BASELINE - open_now)
    assert not newly_open, f"ungated routes not in the reviewed baseline: {newly_open}"
    assert not newly_gated, f"baseline lists routes that are now gated (stale): {newly_gated}"


def test_gated_schema_routes_serve_the_inventory(anon_client):
    """The gate test only proves admins are not refused. Prove the re-served
    routes work: the document lists the API and not itself, and the Swagger
    page is wired to the gated document rather than FastAPI's default URL."""
    doc = anon_client.get("/api/openapi.json", headers=ADMIN)
    assert doc.status_code == 200
    paths = doc.json()["paths"]
    assert "/api/users" in paths
    assert "/api/openapi.json" not in paths
    assert "/api/docs" not in paths

    page = anon_client.get("/api/docs", headers=ADMIN)
    assert page.status_code == 200
    assert "/api/openapi.json" in page.text


@pytest.mark.parametrize("path", ["/openapi.json", "/redoc", "/docs"])
def test_default_schema_routes_are_not_served(anon_client, path):
    """FastAPI's built-in schema routes are disabled; the only copies are the
    gated ones above. Serving them at the defaults as well would hand the full
    route inventory to any authenticated non-admin (issue #82)."""
    assert anon_client.get(path, headers=ADMIN).status_code == 404


# Routes that are not APIRoutes carry no `dependant`, so the gate checks above
# cannot see them. Anything of that kind must be recorded here on purpose —
# (route type name, path). Today there are none; the first StaticFiles mount
# or plain Route lands in this set with a reason, or fails the test (#83).
#
# Recording a route here says someone looked at it, not that it is protected.
# A `Mount` carries no dependency and no middleware in this app authorizes by
# path (`AuthContextMiddleware` only populates `request.state`), so a mount
# must be gated by its own means — a sub-app that carries the dependency, or
# plain `require_admin` routes — before it is recorded, and each entry carries
# a one-line reason it is safe. The key is `(type name, path)` on purpose: a
# Starlette rename makes the entry stop matching and the test fail loudly.
NON_API_ROUTE_ALLOWLIST: set[tuple[str, str]] = set()


def _unallowlisted(routes) -> list[tuple[str, str]]:
    """(type, path) of every route the gate walk cannot inspect and nobody
    has recorded."""
    return sorted(
        (type(r).__name__, getattr(r, "path", "?"))
        for r in routes
        if not isinstance(r, APIRoute)
        and (type(r).__name__, getattr(r, "path", "?")) not in NON_API_ROUTE_ALLOWLIST
    )


def test_every_route_the_gate_cannot_see_is_recorded():
    """A mount, a sub-app, or a plain Route has no dependant; a websocket route
    does have one (`APIWebSocketRoute`) but it is not an `APIRoute`, so the gate
    walk above skips it either way. Inverting the filter makes an unrecorded one
    fail here instead of vanishing (issue #83)."""
    from app.main import app

    assert _unallowlisted(app.routes) == []
    live_routes = {(type(r).__name__, getattr(r, "path", "?")) for r in app.routes}
    assert NON_API_ROUTE_ALLOWLIST <= live_routes, "stale allowlist entries"


def test_a_stale_allowlist_entry_is_caught():
    """Prove the reverse check above actually works: an allowlist entry for a
    route that no longer exists shows up in the difference against live
    routes, rather than silently matching. Uses a local set — the module-level
    NON_API_ROUTE_ALLOWLIST is never mutated."""
    from app.main import app

    fake_allowlist = {("Mount", "/no-longer-mounted")}
    live_routes = {(type(r).__name__, getattr(r, "path", "?")) for r in app.routes}
    assert fake_allowlist - live_routes, "a stale entry must not be a subset of live routes"


def test_the_check_catches_the_routes_the_issue_demonstrated(tmp_path):
    """Issue #83 mounted StaticFiles at /files and inserted a plain Route at
    /api/ca-export; both passed the structural tests. Prove they fail now —
    on a copy of the route list, never the live app."""
    from app.main import app
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    async def secret_handler(request):
        return JSONResponse({"ca_key": "supersecret"})

    routes = [
        Mount("/files", app=StaticFiles(directory=str(tmp_path))),
        Route("/api/ca-export", secret_handler),
        *app.routes,
    ]
    assert _unallowlisted(routes) == [("Mount", "/files"), ("Route", "/api/ca-export")]

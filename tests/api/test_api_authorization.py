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

    # Starlette's own routes (/docs, /openapi.json, static mounts) have no
    # dependant and carry no app data.
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

"""Authorization gate for the admin JSON APIs (issue #52).

The user, service-account, and ops routers must reject callers who are not in
the admin group. Authentication (a valid LDAP bind) is handled upstream by
Caddy `forward_auth` -> ldap-proxy, which sets `Remote-Groups`; these routers
must additionally *authorize* on that group, mirroring the backup router.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with (
        patch("app.main.init_config_hash"),
        patch("app.main.start_scheduler"),
        patch("app.main.stop_scheduler"),
    ):
        from app.main import app

        with TestClient(app) as c:
            yield c


NONADMIN = {"Remote-User": "bob", "Remote-Groups": "tak_alpha"}
ADMIN = {"Remote-User": "alice", "Remote-Groups": "monitor_admin"}

# (method, path) pairs that must be admin-gated. One representative route per router.
GATED_ROUTES = [
    ("get", "/api/users"),
    ("post", "/api/service-accounts"),
    ("post", "/api/ops/database/vacuum"),
]


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_rejects_missing_admin_group(client, method, path):
    r = getattr(client, method)(path, headers=NONADMIN)
    assert r.status_code == 403, f"{method} {path} was not gated (got {r.status_code})"


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_rejects_no_groups_at_all(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 403, f"{method} {path} allowed an unauthenticated caller"


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_admin_group_passes_the_gate(client, method, path):
    """Admin must get *past* the auth gate (not 403). Downstream may 4xx/5xx on
    unmocked dependencies — we only assert the gate itself does not reject."""
    r = getattr(client, method)(path, headers=ADMIN)
    assert r.status_code != 403, f"{method} {path} rejected a legitimate admin"

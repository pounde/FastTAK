"""Tests for /api/tak/* proxy endpoints.

The default Remote-Groups header is forward-compatible with #21's agency
filter — once that lands, an unauthenticated test caller would otherwise
receive [] from filtered endpoints. The header is harmless until then.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(mock_settings):
    """TestClient with the TAK Server client mocked.

    `mock_settings` (from tests/conftest.py) seeds settings so that
    `_get_tak_server` won't raise. The fake _client = MagicMock() satisfies
    the truthy check in the 503 guard.
    """
    from app.main import app

    fake = MagicMock()
    fake._client = MagicMock()  # truthy — passes the 503 guard
    with patch("app.api.tak.router._get_tak_server", return_value=fake):
        client = TestClient(
            app,
            headers={"Remote-Groups": "fastak_admin", "Remote-User": "tester"},
        )
        yield client, fake


def test_list_groups_proxies_to_tak_server(app_client):
    client, fake = app_client
    fake.list_groups.return_value = [{"name": "Blue", "direction": "OUT"}]
    r = client.get("/api/tak/groups")
    assert r.status_code == 200
    assert r.json() == [{"name": "Blue", "direction": "OUT"}]


def test_list_groups_503_when_tak_client_unavailable(app_client):
    client, _ = app_client
    with patch("app.api.tak.router._get_tak_server", return_value=None):
        r = client.get("/api/tak/groups")
    assert r.status_code == 503


def test_list_clients_proxies_to_tak_server(app_client):
    client, fake = app_client
    fake.list_clients.return_value = [
        {"callsign": "ALPHA-1", "uid": "ANDROID-abc", "groups": ["Blue"]}
    ]
    r = client.get("/api/tak/clients")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "ALPHA-1"
    assert "lkp" not in body[0]  # not requested

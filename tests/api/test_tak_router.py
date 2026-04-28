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


def test_list_clients_with_lkp_enriches_response(app_client):
    client, fake = app_client
    fake.list_clients.return_value = [
        {"callsign": "ALPHA-1", "uid": "ANDROID-abc", "groups": ["Blue"]}
    ]
    with patch(
        "app.api.tak.router.get_lkp_for_uids",
        return_value={
            "ANDROID-abc": {
                "uid": "ANDROID-abc",
                "lat": 38.8,
                "lon": -77.0,
                "hae": 100.0,
                "servertime": "2026-04-27T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
            }
        },
    ):
        r = client.get("/api/tak/clients?include=lkp")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["lkp"]["lat"] == 38.8


def test_list_clients_with_lkp_null_when_no_position(app_client):
    client, fake = app_client
    fake.list_clients.return_value = [
        {"callsign": "ALPHA-1", "uid": "ANDROID-abc", "groups": ["Blue"]}
    ]
    with patch("app.api.tak.router.get_lkp_for_uids", return_value={}):
        r = client.get("/api/tak/clients?include=lkp")
    body = r.json()
    assert body[0]["lkp"] is None


def test_list_clients_filters_service_accounts_by_default(app_client):
    """Subscriptions whose username matches USERS_HIDDEN_PREFIXES are dropped."""
    client, fake = app_client
    fake.list_clients.return_value = [
        # Service accounts — should be filtered out
        {"callsign": "tls:5", "uid": "", "username": "svc_nr2"},
        {"callsign": "tls:3", "uid": "", "username": "svc_adsb"},
        {"callsign": "tls:1", "uid": "", "username": "adm_console"},
        {"callsign": "tls:2", "uid": "", "username": "ma-bridge"},
        # Real user — should remain
        {"callsign": "ALPHA-1", "uid": "ANDROID-abc", "username": "epound"},
    ]
    r = client.get("/api/tak/clients")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "ALPHA-1"


def test_list_clients_include_service_returns_full_list(app_client):
    """Opt-in flag returns the full subscription list including service accounts."""
    client, fake = app_client
    fake.list_clients.return_value = [
        {"callsign": "tls:5", "uid": "", "username": "svc_nr2"},
        {"callsign": "ALPHA-1", "uid": "ANDROID-abc", "username": "epound"},
    ]
    r = client.get("/api/tak/clients?include_service=true")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert {c["callsign"] for c in body} == {"tls:5", "ALPHA-1"}


def test_list_clients_keeps_entries_without_username(app_client):
    """Subscriptions with no username field aren't dropped by the filter."""
    client, fake = app_client
    fake.list_clients.return_value = [
        {"callsign": "ALPHA-1", "uid": "ANDROID-abc"},  # no username key
        {"callsign": "BRAVO-2", "uid": "ANDROID-xyz", "username": ""},  # empty username
    ]
    r = client.get("/api/tak/clients")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2


def test_list_contacts_proxies_to_tak_server(app_client):
    client, fake = app_client
    fake.list_contacts.return_value = [
        {"uid": "ANDROID-abc", "callsign": "ALPHA-1", "lastReportTime": 1719500000000}
    ]
    r = client.get("/api/tak/contacts")
    assert r.status_code == 200
    assert r.json()[0]["uid"] == "ANDROID-abc"


def test_list_contacts_filters_service_accounts_by_default(app_client):
    """Marti carries the actor in `notes` (often with leading whitespace)."""
    client, fake = app_client
    fake.list_contacts.return_value = [
        # Service accounts — leading-space `notes` is TAK Server's quirk
        {"uid": "", "callsign": "", "notes": " svc_wx"},
        {"uid": "", "callsign": "", "notes": " svc_adsb"},
        {"uid": "", "callsign": "", "notes": " adm_console"},
        # Real user
        {"uid": "ANDROID-abc", "callsign": "ALPHA-1", "notes": "epound"},
    ]
    r = client.get("/api/tak/contacts")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "ALPHA-1"


def test_list_contacts_include_service_returns_full_list(app_client):
    client, fake = app_client
    fake.list_contacts.return_value = [
        {"uid": "", "callsign": "", "notes": " svc_wx"},
        {"uid": "ANDROID-abc", "callsign": "ALPHA-1", "notes": "epound"},
    ]
    r = client.get("/api/tak/contacts?include_service=true")
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_recent_contacts_combines_marti_with_lkp_no_default_age(app_client):
    """Default behaviour: no max_age, so the LKP query has no time filter."""
    client, fake = app_client
    fake.list_contacts.return_value = [
        {
            "uid": "ANDROID-abc",
            "callsign": "ALPHA-1",
            "team": "Blue",
            "lastReportTime": 1719500000000,
        },
        {
            "uid": "ANDROID-xyz",
            "callsign": "BRAVO-2",
            "team": "Red",
            "lastReportTime": 1719500000000,
        },
    ]
    with patch(
        "app.api.tak.router.get_recent_contacts_with_lkp",
        return_value=[
            {
                "uid": "ANDROID-abc",
                "lat": 38.8,
                "lon": -77.0,
                "hae": 100.0,
                "servertime": "2026-04-27T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
            }
        ],
    ) as mock_lkp:
        r = client.get("/api/tak/contacts/recent")
    assert r.status_code == 200
    # Confirm the helper was called WITHOUT max_age (relying on TAK retention)
    _, kwargs = mock_lkp.call_args
    assert kwargs.get("max_age_seconds") is None
    body = r.json()
    by_uid = {c["uid"]: c for c in body}
    assert by_uid["ANDROID-abc"]["lkp"]["lat"] == 38.8
    assert by_uid["ANDROID-xyz"]["lkp"] is None  # never reported a position


def test_recent_contacts_passes_max_age_when_set(app_client):
    client, fake = app_client
    fake.list_contacts.return_value = [
        {"uid": "ANDROID-abc", "callsign": "ALPHA-1", "team": "Blue"},
    ]
    with patch(
        "app.api.tak.router.get_recent_contacts_with_lkp",
        return_value=[],
    ) as mock_lkp:
        r = client.get("/api/tak/contacts/recent?max_age=3600")
    assert r.status_code == 200
    _, kwargs = mock_lkp.call_args
    assert kwargs["max_age_seconds"] == 3600


def test_recent_contacts_filters_service_accounts_by_default(app_client):
    """Service-account contacts (notes = svc_*) are dropped before LKP lookup."""
    client, fake = app_client
    fake.list_contacts.return_value = [
        {"uid": "", "callsign": "", "notes": " svc_wx"},
        {"uid": "ANDROID-abc", "callsign": "ALPHA-1", "notes": "epound"},
    ]
    with patch(
        "app.api.tak.router.get_recent_contacts_with_lkp",
        return_value=[],
    ) as mock_lkp:
        r = client.get("/api/tak/contacts/recent")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "ALPHA-1"
    # Only the real user's UID is passed to the LKP query
    args, _ = mock_lkp.call_args
    assert args[0] == ["ANDROID-abc"]


def test_list_missions_proxies_to_tak_server(app_client):
    client, fake = app_client
    fake.list_missions.return_value = [{"name": "ops-2026-04"}]
    r = client.get("/api/tak/missions")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "ops-2026-04"

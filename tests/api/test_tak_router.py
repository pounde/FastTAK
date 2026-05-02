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


def test_recent_contacts_uses_cot_router_as_source(app_client):
    """UIDs come from cot_router; /contacts/all only enriches."""
    client, fake = app_client
    fake.list_contacts.return_value = [
        {
            "uid": "ANDROID-abc",
            "callsign": "ALPHA-1",
            "team": "Blue",
            "role": "HQ",
            "notes": "epound",
        },
    ]
    with patch(
        "app.api.tak.router.get_recent_lkp",
        return_value=[
            {
                "uid": "ANDROID-abc",
                "lat": 38.8,
                "lon": -77.0,
                "hae": 100.0,
                "servertime": "2026-05-01T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
                "detail": {},
            }
        ],
    ) as mock_lkp:
        r = client.get("/api/tak/contacts/recent?max_age=3600")
    assert r.status_code == 200
    args, _ = mock_lkp.call_args
    assert args[0] == 3600  # max_age forwarded
    body = r.json()
    assert len(body) == 1
    assert body[0]["uid"] == "ANDROID-abc"
    assert body[0]["callsign"] == "ALPHA-1"  # from roster
    assert body[0]["lkp"]["lat"] == 38.8


def test_recent_contacts_default_max_age_is_24h(app_client):
    """Omitted max_age defaults to 86400 (the card's default window)."""
    client, fake = app_client
    fake.list_contacts.return_value = []
    with patch("app.api.tak.router.get_recent_lkp", return_value=[]) as mock_lkp:
        r = client.get("/api/tak/contacts/recent")
    assert r.status_code == 200
    args, _ = mock_lkp.call_args
    assert args[0] == 86400


def test_recent_contacts_falls_back_to_detail_xml_when_uid_not_in_roster(app_client):
    """The whole point of this rewrite: UIDs aged off /contacts/all still render."""
    client, fake = app_client
    fake.list_contacts.return_value = []  # roster wiped (e.g. after TAK restart)
    with patch(
        "app.api.tak.router.get_recent_lkp",
        return_value=[
            {
                "uid": "ANDROID-aged-off",
                "lat": 38.8,
                "lon": -77.0,
                "hae": 100.0,
                "servertime": "2026-05-01T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
                "detail": {"callsign": "DETAIL-CS", "team": "Cyan", "role": "Member"},
            }
        ],
    ):
        r = client.get("/api/tak/contacts/recent")
    body = r.json()
    assert len(body) == 1
    assert body[0]["callsign"] == "DETAIL-CS"  # from detail XML
    assert body[0]["team"] == "Cyan"
    assert body[0]["role"] == "Member"
    assert body[0]["takv"] is None  # roster-only field
    assert body[0]["notes"] is None


def test_recent_contacts_renders_uid_when_no_enrichment_at_all(app_client):
    """No roster, no detail XML — still render the UID + LKP."""
    client, fake = app_client
    fake.list_contacts.return_value = []
    with patch(
        "app.api.tak.router.get_recent_lkp",
        return_value=[
            {
                "uid": "ANDROID-naked",
                "lat": 38.8,
                "lon": -77.0,
                "hae": None,
                "servertime": "2026-05-01T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
                "detail": {},
            }
        ],
    ):
        r = client.get("/api/tak/contacts/recent")
    body = r.json()
    assert body[0]["uid"] == "ANDROID-naked"
    assert body[0]["callsign"] is None
    assert body[0]["lkp"]["lat"] == 38.8


def test_recent_contacts_drops_service_accounts_when_in_roster(app_client):
    """Roster-known service accounts (notes matches prefix) are dropped."""
    client, fake = app_client
    fake.list_contacts.return_value = [
        {"uid": "SVC-WX-1", "callsign": "WX", "notes": " svc_wx"},
        {"uid": "ANDROID-real", "callsign": "ALPHA-1", "notes": "epound"},
    ]
    with patch(
        "app.api.tak.router.get_recent_lkp",
        return_value=[
            {
                "uid": "SVC-WX-1",
                "lat": 0.0,
                "lon": 0.0,
                "hae": 0.0,
                "servertime": "2026-05-01T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
                "detail": {},
            },
            {
                "uid": "ANDROID-real",
                "lat": 38.8,
                "lon": -77.0,
                "hae": 100.0,
                "servertime": "2026-05-01T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
                "detail": {},
            },
        ],
    ):
        r = client.get("/api/tak/contacts/recent")
    body = r.json()
    assert {c["uid"] for c in body} == {"ANDROID-real"}


def test_recent_contacts_keeps_uid_not_in_roster_even_if_could_be_service(app_client):
    """Fail-open: UIDs not in /contacts/all can't be classified, so render them."""
    client, fake = app_client
    fake.list_contacts.return_value = []  # nothing in roster to classify against
    with patch(
        "app.api.tak.router.get_recent_lkp",
        return_value=[
            {
                "uid": "MAYBE-SVC",
                "lat": 0.0,
                "lon": 0.0,
                "hae": 0.0,
                "servertime": "2026-05-01T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
                "detail": {},
            },
        ],
    ):
        r = client.get("/api/tak/contacts/recent")
    assert len(r.json()) == 1


def test_recent_contacts_degrades_when_tak_http_503s(app_client):
    """list_contacts raising HTTPException(503) — cot_router rows still render."""
    from fastapi import HTTPException

    client, fake = app_client
    fake.list_contacts.side_effect = HTTPException(503, "TAK offline")
    with patch(
        "app.api.tak.router.get_recent_lkp",
        return_value=[
            {
                "uid": "ANDROID-abc",
                "lat": 38.8,
                "lon": -77.0,
                "hae": 100.0,
                "servertime": "2026-05-01T12:00:00+00:00",
                "cot_type": "a-f-G-U-C",
                "detail": {"callsign": "FROM-DETAIL"},
            },
        ],
    ):
        r = client.get("/api/tak/contacts/recent")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["callsign"] == "FROM-DETAIL"


def test_recent_contacts_propagates_db_error_as_500(app_client):
    """get_recent_lkp raising bubbles up — caller sees a real error, not silent []."""
    client, fake = app_client
    fake.list_contacts.return_value = []
    with patch(
        "app.api.tak.router.get_recent_lkp",
        side_effect=RuntimeError("db down"),
    ):
        with pytest.raises(RuntimeError, match="db down"):
            client.get("/api/tak/contacts/recent")


def test_list_missions_proxies_to_tak_server(app_client):
    client, fake = app_client
    fake.list_missions.return_value = [{"name": "ops-2026-04"}]
    r = client.get("/api/tak/missions")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "ops-2026-04"


# --- Dashboard partial: /ui/partials/recent-contacts ---


def test_ui_recent_contacts_default_window_is_24h(app_client):
    client, fake = app_client
    fake.list_contacts.return_value = []
    with patch("app.api.tak.router.get_recent_lkp", return_value=[]) as mock_lkp:
        r = client.get("/ui/partials/recent-contacts")
    assert r.status_code == 200
    # 86400 == 24h default
    args, _ = mock_lkp.call_args
    assert args[0] == 86400
    # Dropdown rendered with 24h selected
    assert "selected" in r.text
    assert 'value="86400"' in r.text


def test_ui_recent_contacts_honors_max_age_query_param(app_client):
    client, fake = app_client
    fake.list_contacts.return_value = []
    with patch("app.api.tak.router.get_recent_lkp", return_value=[]) as mock_lkp:
        r = client.get("/ui/partials/recent-contacts?max_age=604800")
    args, _ = mock_lkp.call_args
    assert args[0] == 604800
    # 1-week option marked selected, not 24h
    assert 'value="604800"' in r.text


def test_ui_recent_contacts_renders_all_window_options(app_client):
    client, fake = app_client
    fake.list_contacts.return_value = []
    with patch("app.api.tak.router.get_recent_lkp", return_value=[]):
        r = client.get("/ui/partials/recent-contacts")
    for label in ["24h", "48h", "96h", "1 week", "1 month"]:
        assert label in r.text


def test_ui_recent_contacts_renders_error_when_helper_raises(app_client):
    client, fake = app_client
    fake.list_contacts.return_value = []
    with patch(
        "app.api.tak.router.get_recent_lkp",
        side_effect=RuntimeError("db down"),
    ):
        r = client.get("/ui/partials/recent-contacts")
    # Error renders inside the partial, not a 500 — by design for graceful UI
    assert r.status_code == 200
    assert "unavailable" in r.text.lower() or "db down" in r.text

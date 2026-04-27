"""Tests for /api/events router."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(mock_settings):
    from app.main import app

    return TestClient(app)


def test_list_events_filters_by_source(app_client):
    rows = [
        {
            "id": 1,
            "timestamp": datetime(2026, 4, 27, 12, tzinfo=UTC),
            "source": "audit",
            "actor": "alice",
            "action": "POST /api/users",
            "target_type": None,
            "target_id": None,
            "detail": {},
            "ip": None,
            "agency_id": None,
        },
    ]
    with patch("app.api.events.router.fetch", return_value=rows) as mock_fetch:
        r = app_client.get("/api/events?source=audit&limit=50")
    assert r.status_code == 200
    sql = mock_fetch.call_args.args[0]
    assert "source = %s" in sql
    body = r.json()
    assert body["count"] == 1
    assert body["events"][0]["actor"] == "alice"


def test_list_events_default_limit_caps_at_max(app_client):
    with patch("app.api.events.router.fetch", return_value=[]):
        r = app_client.get("/api/events?limit=10000")
    # FastAPI rejects out-of-range Query values with 422
    assert r.status_code == 422


def test_list_events_no_filters_omits_where_clause(app_client):
    with patch("app.api.events.router.fetch", return_value=[]) as mock_fetch:
        app_client.get("/api/events")
    sql = mock_fetch.call_args.args[0]
    assert "WHERE" not in sql
    assert "FROM fastak_events ORDER BY timestamp DESC" in sql


def test_csv_export_emits_text_csv(app_client):
    rows = [
        {
            "id": 1,
            "timestamp": datetime(2026, 4, 27, 12, tzinfo=UTC),
            "source": "audit",
            "actor": "alice",
            "action": "POST /api/users",
            "target_type": "user",
            "target_id": "42",
            "detail": {"k": "v"},
            "ip": "10.0.0.5",
            "agency_id": None,
        },
    ]
    with patch("app.api.events.router.fetch", return_value=rows):
        r = app_client.get("/api/events.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    body = r.text
    assert "alice" in body
    assert "POST /api/users" in body
    # detail flattened to compact JSON; csv.writer escapes embedded quotes by doubling them
    assert '"{""k"":""v""}"' in body


def test_csv_export_empty_returns_header_only(app_client):
    with patch("app.api.events.router.fetch", return_value=[]):
        r = app_client.get("/api/events.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    body = r.text.strip().splitlines()
    assert len(body) == 1  # header row only, no data rows
    assert body[0].split(",") == [
        "id",
        "timestamp",
        "source",
        "actor",
        "action",
        "target_type",
        "target_id",
        "detail",
        "ip",
        "agency_id",
    ]


def test_csv_export_nullable_columns_emit_empty_strings(app_client):
    rows = [
        {
            "id": 1,
            "timestamp": datetime(2026, 4, 27, 12, tzinfo=UTC),
            "source": "audit",
            "actor": "alice",
            "action": "POST /api/users",
            "target_type": None,
            "target_id": None,
            "detail": None,
            "ip": None,
            "agency_id": None,
        },
    ]
    with patch("app.api.events.router.fetch", return_value=rows):
        r = app_client.get("/api/events.csv")
    assert r.status_code == 200
    # Data row should have empty fields, not "None"
    data_row = r.text.strip().splitlines()[1]
    assert ",None," not in data_row
    assert "None\r" not in data_row and not data_row.endswith("None")

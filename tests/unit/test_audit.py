"""Tests for the audit / event-store module."""

from unittest.mock import patch


def test_init_schema_runs_create_table_idempotently():
    from app.audit import init_schema

    with patch("app.audit.execute") as mock_exec:
        init_schema()
    sqls = [c.args[0] for c in mock_exec.call_args_list]
    create_calls = [s for s in sqls if "CREATE TABLE IF NOT EXISTS fastak_events" in s]
    assert len(create_calls) == 1
    # Must include agency_id (forward-compat with #21)
    assert "agency_id" in create_calls[0]
    # Must include the four required indexes
    index_calls = [s for s in sqls if "CREATE INDEX IF NOT EXISTS" in s]
    assert len(index_calls) >= 4


def test_record_event_inserts_row():
    from app.audit import record_event

    with patch("app.audit.execute") as mock_exec:
        record_event(
            source="audit",
            actor="alice",
            action="user.create",
            target_type="user",
            target_id="42",
            detail={"name": "Alice", "groups": ["tak_Blue"]},
            ip="10.0.0.5",
        )
    assert mock_exec.called
    sql, params = mock_exec.call_args.args
    assert "INSERT INTO fastak_events" in sql
    # Column-order matches signature: (source, actor, action, target_type,
    # target_id, detail, ip, agency_id) — guards against transposition.
    assert len(params) == 8
    assert params[0] == "audit"
    assert params[1] == "alice"
    assert params[2] == "user.create"
    assert params[3] == "user"
    assert params[4] == "42"
    # detail is JSON-encoded for the JSONB column
    import json

    assert json.loads(params[5]) == {"name": "Alice", "groups": ["tak_Blue"]}
    assert params[6] == "10.0.0.5"
    assert params[7] is None


def test_record_event_swallows_db_errors():
    import psycopg
    from app.audit import record_event

    with patch("app.audit.execute", side_effect=psycopg.OperationalError("db down")):
        # Must NOT raise — audit writes never break user requests
        record_event("audit", "alice", "user.create")

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

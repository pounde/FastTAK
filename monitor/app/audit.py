"""Audit event store: schema bootstrap, recording, and middleware.

Schema (fastak_events):
- id            BIGSERIAL PRIMARY KEY
- timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW()
- source        TEXT NOT NULL  -- 'audit' or 'health'
- actor         TEXT NOT NULL  -- username, 'system', or 'unknown'
- action        TEXT NOT NULL  -- 'POST /api/users', 'unhealthy', etc.
- target_type   TEXT           -- 'user', 'group', 'cert', 'service'
- target_id     TEXT           -- string id of the affected resource
- detail        JSONB          -- structured context (sanitised request body)
- ip            INET           -- source IP for audit events
- agency_id     UUID           -- nullable; populated once #21 lands
"""

from __future__ import annotations

import logging

from app.fastak_db import execute

log = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fastak_events (
    id            BIGSERIAL PRIMARY KEY,
    timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source        TEXT NOT NULL,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    target_type   TEXT,
    target_id     TEXT,
    detail        JSONB,
    ip            INET,
    agency_id     UUID
)
"""

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_fastak_events_timestamp ON fastak_events (timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fastak_events_source_ts "
    "ON fastak_events (source, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fastak_events_actor_ts "
    "ON fastak_events (actor, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fastak_events_agency_ts "
    "ON fastak_events (agency_id, timestamp DESC) WHERE agency_id IS NOT NULL",
]


def init_schema() -> None:
    """Idempotent — safe to run on every startup."""
    execute(SCHEMA_SQL)
    for sql in INDEX_SQL:
        execute(sql)

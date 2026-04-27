"""GET /api/events — query the fastak_events table."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query

from app.fastak_db import fetch

router = APIRouter(tags=["events"])

MAX_LIMIT = 500
DEFAULT_LIMIT = 50


def _build_query(
    source: str | None,
    actor: str | None,
    action: str | None,
    target_type: str | None,
    target_id: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> tuple[str, tuple[Any, ...]]:
    where: list[str] = []
    params: list[Any] = []
    if source:
        where.append("source = %s")
        params.append(source)
    if actor:
        where.append("actor = %s")
        params.append(actor)
    if action:
        where.append("action = %s")
        params.append(action)
    if target_type:
        where.append("target_type = %s")
        params.append(target_type)
    if target_id:
        where.append("target_id = %s")
        params.append(target_id)
    if since:
        where.append("timestamp >= %s")
        params.append(since)
    if until:
        where.append("timestamp <= %s")
        params.append(until)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, timestamp, source, actor, action, target_type, target_id, "
        "detail, ip::text AS ip, agency_id "
        f"FROM fastak_events{where_sql} "
        "ORDER BY timestamp DESC "
        "LIMIT %s"
    )
    params.append(limit)
    return sql, tuple(params)


@router.get("/api/events", summary="Query events")
def list_events(
    source: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None, description="ISO-8601 timestamp"),
    until: datetime | None = Query(default=None, description="ISO-8601 timestamp"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
):
    sql, params = _build_query(source, actor, action, target_type, target_id, since, until, limit)
    rows = fetch(sql, params)
    return {"count": len(rows), "events": rows}

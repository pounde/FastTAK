"""GET /api/events — query the fastak_events table."""

import csv
import io
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

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


def _flatten_detail(detail: Any | None) -> str:
    if detail is None:
        return ""
    try:
        return json.dumps(detail, separators=(",", ":"))
    except Exception:
        return str(detail)


@router.get("/api/events.csv", summary="Export events as CSV")
def export_csv(
    source: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None, description="ISO-8601 timestamp"),
    until: datetime | None = Query(default=None, description="ISO-8601 timestamp"),
    limit: int = Query(default=MAX_LIMIT, ge=1, le=MAX_LIMIT),
):
    sql, params = _build_query(source, actor, action, target_type, target_id, since, until, limit)
    rows = fetch(sql, params)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
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
    )
    for r in rows:
        writer.writerow(
            [
                r["id"],
                r["timestamp"].isoformat(),
                r["source"],
                r["actor"],
                r["action"],
                r["target_type"] or "",
                r["target_id"] or "",
                _flatten_detail(r["detail"]),
                r["ip"] or "",
                str(r["agency_id"] or ""),
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="fastak_events.csv"'},
    )

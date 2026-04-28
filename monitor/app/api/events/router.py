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
    # When #21 adds agency-scoped filtering, the agency clause MUST treat
    # `agency_id IS NULL` as visible to non-superadmins — see DD-040. Pre-#21
    # rows have NULL agency_id and would otherwise disappear from view.
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
    """Query the `fastak_events` audit log with optional filters.

    Returns events in reverse-chronological order. All filter parameters are
    ANDed together; omit any to broaden the result set. See DD-040 for the
    schema design and agency-scoping considerations.

    Args:
        source: One of `"audit"` (mutating API call) or `"health"` (container
            state transition / alert / recovery).
        actor: Username from `Remote-User`, or `"system"` for health events,
            or `"unknown"` when no auth header was forwarded.
        action: For audit rows, `"<METHOD> <path>"` (e.g. `"POST /api/users"`).
            For health rows, the alert level (`"critical"`, `"warning"`,
            `"recovered"`, etc.).
        target_type: Resource category — typically `"user"`, `"group"`,
            `"cert"`, or `"service"`. NULL for audit rows that haven't been
            enriched per-route yet.
        target_id: Specific resource identifier within `target_type` (e.g.
            the service name for health rows).
        since: Inclusive lower bound on `timestamp` (ISO-8601 on the wire,
            parsed to `datetime`). Use with `until` to constrain a window.
        until: Inclusive upper bound on `timestamp`.
        limit: Maximum rows to return (1–500, default 50).

    Returns:
        JSON object with `count` (int) and `events` (list of event dicts),
        each containing `id`, `timestamp`, `source`, `actor`, `action`,
        `target_type`, `target_id`, `detail`, `ip`, and `agency_id`.
        `agency_id` is NULL until issue #21 lands.
    """
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
    """Export the `fastak_events` audit log as a downloadable CSV file.

    Accepts the same filter parameters as `GET /api/events`. The response is
    streamed with `Content-Disposition: attachment` so browsers prompt a
    file-save dialog. The `detail` column is JSON-minified into a single string
    field to keep the CSV flat.

    Args:
        source: Filter by emitting subsystem.
        actor: Filter by principal identity.
        action: Filter by event verb.
        target_type: Filter by resource category.
        target_id: Filter by specific resource identifier.
        since: Inclusive lower bound on `timestamp` (ISO-8601).
        until: Inclusive upper bound on `timestamp` (ISO-8601).
        limit: Maximum rows to include (1–500, default 500).

    Returns:
        `text/csv` stream with columns: `id`, `timestamp`, `source`, `actor`,
        `action`, `target_type`, `target_id`, `detail`, `ip`, `agency_id`.
        Filename is `fastak_events.csv`.
    """
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

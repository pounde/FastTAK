"""Last-known-position queries against TAK Server's cot_router table.

cot_router is TAK-Server-internal — the schema can shift between TAK Server
versions. The queries here assume columns uid, point_hae, servertime,
cot_type, and event_pt (PostGIS Point). Lat/lon are extracted with
ST_Y(event_pt) / ST_X(event_pt). Confirmed against TAK 5.x as of 2026-04.

The cot database is encoded as SQL_ASCII, so psycopg returns text columns
as bytes; ``_decode`` on uid/cot_type normalises them to str. If the schema
or encoding changes, this module needs revisiting.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from app.db import query

log = logging.getLogger(__name__)


def _decode(v):
    """SQL_ASCII columns come back as bytes from psycopg; decode to str."""
    if isinstance(v, bytes | bytearray):
        return v.decode("utf-8", errors="replace")
    return v


def _parse_detail(xml_data: str | bytes | None) -> dict:
    """Best-effort extraction of callsign/team/role from a CoT detail blob.

    Returns {} on empty input, malformed XML, or any other failure. Logged
    at DEBUG only — malformed CoT is common in the wild and warning-level
    would flood logs.
    """
    if not xml_data:
        return {}
    if isinstance(xml_data, bytes | bytearray):
        xml_data = xml_data.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        log.debug("parse_detail: malformed XML: %s", exc)
        return {}

    out: dict = {}
    # If the root itself is <contact>, treat it as the contact element.
    contact = root if root.tag == "contact" else root.find(".//contact")
    if contact is not None:
        callsign = contact.get("callsign")
        if callsign:
            out["callsign"] = callsign
    group = root if root.tag == "__group" else root.find(".//__group")
    if group is not None:
        team = group.get("name")
        role = group.get("role")
        if team:
            out["team"] = team
        if role:
            out["role"] = role
    return out


def _row_to_position(row: tuple) -> dict:
    uid, lat, lon, hae, servertime, cot_type = row
    return {
        "uid": _decode(uid),
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
        "hae": float(hae) if hae is not None else None,
        "servertime": servertime.isoformat() if hasattr(servertime, "isoformat") else servertime,
        "cot_type": _decode(cot_type),
    }


def get_lkp_for_uids(uids: Iterable[str]) -> dict[str, dict]:
    """Return {uid: position-dict} for the most recent cot_router row per uid.

    UIDs absent from cot_router are simply not in the returned dict.
    Empty input short-circuits without hitting the database.
    """
    uids = list(uids)
    if not uids:
        return {}

    placeholders = ",".join(["%s"] * len(uids))
    sql = f"""
        SELECT DISTINCT ON (uid)
            uid, ST_Y(event_pt) AS lat, ST_X(event_pt) AS lon,
            point_hae, servertime, cot_type
        FROM cot_router
        WHERE uid IN ({placeholders})
        ORDER BY uid, servertime DESC
    """
    try:
        rows = query(sql, tuple(uids))
    except Exception as exc:
        log.warning("LKP query failed: %s", exc)
        return {}

    return {_decode(row[0]): _row_to_position(row) for row in rows}


def get_recent_contacts_with_lkp(
    contact_uids: Iterable[str],
    max_age_seconds: int | None = None,
) -> list[dict]:
    """Return the latest position for any contact UID seen, optionally bounded.

    When max_age_seconds is None, no FastTAK-side time filter is applied —
    the caller relies on TAK Server's own contact retention to bound the
    set of contact_uids passed in.

    contact_uids should come from Marti's /contacts/all roster, which acts
    as the implicit "humans only" filter (drone tracks, sensor pings, and
    other non-human CoT never appear there).
    """
    contact_uids = list(contact_uids)
    if not contact_uids:
        return []

    placeholders = ",".join(["%s"] * len(contact_uids))
    params: list = list(contact_uids)
    where_clauses = [f"uid IN ({placeholders})"]
    if max_age_seconds is not None:
        where_clauses.append("servertime >= NOW() - make_interval(secs => %s)")
        params.append(int(max_age_seconds))
    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT DISTINCT ON (uid)
            uid, ST_Y(event_pt) AS lat, ST_X(event_pt) AS lon,
            point_hae, servertime, cot_type
        FROM cot_router
        WHERE {where_sql}
        ORDER BY uid, servertime DESC
    """
    try:
        rows = query(sql, tuple(params))
    except Exception as exc:
        log.warning("recent-contacts LKP query failed: %s", exc)
        return []

    return [_row_to_position(row) for row in rows]


def get_recent_lkp(
    max_age_seconds: int,
    cot_type_prefixes: list[str],
) -> list[dict]:
    """Latest position per UID from cot_router, filtered by age + cot_type.

    Source-of-truth for the "Recently seen" dashboard card. Drives the UID
    list itself (not just the position lookup), so entries persist across
    TAK Server restarts that wipe the in-memory contact roster.

    Args:
        max_age_seconds: Window in seconds. Rows with servertime older than
            NOW() - this value are excluded.
        cot_type_prefixes: Lowercase prefixes matched via ILIKE. Empty list
            short-circuits and returns [] without hitting the DB (defensive
            against accidentally matching every CoT type).

    Returns:
        List of position dicts: uid, lat, lon, hae, servertime, cot_type,
        detail (parsed dict from cot_router.detail XML; {} on missing/bad).

    Does NOT swallow DB errors — they propagate so the caller can render an
    error state. `get_recent_contacts_with_lkp` (the older sibling) DOES
    swallow, which makes "DB broken" indistinguishable from "no data";
    that's a known-bad pattern we explicitly reverse here.
    """
    if not cot_type_prefixes:
        return []

    like_patterns = [f"{p}%" for p in cot_type_prefixes]
    sql = """
        SELECT DISTINCT ON (uid)
            uid, ST_Y(event_pt) AS lat, ST_X(event_pt) AS lon,
            point_hae, servertime, cot_type, detail
        FROM cot_router
        WHERE servertime >= NOW() - make_interval(secs => %s)
          AND cot_type ILIKE ANY(%s)
        ORDER BY uid, servertime DESC
    """
    rows = query(sql, (max_age_seconds, like_patterns))

    results = []
    for row in rows:
        uid, lat, lon, hae, servertime, cot_type, detail = row
        results.append(
            {
                "uid": _decode(uid),
                "lat": float(lat) if lat is not None else None,
                "lon": float(lon) if lon is not None else None,
                "hae": float(hae) if hae is not None else None,
                "servertime": servertime.isoformat()
                if hasattr(servertime, "isoformat")
                else servertime,
                "cot_type": _decode(cot_type),
                "detail": _parse_detail(detail),
            }
        )
    return results

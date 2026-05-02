"""TAK Server proxy router — exposes Marti API as HTTP-only endpoints.

Each /api/tak/* endpoint accepts an optional ``?agency=<uuid>`` query
parameter that is currently a no-op. It is reserved for the agency-scoping
work in #21 and ignored until that lands.

Request-free helpers (_build_*_response) are exposed for dashboard partials
to consume without needing a FastAPI Request object. Route handlers call
the helpers and add request-scoped concerns (eventually agency filtering).
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.api.tak.positions import get_lkp_for_uids, get_recent_lkp
from app.api.users.router import _get_tak_server
from app.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tak", tags=["tak-proxy"])


def _client():
    """Resolve the TAK Server mTLS client or 503 if unavailable."""
    c = _get_tak_server()
    if c is None or c._client is None:
        raise HTTPException(503, "TAK Server client not configured")
    return c


def _hidden_prefixes() -> list[str]:
    """Lowercase prefixes from USERS_HIDDEN_PREFIXES, parsed per-call.

    Per-call rather than module-level so settings overrides in tests apply.
    """
    return [p.strip().lower() for p in settings.users_hidden_prefixes.split(",") if p.strip()]


def _is_service_account(entry: dict) -> bool:
    """True if the entry's actor identity matches a hidden prefix.

    /Marti/api/subscriptions/all carries the actor in ``username``.
    /Marti/api/contacts/all carries it in ``notes`` (often with a
    leading space, e.g. ``" svc_wx"``). Strip whitespace and lowercase
    before matching against USERS_HIDDEN_PREFIXES.

    Fail-open: if both fields are missing or empty (e.g. TAK Server 5.7+
    renames the field), return False so the entry is shown rather than
    silently dropped. Visible noise is debuggable; silent emptiness isn't.
    """
    raw = entry.get("username") or entry.get("notes") or ""
    actor = raw.strip().lower()
    if not actor:
        return False
    return any(actor.startswith(p) for p in _hidden_prefixes())


# --- Request-free helpers (used by dashboard partials and route handlers) ---


def _build_groups_response() -> list[dict]:
    return _client().list_groups()


def _build_clients_response(
    include_lkp: bool = False,
    include_service: bool = False,
) -> list[dict]:
    """Connected clients, optionally enriched with LKP from cot_router.

    By default, subscriptions belonging to service accounts (username
    matches USERS_HIDDEN_PREFIXES) are filtered out — these are typically
    integrations like svc_fasttakapi, svc_adsb, etc. that hold a TLS
    session but never broadcast as contacts. Pass ``include_service=True``
    to see the full subscription list.
    """
    clients = _client().list_clients()
    if not include_service:
        clients = [c for c in clients if not _is_service_account(c)]
    if include_lkp:
        uids = [c["uid"] for c in clients if c.get("uid")]
        positions = get_lkp_for_uids(uids)
        # In-place mutation is safe because list_clients() returns fresh dicts
        # on every call (no caching). Add a shallow copy here if anyone caches.
        for c in clients:
            c["lkp"] = positions.get(c.get("uid"))
    return clients


def _build_contacts_response(include_service: bool = False) -> list[dict]:
    contacts = _client().list_contacts()
    if not include_service:
        contacts = [c for c in contacts if not _is_service_account(c)]
    return contacts


def _build_recent_contacts_response(
    max_age: int | None = None,
    include_service: bool = False,
) -> list[dict]:
    """Recent CoT-track positions, sourced from cot_router with roster enrichment.

    UIDs come from cot_router (durable, survives TAK Server restarts), so
    entries that have aged off /contacts/all still appear. Roster fields
    (callsign/team/role/takv/notes/filterGroups) enrich when available;
    callsign/team/role can fall back to fields parsed from the row's
    cot_router.detail XML; otherwise they are None.

    Args:
        max_age: Window in seconds. Defaults to 86400 (24h).
        include_service: When False (default), drops UIDs whose roster
            entry's `notes` field matches USERS_HIDDEN_PREFIXES. UIDs not
            in the roster cannot be classified and are rendered (fail-open,
            matches `_is_service_account`'s existing contract).
    """
    window = max_age if max_age is not None else 86400
    positions = get_recent_lkp(window, settings.lkp_cot_type_prefixes_list)

    contacts_by_uid: dict[str, dict] = {}
    try:
        for c in _client().list_contacts():
            uid = c.get("uid")
            if uid:
                contacts_by_uid[uid] = c
    except HTTPException as exc:
        # TAK Server unreachable — degrade gracefully, render cot_router rows
        # with detail-XML enrichment only.
        log.info("recent_contacts: TAK Server unavailable for enrichment: %s", exc.detail)

    service_uids = {uid for uid, c in contacts_by_uid.items() if _is_service_account(c)}

    rows: list[dict] = []
    for p in positions:
        uid = p["uid"]
        if not include_service and uid in service_uids:
            continue
        contact = contacts_by_uid.get(uid)
        detail = p.get("detail") or {}
        callsign = (contact and contact.get("callsign")) or detail.get("callsign")
        team = (contact and contact.get("team")) or detail.get("team")
        role = (contact and contact.get("role")) or detail.get("role")
        rows.append(
            {
                "uid": uid,
                "callsign": callsign,
                "team": team,
                "role": role,
                "takv": (contact or {}).get("takv"),
                "filterGroups": (contact or {}).get("filterGroups"),
                "notes": (contact or {}).get("notes"),
                "lkp": {
                    "uid": uid,
                    "lat": p["lat"],
                    "lon": p["lon"],
                    "hae": p["hae"],
                    "servertime": p["servertime"],
                    "cot_type": p["cot_type"],
                },
            }
        )
    return rows


def _build_missions_response() -> list[dict]:
    return _client().list_missions()


# --- Routes ---


@router.get("/groups", summary="TAK Server group list")
def list_groups(agency: str | None = Query(default=None)):
    """List all certificate-level group assignments from the TAK Server.

    Proxies `GET /Marti/api/groups/all`. Returns every group known to the
    Marti API regardless of whether any client is currently assigned to it.

    Args:
        agency: Reserved for agency-scoping (issue #21). Currently a no-op;
            all groups are returned regardless of value.

    Returns:
        List of group dicts as returned by the Marti API.

    Raises:
        HTTPException(503): TAK Server client is not configured.
    """
    return _build_groups_response()


@router.get("/clients", summary="Connected TAK clients")
def list_clients(
    agency: str | None = Query(default=None),
    include: str | None = Query(
        default=None, description="Set to 'lkp' to include last known position"
    ),
    include_service: bool = Query(
        default=False,
        description=(
            "Include subscriptions whose username matches USERS_HIDDEN_PREFIXES "
            "(service accounts like svc_fasttakapi). Off by default."
        ),
    ),
):
    """List active TLS subscriptions from the TAK Server.

    Proxies `GET /Marti/api/subscriptions/all`. By default, subscriptions whose
    `username` matches `USERS_HIDDEN_PREFIXES` (service accounts such as
    `svc_fasttakapi`) are filtered out to keep the list focused on human clients.

    Args:
        agency: Reserved for agency-scoping (issue #21). Currently a no-op.
        include: Pass `lkp` to attach a `lkp` field to each entry containing
            the client's last known position drawn from cot_router. Omit to
            skip the position lookup entirely.
        include_service: When `True`, opts back in to service-account
            subscriptions hidden by `USERS_HIDDEN_PREFIXES`.

    Returns:
        List of subscription dicts as returned by Marti, with `uid` normalised
        from TAK Server's `clientUid`. Common fields include `callsign`,
        `username`, `team`, `role`, `takClient`, `takVersion`, `groups` (list of
        direction-aware group dicts), `ipAddress`, `protocol`, `subscriptionUid`,
        and `lastReportMilliseconds`. When `include=lkp`, each entry gains an
        `lkp` field (`null` if no CoT row exists for the UID).

    Raises:
        HTTPException(503): TAK Server client is not configured.
    """
    return _build_clients_response(
        include_lkp=(include == "lkp"),
        include_service=include_service,
    )


@router.get("/contacts", summary="TAK Server contact roster")
def list_contacts(
    agency: str | None = Query(default=None),
    include_service: bool = Query(
        default=False,
        description=(
            "Include contacts whose notes field matches USERS_HIDDEN_PREFIXES "
            "(service accounts). Off by default."
        ),
    ),
):
    """List all contacts from the TAK Server contact roster.

    Proxies `GET /Marti/api/contacts/all`. Returns every contact the TAK Server
    is aware of, which may include clients no longer connected. Service accounts
    are filtered by default using the `notes` field (TAK Server stores the actor
    identity there, often with a leading space).

    Args:
        agency: Reserved for agency-scoping (issue #21). Currently a no-op.
        include_service: When `True`, opts back in to contacts whose `notes`
            field matches `USERS_HIDDEN_PREFIXES`.

    Returns:
        List of contact dicts, each containing `uid`, `callsign`, `team`,
        `role`, `takv`, `notes`, and `filterGroups` (typically `null`).

    Raises:
        HTTPException(503): TAK Server client is not configured.
    """
    return _build_contacts_response(include_service=include_service)


@router.get("/missions", summary="TAK Server missions")
def list_missions(agency: str | None = Query(default=None)):
    """List all missions from the TAK Server.

    Proxies `GET /Marti/api/missions`. Returns the full mission list as
    provided by the Marti API with no server-side filtering applied.

    Args:
        agency: Reserved for agency-scoping (issue #21). Currently a no-op.

    Returns:
        List of mission dicts as returned by the Marti API.

    Raises:
        HTTPException(503): TAK Server client is not configured.
    """
    return _build_missions_response()


@router.get("/contacts/recent", summary="Recent contacts with LKP")
def recent_contacts(
    agency: str | None = Query(default=None),
    max_age: int | None = Query(
        default=None,
        description=(
            "Recency window in seconds for the cot_router lookup. Omit "
            "(default) for 24h. cot_router retains ~30 days, so larger "
            "windows are valid for historical lookups."
        ),
    ),
    include_service: bool = Query(
        default=False,
        description=(
            "Include contacts whose notes field matches USERS_HIDDEN_PREFIXES "
            "(service accounts). Off by default."
        ),
    ),
):
    """TAK Server contact roster joined with each contact's last known position.

    UIDs are sourced from the durable cot_router table, so entries persist
    across TAK Server restarts that wipe the in-memory contact roster.
    `/Marti/api/contacts/all` is consulted opportunistically to enrich each
    entry with roster fields; detail XML from cot_router fills in when a UID
    has aged off the roster.

    Args:
        agency: Reserved for agency-scoping (issue #21). Currently a no-op.
        max_age: Recency window in seconds for the cot_router lookup. Omitted
            defaults to 86400 (24h). Cot_router retains ~30 days, so larger
            windows are valid for historical lookups.
        include_service: When `True`, opts back in to contacts whose `notes`
            field matches `USERS_HIDDEN_PREFIXES`.

    Returns:
        List of dicts shaped like {`uid`, `callsign`, `team`, `role`, `takv`,
        `notes`, `filterGroups`, `lkp`}. UIDs come from cot_router; roster
        fields can be `None` when the contact has aged off /Marti/api/contacts/all.

    Raises:
        HTTPException(503): TAK Server client is not configured.
    """
    return _build_recent_contacts_response(max_age=max_age, include_service=include_service)

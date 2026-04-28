"""TAK Server proxy router — exposes Marti API as HTTP-only endpoints.

Each /api/tak/* endpoint accepts an optional ``?agency=<uuid>`` query
parameter that is currently a no-op. It is reserved for the agency-scoping
work in #21 and ignored until that lands.

Request-free helpers (_build_*_response) are exposed for dashboard partials
to consume without needing a FastAPI Request object. Route handlers call
the helpers and add request-scoped concerns (eventually agency filtering).
"""

from fastapi import APIRouter, HTTPException, Query

from app.api.tak.positions import get_lkp_for_uids, get_recent_contacts_with_lkp
from app.api.users.router import _get_tak_server
from app.config import settings

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
    contacts = _client().list_contacts()
    if not include_service:
        contacts = [c for c in contacts if not _is_service_account(c)]
    uids = [c["uid"] for c in contacts if c.get("uid")]
    positions = get_recent_contacts_with_lkp(uids, max_age_seconds=max_age)
    by_uid = {p["uid"]: p for p in positions}
    for c in contacts:
        c["lkp"] = by_uid.get(c.get("uid"))
    return contacts


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
            "Seconds to look back. Omit (default) to rely on TAK Server's own "
            "contact retention — no FastTAK-side filter is applied."
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

    Combines `GET /Marti/api/contacts/all` with a cot_router lookup so every
    contact entry carries an `lkp` field. The `max_age` parameter controls
    how the position lookup is bounded:

    - **Omitted** — no FastTAK-side time filter; whatever is in `/contacts/all`
      is returned, with `lkp` populated from the most recent CoT row regardless
      of age.
    - **Set** — only CoT rows within the last `max_age` seconds are considered;
      contacts with no recent row still appear but with `lkp: null`.

    Args:
        agency: Reserved for agency-scoping (issue #21). Currently a no-op.
        max_age: Recency window in seconds for the cot_router position lookup.
        include_service: When `True`, opts back in to contacts whose `notes`
            field matches `USERS_HIDDEN_PREFIXES`.

    Returns:
        List of contact dicts containing `uid`, `callsign`, `team`, `role`,
        `takv`, `notes`, `filterGroups`, and `lkp` (a position dict with
        `uid`/`lat`/`lon`/`hae`/`servertime`/`cot_type`, or `null`).

    Raises:
        HTTPException(503): TAK Server client is not configured.
    """
    return _build_recent_contacts_response(max_age=max_age, include_service=include_service)

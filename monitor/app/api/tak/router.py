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
    """True if the subscription's TAK Server username matches a hidden prefix."""
    username = (entry.get("username") or "").lower()
    if not username:
        return False
    return any(username.startswith(p) for p in _hidden_prefixes())


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
        for c in clients:
            c["lkp"] = positions.get(c.get("uid"))
    return clients


def _build_contacts_response() -> list[dict]:
    return _client().list_contacts()


def _build_recent_contacts_response(max_age: int | None = None) -> list[dict]:
    contacts = _client().list_contacts()
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
    """Proxy /Marti/api/groups/all (cert-level group assignments)."""
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
    """Proxy /Marti/api/subscriptions/all.

    Pass ``?include=lkp`` to enrich each entry with a ``lkp`` field
    derived from cot_router. Pass ``?include_service=true`` to see
    service-account sessions that are hidden by default.
    """
    return _build_clients_response(
        include_lkp=(include == "lkp"),
        include_service=include_service,
    )


@router.get("/contacts", summary="TAK Server contact roster")
def list_contacts(agency: str | None = Query(default=None)):
    """Proxy /Marti/api/contacts/all."""
    return _build_contacts_response()


@router.get("/missions", summary="TAK Server missions")
def list_missions(agency: str | None = Query(default=None)):
    """Proxy /Marti/api/missions."""
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
):
    """Contacts from Marti's roster joined with their last known position.

    Time bound:
      * max_age unset → no FastTAK-side filter; whatever is in /contacts/all is shown.
      * max_age set   → only contacts whose latest cot_router row is within that window get an LKP.
    """
    return _build_recent_contacts_response(max_age=max_age)

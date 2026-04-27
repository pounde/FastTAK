"""TAK Server proxy router — exposes Marti API as HTTP-only endpoints.

Each /api/tak/* endpoint accepts an optional ``?agency=<uuid>`` query
parameter that is currently a no-op. It is reserved for the agency-scoping
work in #21 and ignored until that lands.

Request-free helpers (_build_*_response) are exposed for dashboard partials
to consume without needing a FastAPI Request object. Route handlers call
the helpers and add request-scoped concerns (eventually agency filtering).
"""

from fastapi import APIRouter, HTTPException, Query

from app.api.tak.positions import get_lkp_for_uids
from app.api.users.router import _get_tak_server

router = APIRouter(prefix="/api/tak", tags=["tak-proxy"])


def _client():
    """Resolve the TAK Server mTLS client or 503 if unavailable."""
    c = _get_tak_server()
    if c is None or c._client is None:
        raise HTTPException(503, "TAK Server client not configured")
    return c


# --- Request-free helpers (used by dashboard partials and route handlers) ---


def _build_groups_response() -> list[dict]:
    return _client().list_groups()


def _build_clients_response(include_lkp: bool = False) -> list[dict]:
    """Connected clients, optionally enriched with LKP from cot_router."""
    clients = _client().list_clients()
    if include_lkp:
        uids = [c["uid"] for c in clients if c.get("uid")]
        positions = get_lkp_for_uids(uids)
        for c in clients:
            c["lkp"] = positions.get(c.get("uid"))
    return clients


def _build_contacts_response() -> list[dict]:
    return _client().list_contacts()


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
):
    """Proxy /Marti/api/subscriptions/all.

    Pass ``?include=lkp`` to enrich each entry with a ``lkp`` field
    derived from cot_router.
    """
    return _build_clients_response(include_lkp=(include == "lkp"))


@router.get("/contacts", summary="TAK Server contact roster")
def list_contacts(agency: str | None = Query(default=None)):
    """Proxy /Marti/api/contacts/all."""
    return _build_contacts_response()

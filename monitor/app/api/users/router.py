"""User management API routes — /api/users and /api/groups."""

import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.api.users.authentik import AuthentikClient
from app.api.users.enrollment import build_enrollment_url
from app.api.users.tak_server import TakServerClient
from app.config import settings

router = APIRouter(tags=["users"])

_VALID_USERNAME = re.compile(r"^[a-zA-Z0-9._-]+$")

# ── Client singletons ────────────────────────────────────────────

_authentik: AuthentikClient | None = None
_tak_server: TakServerClient | None = None


def _get_authentik() -> AuthentikClient:
    global _authentik
    if _authentik is None:
        if not settings.authentik_api_token:
            raise HTTPException(503, "Authentik not configured")
        _authentik = AuthentikClient(
            base_url=settings.authentik_url,
            token=settings.authentik_api_token,
            hidden_prefixes=settings.users_hidden_prefixes.split(","),
        )
    return _authentik


def _get_tak_server() -> TakServerClient | None:
    global _tak_server
    if _tak_server is None and settings.tak_api_cert_path:
        _tak_server = TakServerClient(
            base_url=settings.tak_server_url,
            cert_path=settings.tak_api_cert_path,
            cert_password=settings.tak_api_cert_password,
        )
    return _tak_server


# ── Request models ────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str
    name: str
    ttl_hours: int | None = None
    groups: list[str] | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        if not _VALID_USERNAME.match(v):
            raise ValueError(
                "Username must contain only letters, numbers, dots, hyphens, underscores"
            )
        if len(v) > 64:
            raise ValueError("Username must be 64 characters or fewer")
        return v


class UpdateUserRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    ttl_hours: int | None = None  # null in JSON = clear TTL; omit field = leave unchanged


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=1)


class CreateGroupRequest(BaseModel):
    name: str


class SetGroupsRequest(BaseModel):
    groups: list[str]


class RevokeCertRequest(BaseModel):
    cert_id: int


# ── User endpoints ────────────────────────────────────────────────


@router.get("/api/users")
def list_users(
    search: str | None = Query(default=None),
    include: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    ak = _get_authentik()
    users = ak.list_users(search=search)

    total = len(users)
    start = (page - 1) * page_size
    page_users = users[start : start + page_size]

    if include and "certs" in include:
        tak = _get_tak_server()
        if tak:
            for u in page_users:
                u["certs"] = tak.list_user_certs(u["username"])

    return {"results": page_users, "count": total, "page": page, "page_size": page_size}


@router.post("/api/users", status_code=201)
def create_user(body: CreateUserRequest):
    ak = _get_authentik()
    return ak.create_user(
        username=body.username,
        name=body.name,
        ttl_hours=body.ttl_hours,
        groups=body.groups,
    )


@router.get("/api/users/{user_id}")
def get_user(user_id: int):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    tak = _get_tak_server()
    if tak:
        user["certs"] = tak.list_user_certs(user["username"])
    return user


@router.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UpdateUserRequest):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    kwargs = {}
    # is_active and name use `is not None` because null has no meaningful
    # semantic for them — there's no "clear name" or "clear active state".
    # ttl_hours uses model_fields_set so that null (JSON null) means "clear the
    # TTL" while omitting the field entirely means "leave unchanged".
    if body.name is not None:
        kwargs["name"] = body.name
    if body.is_active is not None:
        kwargs["is_active"] = body.is_active
    if "ttl_hours" in body.model_fields_set:
        kwargs["ttl_hours"] = body.ttl_hours
    return ak.update_user(user_id, **kwargs)


@router.delete("/api/users/{user_id}")
def delete_user(user_id: int):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    ak.deactivate_user(user_id)

    tak = _get_tak_server()
    if tak:
        all_revoked = tak.revoke_all_user_certs(user["username"])
        if all_revoked:
            ak.mark_certs_revoked(user_id)

    return {"success": True, "username": user["username"]}


@router.post("/api/users/{user_id}/password")
def set_password(user_id: int, body: SetPasswordRequest):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if not user["is_active"]:
        raise HTTPException(400, "Cannot set password for deactivated user")
    ak.set_password(user_id, body.password)
    return {"success": True}


@router.post("/api/users/{user_id}/enroll")
def enroll_user(user_id: int):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if not user["is_active"]:
        raise HTTPException(400, "Cannot enroll deactivated user")
    token, expires_at = ak.get_or_create_enrollment_token(user_id, settings.enrollment_ttl_minutes)
    url = build_enrollment_url(token=token, fqdn=settings.fqdn, username=user["username"])
    return {"enrollment_url": url, "expires_at": expires_at}


# ── Cert endpoints ────────────────────────────────────────────────


@router.get("/api/users/{user_id}/certs")
def list_user_certs(user_id: int):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    tak = _get_tak_server()
    if not tak:
        raise HTTPException(503, "TAK Server not configured")
    return tak.list_user_certs(user["username"])


@router.post("/api/users/{user_id}/certs/revoke")
def revoke_user_cert(user_id: int, body: RevokeCertRequest):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    tak = _get_tak_server()
    if not tak:
        raise HTTPException(503, "TAK Server not configured")
    if not tak.revoke_cert(body.cert_id):
        raise HTTPException(500, "Failed to revoke certificate")
    return {"success": True}


# ── Group endpoints ───────────────────────────────────────────────


@router.get("/api/groups")
def list_groups():
    return _get_authentik().list_groups()


@router.post("/api/groups", status_code=201)
def create_group(body: CreateGroupRequest):
    return _get_authentik().create_group(body.name)


@router.get("/api/groups/{group_id}")
def get_group(group_id: str):
    group = _get_authentik().get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    return group


@router.delete("/api/groups/{group_id}")
def delete_group(group_id: str):
    ak = _get_authentik()
    group = ak.get_group(group_id)
    if not group:
        raise HTTPException(404, "Group not found")
    ak.delete_group(group_id)
    return {"success": True}


@router.put("/api/users/{user_id}/groups")
def set_user_groups(user_id: int, body: SetGroupsRequest):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    ak.set_user_groups(user_id, body.groups)
    return {"success": True}

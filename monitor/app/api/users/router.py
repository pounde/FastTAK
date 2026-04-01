"""User management API routes — /api/users and /api/groups."""

import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.api.users.authentik import AuthentikClient
from app.api.users.enrollment import build_enrollment_url
from app.api.users.tak_server import TakServerClient
from app.config import settings

router = APIRouter(tags=["users"])

_VALID_USERNAME = re.compile(r"^[a-zA-Z0-9._-]+$")

CERT_FILES_PATH = Path("/opt/tak/certs/files")

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
    cert_id: int | None = None
    cert_name: str | None = None


class GenerateCertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not re.match(r"^[a-zA-Z0-9._-]+$", v):
            raise ValueError("Name must contain only letters, numbers, dots, hyphens, underscores")
        return v


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


def _get_revoked_serials() -> set[str]:
    """Get set of revoked cert serial numbers from the CRL."""
    from app.api.service_accounts.cert_gen import get_revoked_serials

    return get_revoked_serials()


def _get_cert_serial(pem_path: Path) -> str | None:
    """Get the serial number from a PEM cert file."""
    import subprocess

    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout", "-serial"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output: "serial=AABBCCDD"
            return result.stdout.strip().split("=", 1)[1].lower()
    except Exception:
        pass
    return None


def _get_cert_expiry(pem_path: Path) -> str | None:
    """Get the expiration date from a PEM cert file as ISO string."""
    import subprocess

    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout", "-enddate"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output: "notAfter=Mar 31 12:00:00 2027 GMT"
            from datetime import UTC, datetime

            date_str = result.stdout.strip().split("=", 1)[1]
            expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
            return expiry.replace(tzinfo=UTC).isoformat()
    except Exception:
        pass
    return None


def _compute_cert_hash(pem_path: Path) -> str | None:
    """Compute SHA-256 fingerprint of a PEM cert file to match against TAK Server."""
    import subprocess

    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(pem_path), "-noout", "-fingerprint", "-sha256"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output: "sha256 Fingerprint=AA:BB:CC:..."
            return result.stdout.strip().split("=", 1)[1].replace(":", "").lower()
    except Exception:
        pass
    return None


@router.get("/api/users/{user_id}/certs")
def list_user_certs(user_id: int):
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    username = user["username"]

    # Get TAK Server certs (includes QR-enrolled and registered certs)
    tak_certs = []
    tak = _get_tak_server()
    if tak:
        tak_certs = tak.list_user_certs(username)

    # Build hash → TAK Server cert lookup for matching
    # Normalize: strip colons, lowercase (certadmin uses "AA:BB:CC", we compute "aabbcc")
    def _normalize_hash(h: str) -> str:
        return h.replace(":", "").lower()

    tak_by_hash = {_normalize_hash(c["hash"]): c for c in tak_certs if c.get("hash")}

    # Get revoked serials from CRL for checking on-disk certs
    revoked_serials = _get_revoked_serials()

    # Scan on-disk certs and merge with TAK Server data
    unified = []
    matched_hashes = set()

    prefix = f"{username}-"
    for p12 in sorted(CERT_FILES_PATH.glob(f"{prefix}*.p12")):
        cert_name = p12.stem[len(prefix) :]
        pem_path = CERT_FILES_PATH / f"{p12.stem}.pem"

        # Check CRL for revocation (covers API-generated certs not in certadmin)
        is_revoked = False
        if pem_path.exists():
            serial = _get_cert_serial(pem_path)
            if serial and serial in revoked_serials:
                is_revoked = True

        # Read expiration from the PEM file
        expiry = _get_cert_expiry(pem_path) if pem_path.exists() else None

        entry = {
            "name": cert_name,
            "downloadable": not is_revoked,
            "revoked": is_revoked,
            "cert_id": None,
            "expiration_date": expiry,
        }

        # Try to match with TAK Server cert by hash
        if pem_path.exists():
            file_hash = _compute_cert_hash(pem_path)
            if file_hash and file_hash in tak_by_hash:
                tak_cert = tak_by_hash[file_hash]
                entry["cert_id"] = tak_cert["id"]
                entry["revoked"] = tak_cert.get("revocation_date") is not None
                entry["expiration_date"] = tak_cert.get("expiration_date")
                matched_hashes.add(file_hash)

        unified.append(entry)

    # Add TAK Server certs that don't have on-disk files (e.g., QR-enrolled)
    for tak_cert in tak_certs:
        cert_hash = _normalize_hash(tak_cert.get("hash") or "")
        if cert_hash and cert_hash not in matched_hashes:
            unified.append(
                {
                    "name": None,
                    "downloadable": False,
                    "revoked": tak_cert.get("revocation_date") is not None,
                    "cert_id": tak_cert["id"],
                    "expiration_date": tak_cert.get("expiration_date"),
                }
            )

    return unified


@router.post("/api/users/{user_id}/certs/generate", status_code=201)
def generate_user_cert(user_id: int, body: GenerateCertRequest):
    from app.api.service_accounts.cert_gen import generate_client_cert

    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if not user["is_active"]:
        raise HTTPException(400, "Cannot generate cert for deactivated user")

    username = user["username"]
    cert_filename = f"{username}-{body.name}"

    # Check if a cert with this name already exists
    if (CERT_FILES_PATH / f"{cert_filename}.p12").exists():
        raise HTTPException(409, f"Certificate '{body.name}' already exists for this user")

    # Determine validity — don't exceed user's expiry if set
    validity_days = 365
    fastak_expires = user.get("fastak_expires")
    if fastak_expires is not None:
        remaining_seconds = fastak_expires - time.time()
        if remaining_seconds <= 0:
            raise HTTPException(400, "User has already expired")
        remaining_days = int(remaining_seconds / 86400)
        validity_days = min(validity_days, max(remaining_days, 1))

    # Generate cert with CN=username (not cert_filename) so all certs
    # resolve to the same LDAP identity
    result = generate_client_cert(cert_filename, validity_days=validity_days, cn_override=username)
    if not result["success"]:
        raise HTTPException(500, f"Cert generation failed: {result.get('error')}")

    return {
        "name": body.name,
        "filename": f"{cert_filename}.p12",
        "validity_days": validity_days,
        "download_url": f"/api/users/{user_id}/certs/download/{body.name}",
    }


@router.post("/api/users/{user_id}/certs/revoke")
def revoke_user_cert(user_id: int, body: RevokeCertRequest):
    """Revoke a certificate and update the CRL so TAK Server rejects it.

    Provide exactly one of cert_id or cert_name:
    - cert_id: for QR-enrolled certs (fetches PEM from certadmin, revokes via CRL)
    - cert_name: for API-generated certs (revokes via CRL using on-disk .pem)

    Both paths update the CRL (actual TLS revocation) AND mark the cert as
    revoked in certadmin if applicable.
    """
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    if body.cert_id is not None:
        from app.api.service_accounts.cert_gen import revoke_cert_by_pem

        tak = _get_tak_server()
        if not tak:
            raise HTTPException(503, "TAK Server not configured")

        # Get the cert PEM from certadmin to feed to CRL revocation
        certs = tak.list_user_certs(user["username"])
        cert_data = next((c for c in certs if c["id"] == body.cert_id), None)
        if not cert_data:
            raise HTTPException(404, "Certificate not found")
        if not cert_data.get("certificate_pem"):
            raise HTTPException(500, "Cannot revoke: cert PEM not available from TAK Server")

        # CRL revocation (actual TLS disconnect)
        result = revoke_cert_by_pem(cert_data["certificate_pem"])
        if not result["success"]:
            raise HTTPException(500, f"CRL revocation failed: {result.get('error', '')}")

        # Also mark revoked in certadmin database
        tak.revoke_cert(body.cert_id)

        # Delete enrollment tokens to prevent re-enrollment with cached credentials
        ak.delete_enrollment_tokens(user_id)

        return {"success": True}

    if body.cert_name is not None:
        from app.api.service_accounts.cert_gen import revoke_cert_by_name

        cert_filename = f"{user['username']}-{body.cert_name}"
        result = revoke_cert_by_name(cert_filename)
        if not result["success"]:
            raise HTTPException(500, f"Revocation failed: {result.get('error', '')}")

        # Delete enrollment tokens to prevent re-enrollment with cached credentials
        ak.delete_enrollment_tokens(user_id)

        return {"success": True}

    raise HTTPException(400, "Provide either cert_id or cert_name")


@router.get("/api/users/{user_id}/certs/download/{cert_name}")
def download_user_cert(user_id: int, cert_name: str):
    if not re.match(r"^[a-zA-Z0-9._-]+$", cert_name):
        raise HTTPException(400, "Invalid certificate name")
    ak = _get_authentik()
    user = ak.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")

    username = user["username"]
    filename = f"{username}-{cert_name}.p12"
    p12_path = CERT_FILES_PATH / filename
    if not p12_path.exists():
        raise HTTPException(404, f"Certificate '{cert_name}' not found")

    # Block download of revoked certs
    pem_path = CERT_FILES_PATH / f"{username}-{cert_name}.pem"
    if pem_path.exists():
        serial = _get_cert_serial(pem_path)
        if serial and serial in _get_revoked_serials():
            raise HTTPException(403, "Certificate has been revoked")

    return FileResponse(
        path=p12_path,
        media_type="application/x-pkcs12",
        filename=filename,
    )


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

"""Service account management API routes — /api/service-accounts."""

import logging
import re
from enum import Enum
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.service_accounts.cert_gen import generate_client_cert, register_admin_cert
from app.api.users.authentik import AuthentikClient
from app.api.users.tak_server import TakServerClient
from app.config import settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["service-accounts"])

CERT_FILES_PATH = Path("/opt/tak/certs/files")

_VALID_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")

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
            hidden_prefixes=[],
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


# ── Request / response models ────────────────────────────────────


class ServiceAccountMode(str, Enum):
    data = "data"
    admin = "admin"


class CreateServiceAccountRequest(BaseModel):
    name: str
    display_name: str
    mode: ServiceAccountMode
    groups: list[str] | None = None
    validity_days: int | None = Field(default=None, gt=0, le=3650)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        # Strip svc_ prefix for validation, it gets added back later
        raw = v[4:] if v.startswith("svc_") else v
        if not _VALID_NAME.match(raw):
            raise ValueError("Name must contain only letters, numbers, dots, hyphens, underscores")
        if len(v) > 64:
            raise ValueError("Name must be 64 characters or fewer")
        return v

    @model_validator(mode="after")
    def validate_groups_for_mode(self):
        if self.mode == ServiceAccountMode.data:
            if not self.groups:
                raise ValueError("Groups are required for data mode service accounts")
        elif self.mode == ServiceAccountMode.admin:
            if self.groups:
                raise ValueError("Groups are forbidden for admin mode service accounts")
        return self


class UpdateServiceAccountRequest(BaseModel):
    display_name: str | None = None
    groups: list[str] | None = None


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/api/service-accounts")
def list_service_accounts():
    ak = _get_authentik()
    accounts = ak.list_users(search="svc_")
    return {"results": accounts}


@router.post("/api/service-accounts", status_code=201)
def create_service_account(body: CreateServiceAccountRequest):
    ak = _get_authentik()

    # Auto-prepend svc_ if not already present
    username = body.name if body.name.startswith("svc_") else f"svc_{body.name}"

    # Determine validity
    if body.validity_days is not None:
        validity_days = body.validity_days
    else:
        validity_days = 365 if body.mode == ServiceAccountMode.data else 730

    # Validate groups exist before creating anything
    if body.groups and body.mode == ServiceAccountMode.data:
        existing_groups = ak.list_groups()
        existing_names = {g["name"] for g in existing_groups}
        missing = [g for g in body.groups if g not in existing_names]
        if missing:
            msg = f"Groups do not exist: {', '.join(missing)}. Create them first."
            raise HTTPException(400, msg)

    # Create Authentik user
    user = ak.create_user(
        username=username,
        name=body.display_name,
        groups=body.groups if body.mode == ServiceAccountMode.data else None,
        user_type="service_account",
    )

    # Generate certificate
    cert_result = generate_client_cert(username, validity_days)
    if not cert_result.get("success"):
        # Rollback: deactivate the orphaned Authentik user
        log.warning(
            "Cert generation failed for %s, rolling back Authentik user %s: %s",
            username,
            user["id"],
            cert_result.get("error"),
        )
        try:
            ak.deactivate_user(user["id"])
        except Exception:
            log.exception("Failed to rollback Authentik user %s", user["id"])
        raise HTTPException(
            500,
            f"Certificate generation failed: {cert_result.get('error', 'unknown error')}",
        )

    # Register as admin if admin mode
    if body.mode == ServiceAccountMode.admin:
        admin_result = register_admin_cert(username)
        if not admin_result.get("success"):
            log.warning(
                "Admin cert registration failed for %s: %s",
                username,
                admin_result.get("error") or admin_result.get("output"),
            )
            try:
                ak.deactivate_user(user["id"])
            except Exception:
                log.exception("Failed to rollback Authentik user %s", user["id"])
            detail = admin_result.get("error") or admin_result.get("output", "unknown error")
            raise HTTPException(500, f"Admin cert registration failed: {detail}")

    return {
        **user,
        "mode": body.mode.value,
        "validity_days": validity_days,
        "cert_download_url": f"/api/service-accounts/{user['id']}/certs/download",
    }


@router.patch("/api/service-accounts/{account_id}")
def update_service_account(account_id: int, body: UpdateServiceAccountRequest):
    ak = _get_authentik()
    user = ak.get_user(account_id)
    if not user:
        raise HTTPException(404, "Service account not found")
    if not user["username"].startswith("svc_"):
        raise HTTPException(404, "Service account not found")

    # Validate groups before making any changes (atomic validation)
    if body.groups is not None:
        existing_groups = ak.list_groups()
        existing_names = {g["name"] for g in existing_groups}
        missing = [g for g in body.groups if g not in existing_names]
        if missing:
            msg = f"Groups do not exist: {', '.join(missing)}. Create them first."
            raise HTTPException(400, msg)

    if body.display_name is not None:
        ak.update_user(account_id, name=body.display_name)

    if body.groups is not None:
        ak.set_user_groups(account_id, body.groups)

    return {"success": True}


@router.get("/api/service-accounts/{account_id}")
def get_service_account(account_id: int):
    ak = _get_authentik()
    user = ak.get_user(account_id)
    if not user:
        raise HTTPException(404, "Service account not found")
    if not user["username"].startswith("svc_"):
        raise HTTPException(404, "Service account not found")
    tak = _get_tak_server()
    if tak:
        user["certs"] = tak.list_user_certs(user["username"])
    return user


@router.delete("/api/service-accounts/{account_id}")
def delete_service_account(account_id: int):
    ak = _get_authentik()
    user = ak.get_user(account_id)
    if not user:
        raise HTTPException(404, "Service account not found")
    if not user["username"].startswith("svc_"):
        raise HTTPException(404, "Service account not found")

    ak.deactivate_user(account_id)

    tak = _get_tak_server()
    if tak:
        all_revoked = tak.revoke_all_user_certs(user["username"])
        if all_revoked:
            ak.mark_certs_revoked(account_id)

    return {"success": True, "username": user["username"]}


@router.get("/api/service-accounts/{account_id}/certs/download")
def download_cert(account_id: int):
    ak = _get_authentik()
    user = ak.get_user(account_id)
    if not user:
        raise HTTPException(404, "Service account not found")
    if not user["username"].startswith("svc_"):
        raise HTTPException(404, "Service account not found")

    username = user["username"]
    p12_path = CERT_FILES_PATH / f"{username}.p12"
    if not p12_path.exists():
        raise HTTPException(404, "Certificate file not found")

    # Block download of revoked certs
    pem_path = CERT_FILES_PATH / f"{username}.pem"
    if pem_path.exists():
        import subprocess

        try:
            result = subprocess.run(
                ["openssl", "x509", "-in", str(pem_path), "-noout", "-serial"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                serial = result.stdout.strip().split("=", 1)[1].lower()
                crl_path = CERT_FILES_PATH / "ca.crl"
                if crl_path.exists():
                    crl_result = subprocess.run(
                        ["openssl", "crl", "-in", str(crl_path), "-text", "-noout"],
                        capture_output=True, text=True, timeout=5,
                    )
                    revoked = {
                        line.strip().split(": ")[1].strip().lower()
                        for line in crl_result.stdout.splitlines()
                        if line.strip().startswith("Serial Number:")
                    }
                    if serial in revoked:
                        raise HTTPException(403, "Certificate has been revoked")
        except HTTPException:
            raise
        except Exception:
            pass  # If we can't check, allow download

    return FileResponse(
        path=str(p12_path),
        media_type="application/x-pkcs12",
        filename=f"{username}.p12",
    )

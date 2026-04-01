"""Certificate generation and revocation utilities.

Uses openssl directly (not makeCert.sh) for configurable validity.
All operations run inside the tak-server container where the CA key resides.
Shared CRL utilities are imported by both the users and service accounts routers.
"""

import re
import subprocess
from pathlib import Path

from app.docker_client import find_container

CERT_DIR = "/opt/tak/certs"
CERT_FILES = "/opt/tak/certs/files"
CERT_FILES_PATH = Path("/opt/tak/certs/files")
_VALID_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")

_DEFAULTS = {"state": "XX", "city": "Default", "org_unit": "FastTAK"}


def get_revoked_serials() -> set[str]:
    """Get set of revoked cert serial numbers from the CRL.

    Shared by both user and service account routers to check revocation
    status on cert download and listing.
    """
    crl_path = CERT_FILES_PATH / "ca.crl"
    if not crl_path.exists():
        return set()
    try:
        result = subprocess.run(
            ["openssl", "crl", "-in", str(crl_path), "-text", "-noout"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        serials = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Serial Number:"):
                serial = line.split(":", 1)[1].strip().lower()
                serials.add(serial)
        return serials
    except Exception:
        return set()


def _validate_name(name: str) -> str | None:
    if not name or not _VALID_NAME.match(name):
        return "Name must contain only letters, numbers, dots, hyphens, underscores"
    if len(name) > 64:
        return "Name must be 64 characters or fewer"
    return None


def _get_tak_container():
    container = find_container("tak-server")
    if container is None:
        return None, {"success": False, "error": "tak-server container not found"}
    return container, None


def parse_ca_subject(subject_line: str) -> dict:
    """Extract STATE, CITY, OU from a CA certificate subject string."""
    result = dict(_DEFAULTS)
    for part in subject_line.split(","):
        part = part.strip()
        if part.startswith("ST=") or part.startswith("ST ="):
            result["state"] = part.split("=", 1)[1].strip()
        elif part.startswith("L=") or part.startswith("L ="):
            result["city"] = part.split("=", 1)[1].strip()
        elif part.startswith("OU=") or part.startswith("OU ="):
            result["org_unit"] = part.split("=", 1)[1].strip()
    return result


def _read_ca_subject(container) -> dict:
    """Read the CA cert subject to match new certs to the deployment's PKI naming."""
    exit_code, output = container.exec_run(
        ["openssl", "x509", "-in", f"{CERT_FILES}/ca.pem", "-noout", "-subject"],
    )
    if exit_code != 0:
        return dict(_DEFAULTS)
    return parse_ca_subject(output.decode(errors="replace"))


def generate_client_cert(
    name: str, validity_days: int = 365, cn_override: str | None = None
) -> dict:
    """Generate a client certificate with configurable validity.

    Steps: generate key+CSR, sign with CA, create .p12 bundle with modern ciphers.

    Args:
        name: filename base for the cert files (e.g., "svc_bot" or "userName-tablet")
        validity_days: cert validity in days
        cn_override: if set, use this as the CN instead of name. Used when
            generating multiple named certs for a user — all certs share the
            same CN (username) so they resolve to the same LDAP identity.
    """
    if err := _validate_name(name):
        return {"success": False, "error": err}
    if cn_override and (err := _validate_name(cn_override)):
        return {"success": False, "error": f"CN override: {err}"}

    cn = cn_override or name

    container, error = _get_tak_container()
    if error:
        return error

    try:
        subj = _read_ca_subject(container)

        # Generate key + CSR
        exit_code, output = container.exec_run(
            [
                "sh",
                "-c",
                "openssl req -new -newkey rsa:2048 -sha256"
                ' -keyout "${CERT_FILES}/${NAME}.key"'
                " -passout pass:atakatak"
                ' -out "${CERT_FILES}/${NAME}.csr"'
                ' -subj "/C=US/ST=${STATE}/L=${CITY}/O=TAK/OU=${OU}/CN=${CN}"',
            ],
            environment={
                "CERT_FILES": CERT_FILES,
                "NAME": name,
                "CN": cn,
                "STATE": subj["state"],
                "CITY": subj["city"],
                "OU": subj["org_unit"],
            },
        )
        if exit_code != 0:
            return {
                "success": False,
                "error": f"CSR generation failed: {output.decode(errors='replace')[:300]}",
            }

        # Sign with CA
        exit_code, output = container.exec_run(
            [
                "sh",
                "-c",
                "openssl x509 -sha256 -req -days ${DAYS}"
                ' -in "${CERT_FILES}/${NAME}.csr"'
                ' -CA "${CERT_FILES}/ca.pem"'
                ' -CAkey "${CERT_FILES}/ca-do-not-share.key"'
                ' -out "${CERT_FILES}/${NAME}.pem"'
                " -set_serial 0x$(openssl rand -hex 8)"
                " -passin pass:atakatak"
                " -extensions client"
                ' -extfile "${CERT_DIR}/config.cfg"',
            ],
            environment={
                "CERT_DIR": CERT_DIR,
                "CERT_FILES": CERT_FILES,
                "NAME": name,
                "DAYS": str(validity_days),
            },
        )
        if exit_code != 0:
            return {
                "success": False,
                "error": f"Signing failed: {output.decode(errors='replace')[:300]}",
            }

        # Create .p12 bundle with modern ciphers
        exit_code, output = container.exec_run(
            [
                "sh",
                "-c",
                "openssl pkcs12 -export"
                ' -in "${CERT_FILES}/${NAME}.pem"'
                ' -inkey "${CERT_FILES}/${NAME}.key"'
                ' -out "${CERT_FILES}/${NAME}.p12"'
                ' -name "${NAME}"'
                ' -CAfile "${CERT_FILES}/ca.pem"'
                " -passin pass:atakatak -passout pass:atakatak"
                " -certpbe AES-256-CBC -keypbe AES-256-CBC -macalg SHA256",
            ],
            environment={"CERT_FILES": CERT_FILES, "NAME": name},
        )
        if exit_code != 0:
            return {
                "success": False,
                "error": f"P12 creation failed: {output.decode(errors='replace')[:300]}",
            }

        # Register cert with TAK Server so certadmin knows about it
        # (enables listing and revocation by cert ID)
        reg_result = register_cert(name, container=container)
        if not reg_result["success"]:
            return {
                "success": False,
                "error": f"Cert created but registration failed: {reg_result.get('error', '')}",
            }

        return {"success": True}

    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def register_cert(name: str, container=None) -> dict:
    """Register a cert with TAK Server via certmod (no admin flag).

    This makes TAK Server's certadmin API aware of the cert, enabling
    listing and revocation by cert ID. Without registration, the cert
    is trusted (signed by the CA) but invisible to certadmin queries.
    """
    if container is None:
        container, error = _get_tak_container()
        if error:
            return error

    try:
        exit_code, output = container.exec_run(
            [
                "java",
                "-jar",
                "/opt/tak/utils/UserManager.jar",
                "certmod",
                f"{CERT_FILES}/{name}.pem",
            ],
        )
        return {
            "success": exit_code == 0,
            "output": output.decode(errors="replace")[:500],
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def revoke_cert_by_name(name: str) -> dict:
    """Revoke a cert by name via CRL update.

    The cert's .pem file must exist at {CERT_FILES}/{name}.pem.
    Revocation is immediate — TAK Server rejects revoked certs on next
    connection attempt.
    """
    if err := _validate_name(name):
        return {"success": False, "error": err}
    return _revoke_via_crl(name)


def revoke_cert_by_pem(pem_content: str) -> dict:
    """Revoke a cert by PEM content via CRL update.

    Writes the PEM to a temp file in the cert directory, runs openssl ca
    -revoke directly (not revokeCert.sh, to avoid env var requirements),
    updates the CRL, and cleans up.

    Used for QR-enrolled certs where no .pem file exists on disk — the PEM
    is extracted from TAK Server's certadmin API response.
    """
    container, error = _get_tak_container()
    if error:
        return error

    import secrets

    tmp_name = f"revoke-tmp-{secrets.token_hex(4)}"

    try:
        # Write PEM to temp file using Docker's put_archive API
        # This avoids all shell escaping issues with env vars and heredocs
        import io
        import tarfile

        pem_bytes = pem_content.encode("utf-8")
        pem_filename = f"{tmp_name}.pem"

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=pem_filename)
            info.size = len(pem_bytes)
            tar.addfile(info, io.BytesIO(pem_bytes))
        buf.seek(0)

        container.put_archive(CERT_FILES, buf)

        result = _revoke_via_crl(tmp_name, container=container)

        # Clean up temp file
        container.exec_run(["rm", "-f", f"{CERT_FILES}/{pem_filename}"])

        return result
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def _revoke_via_crl(name: str, container=None) -> dict:
    """Revoke a cert and update the CRL using openssl directly.

    All operations run in a single shell command from the cert files
    directory where crl_index.txt lives. This avoids issues with separate
    docker exec calls losing working directory state.
    """
    if container is None:
        container, error = _get_tak_container()
        if error:
            return error

    try:
        exit_code, output = container.exec_run(
            [
                "sh",
                "-c",
                # Single shell: cd, init index, strip blanks, revoke, update CRL
                "cd ${CERT_FILES}"
                " && touch crl_index.txt crl_index.txt.attr"
                " && (grep -q unique_subject crl_index.txt.attr"
                ' || echo "unique_subject = no" >> crl_index.txt.attr)'
                ' && sed -i "/^$/d" "${NAME}.pem"'
                " && openssl ca"
                ' -config "${CERT_DIR}/config.cfg"'
                ' -revoke "${NAME}.pem"'
                " -keyfile ca-do-not-share.key"
                " -key atakatak"
                " -cert ca.pem"
                " && openssl ca"
                ' -config "${CERT_DIR}/config.cfg"'
                " -gencrl"
                " -keyfile ca-do-not-share.key"
                " -key atakatak"
                " -cert ca.pem"
                " -out ca.crl",
            ],
            environment={
                "CERT_DIR": CERT_DIR,
                "CERT_FILES": CERT_FILES,
                "NAME": name,
            },
        )
        if exit_code != 0:
            return {
                "success": False,
                "error": output.decode(errors="replace")[:500],
            }
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def register_admin_cert(name: str) -> dict:
    """Register a cert as ROLE_ADMIN via certmod -A."""
    container, error = _get_tak_container()
    if error:
        return error

    try:
        exit_code, output = container.exec_run(
            [
                "java",
                "-jar",
                "/opt/tak/utils/UserManager.jar",
                "certmod",
                "-A",
                f"{CERT_FILES}/{name}.pem",
            ],
        )
        return {
            "success": exit_code == 0,
            "output": output.decode(errors="replace")[:500],
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}

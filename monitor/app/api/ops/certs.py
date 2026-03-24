"""Certificate operations via docker exec into tak-server.

Mirrors the commands in certs.sh. Cert rotation (CA regeneration) is not
supported here — it's a complex, destructive operation that should be done
manually via the host CLI.
"""

import re

from app.docker_client import find_container

CERT_DIR = "/opt/tak/certs"
CERT_FILES = "/opt/tak/certs/files"
_VALID_NAME = re.compile(r"^[a-zA-Z0-9._-]+$")


def _get_tak_server():
    container = find_container("tak-server")
    if container is None:
        return None, {"success": False, "error": "tak-server container not found"}
    return container, None


def _validate_name(name: str) -> str | None:
    """Return error message if name is invalid, None if ok."""
    if not name or not _VALID_NAME.match(name):
        return "Name must contain only letters, numbers, dots, hyphens, underscores"
    if len(name) > 64:
        return "Name must be 64 characters or fewer"
    return None


def _upgrade_p12(container, name: str) -> dict | None:
    """Re-export .p12 with modern ciphers (AES-256-CBC). Returns error dict or None.

    Uses positional arg passing ("--" "$1") to avoid shell injection —
    name is never interpolated into the shell command string.
    """
    exit_code, output = container.exec_run(
        ["sh", "-c", """
            openssl pkcs12 -in ${CERT_FILES}/"$1".p12 -passin pass:atakatak -nokeys -out /tmp/cert.pem -legacy 2>/dev/null && \
            openssl pkcs12 -in ${CERT_FILES}/"$1".p12 -passin pass:atakatak -nocerts -nodes -out /tmp/key.pem -legacy 2>/dev/null && \
            openssl pkcs12 -export -in /tmp/cert.pem -inkey /tmp/key.pem -out ${CERT_FILES}/"$1".p12 \
              -passout pass:atakatak -certpbe AES-256-CBC -keypbe AES-256-CBC -macalg SHA256 2>/dev/null && \
            rm -f /tmp/cert.pem /tmp/key.pem
        """, "--", name],
        environment={"CERT_FILES": CERT_FILES},
    )
    if exit_code != 0:
        return {"warning": "p12 upgrade failed", "output": output.decode(errors="replace")[:300]}
    return None


def create_client_cert(name: str) -> dict:
    """Create a client certificate + .p12 (mirrors: certs.sh create-client)."""
    if err := _validate_name(name):
        return {"success": False, "error": err}
    container, error = _get_tak_server()
    if error:
        return error
    try:
        exit_code, output = container.exec_run(
            ["bash", "-c", 'cd "$1" && ./makeCert.sh client "$2"', "--", CERT_DIR, name],
        )
        result = {
            "success": exit_code == 0,
            "output": output.decode(errors="replace")[:500],
        }
        if exit_code == 0:
            p12_warn = _upgrade_p12(container, name)
            if p12_warn:
                result["p12_upgrade"] = p12_warn
        return result
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def create_server_cert(name: str) -> dict:
    """Create a server certificate (mirrors: certs.sh create-server)."""
    if err := _validate_name(name):
        return {"success": False, "error": err}
    container, error = _get_tak_server()
    if error:
        return error
    try:
        exit_code, output = container.exec_run(
            ["bash", "-c", 'cd "$1" && ./makeCert.sh server "$2"', "--", CERT_DIR, name],
        )
        result = {
            "success": exit_code == 0,
            "output": output.decode(errors="replace")[:500],
        }
        if exit_code == 0:
            p12_warn = _upgrade_p12(container, name)
            if p12_warn:
                result["p12_upgrade"] = p12_warn
        return result
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def revoke_cert(name: str) -> dict:
    """Revoke a certificate (mirrors: certs.sh revoke)."""
    if err := _validate_name(name):
        return {"success": False, "error": err}
    container, error = _get_tak_server()
    if error:
        return error
    try:
        exit_code, output = container.exec_run(
            ["bash", "-c", 'cd "$1" && ./revokeCert.sh "$2"', "--", CERT_DIR, name],
        )
        return {
            "success": exit_code == 0,
            "output": output.decode(errors="replace")[:500],
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


def list_certs() -> dict:
    """List all cert files (mirrors: certs.sh list)."""
    container, error = _get_tak_server()
    if error:
        return error
    try:
        exit_code, output = container.exec_run(
            ["sh", "-c", 'ls -la "$1" | grep -E "\\.(pem|p12|jks|csr)$"', "--", CERT_FILES],
        )
        return {
            "success": True,
            "files": output.decode(errors="replace").strip(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}

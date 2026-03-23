#!/bin/sh
# certs.sh — Certificate management CLI for FastTAK
# Wraps docker exec calls to TAK Server's cert tools.
#
# Usage:
#   ./certs.sh list                          List all certs
#   ./certs.sh create-client <name>          Create client cert + .p12
#   ./certs.sh create-server <name>          Create server cert + .jks
#   ./certs.sh download <filename> [dest]    Download cert file from container
#   ./certs.sh revoke <name>                 Revoke a client cert
#   ./certs.sh create-user <name> <pass>     Create admin user (UserManager)
#   ./certs.sh ca-info                       Show CA cert details + expiry

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTAINER="$(docker compose -f "$SCRIPT_DIR/docker-compose.yml" ps -q tak-server 2>/dev/null)"
CERT_DIR="/opt/tak/certs"
CERT_FILES="/opt/tak/certs/files"

if [ -z "$CONTAINER" ]; then
    echo "ERROR: tak-server container not found. Is the stack running?" >&2
    exit 1
fi

# Validate name arguments — alphanumeric, dots, hyphens, underscores only
validate_name() {
    case "$1" in
        *[!a-zA-Z0-9._-]*)
            echo "ERROR: name must contain only letters, numbers, dots, hyphens, underscores" >&2
            exit 1
            ;;
        "")
            echo "ERROR: name cannot be empty" >&2
            exit 1
            ;;
    esac
}

# Re-export a .p12 with modern ciphers (AES-256-CBC)
upgrade_p12() {
    docker exec "$CONTAINER" sh -c "
        openssl pkcs12 -in ${CERT_FILES}/\$1.p12 -passin pass:atakatak -nokeys -out /tmp/cert.pem -legacy 2>/dev/null && \
        openssl pkcs12 -in ${CERT_FILES}/\$1.p12 -passin pass:atakatak -nocerts -nodes -out /tmp/key.pem -legacy 2>/dev/null && \
        openssl pkcs12 -export -in /tmp/cert.pem -inkey /tmp/key.pem -out ${CERT_FILES}/\$1.p12 \
          -passout pass:atakatak -certpbe AES-256-CBC -keypbe AES-256-CBC -macalg SHA256 2>/dev/null && \
        rm -f /tmp/cert.pem /tmp/key.pem
    " -- "$1"
}

cmd="${1:-help}"
shift 2>/dev/null || true

case "$cmd" in
    list)
        echo "=== Certificates in ${CERT_FILES} ==="
        docker exec "$CONTAINER" ls -la "$CERT_FILES" | grep -E '\.(pem|p12|jks|csr)$' || echo "(no cert files found)"
        ;;

    create-client)
        name="${1:?Usage: certs.sh create-client <name>}"
        validate_name "$name"
        echo "Creating client cert for '${name}'..."
        docker exec "$CONTAINER" bash -c "cd ${CERT_DIR} && ./makeCert.sh client \"\$1\"" -- "$name"
        upgrade_p12 "$name"
        echo ""
        echo "Files created:"
        docker exec "$CONTAINER" sh -c "ls -la ${CERT_FILES}/${name}*" 2>/dev/null || echo "  (none found — check output above for errors)"
        echo ""
        echo "Download the .p12 with:"
        echo "  ./certs.sh download ${name}.p12"
        ;;

    create-server)
        name="${1:?Usage: certs.sh create-server <name>}"
        validate_name "$name"
        echo "Creating server cert for '${name}'..."
        docker exec "$CONTAINER" bash -c "cd ${CERT_DIR} && ./makeCert.sh server \"\$1\"" -- "$name"
        upgrade_p12 "$name"
        ;;

    download)
        filename="${1:?Usage: certs.sh download <filename> [destination]}"
        validate_name "$filename"
        dest="${2:-./${filename}}"
        echo "Downloading ${filename} -> ${dest}"
        docker cp "${CONTAINER}:${CERT_FILES}/${filename}" "${dest}"
        echo "Saved to ${dest}"
        ;;

    revoke)
        name="${1:?Usage: certs.sh revoke <name>}"
        validate_name "$name"
        echo "Revoking cert for '${name}'..."
        docker exec "$CONTAINER" bash -c "cd ${CERT_DIR} && ./revokeCert.sh \"\$1\"" -- "$name"
        ;;

    create-user)
        name="${1:?Usage: certs.sh create-user <name> <password>}"
        pass="${2:?Usage: certs.sh create-user <name> <password>}"
        validate_name "$name"
        echo "Creating admin user '${name}'..."
        echo "Password must be 15+ characters with uppercase, lowercase, number, and special character."
        docker exec "$CONTAINER" java -jar /opt/tak/utils/UserManager.jar usermod -A -p "${pass}" "${name}"
        ;;

    ca-info)
        echo "=== Root CA ==="
        docker exec "$CONTAINER" openssl x509 -in "${CERT_FILES}/root-ca.pem" -noout -subject -issuer -enddate 2>/dev/null || echo "  root-ca.pem not found"
        echo ""
        echo "=== Intermediate CA ==="
        docker exec "$CONTAINER" openssl x509 -in "${CERT_FILES}/ca.pem" -noout -subject -issuer -enddate 2>/dev/null || echo "  ca.pem not found"
        ;;

    help|--help|-h|*)
        cat <<'USAGE'
FastTAK Certificate Management

Usage: ./certs.sh <command> [arguments]

Commands:
  list                          List all cert files
  create-client <name>          Create client cert (.pem, .p12, .jks)
  create-server <name>          Create server cert (.pem, .jks)
  download <file> [dest]        Copy cert file from container to host
  revoke <name>                 Revoke a certificate
  create-user <name> <pass>     Create TAK admin user (15+ chars, mixed case, number, special)
                                Note: password is visible in the process list during execution
  ca-info                       Show CA certificate details and expiry

Examples:
  ./certs.sh create-client alice
  ./certs.sh download alice.p12
  ./certs.sh download alice.p12 ~/Downloads/alice.p12
  ./certs.sh create-user webadmin 'My-Secure-Pass1!'
  ./certs.sh ca-info
USAGE
        ;;
esac

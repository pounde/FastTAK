#!/bin/sh
# reconfig.sh — Change SERVER_ADDRESS and DEPLOY_MODE, regenerate server certs.
#
# Client certs, CA, and service accounts are preserved. No re-enrollment needed.
# See docs/certificates.md "Changing Server Address" for details.
#
# Usage: ./reconfig.sh <server_address> <deploy_mode>
# Example: ./reconfig.sh tak.example.com subdomain
#          ./reconfig.sh 10.0.0.5 direct

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
CERT_DIR="$SCRIPT_DIR/tak/certs/files"

# ── Validate arguments ──────────────────────────────────────────────────

if [ $# -ne 2 ]; then
    echo "Usage: $0 <server_address> <deploy_mode>"
    echo ""
    echo "  server_address   IP, hostname, or FQDN (e.g. 10.0.0.5, tak.example.com)"
    echo "  deploy_mode      'direct' (port-based) or 'subdomain' (DNS-based)"
    exit 1
fi

NEW_ADDRESS="$1"
NEW_MODE="$2"

case "$NEW_MODE" in
    direct|subdomain) ;;
    *) echo "ERROR: deploy_mode must be 'direct' or 'subdomain', got '$NEW_MODE'" >&2; exit 1 ;;
esac

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env not found at $ENV_FILE" >&2
    exit 1
fi

# ── Read old values ─────────────────────────────────────────────────────

OLD_ADDRESS=$(grep '^SERVER_ADDRESS=' "$ENV_FILE" | cut -d= -f2)
OLD_MODE=$(grep '^DEPLOY_MODE=' "$ENV_FILE" | cut -d= -f2)

if [ "$OLD_ADDRESS" = "$NEW_ADDRESS" ] && [ "$OLD_MODE" = "$NEW_MODE" ]; then
    echo "No changes — already set to $NEW_ADDRESS ($NEW_MODE)"
    exit 0
fi

echo "Reconfiguring FastTAK:"
echo "  Address: ${OLD_ADDRESS:-unset} → $NEW_ADDRESS"
echo "  Mode:    ${OLD_MODE:-unset} → $NEW_MODE"
echo ""

# ── Update .env ─────────────────────────────────────────────────────────

sed -i.bak "s|^SERVER_ADDRESS=.*|SERVER_ADDRESS=$NEW_ADDRESS|" "$ENV_FILE"
sed -i.bak "s|^DEPLOY_MODE=.*|DEPLOY_MODE=$NEW_MODE|" "$ENV_FILE"
rm -f "$ENV_FILE.bak"

echo "Updated .env"

# ── Delete server certs ─────────────────────────────────────────────────

DELETED=0

for ext in jks pem p12; do
    f="$CERT_DIR/takserver.$ext"
    if [ -f "$f" ]; then
        rm "$f"
        DELETED=$((DELETED + 1))
    fi
done

if [ -n "$OLD_ADDRESS" ]; then
    for f in "$CERT_DIR/$OLD_ADDRESS".*; do
        if [ -f "$f" ]; then
            rm "$f"
            DELETED=$((DELETED + 1))
        fi
    done
fi

echo "Deleted $DELETED server cert files (CA and client certs preserved)"

# ── Restart stack ───────────────────────────────────────────────────────

echo ""
# Set compose files based on deploy mode
if [ "$NEW_MODE" = "direct" ]; then
    export COMPOSE_FILE="docker-compose.yml:docker-compose.direct.yml"
fi

echo "Bringing stack down..."
docker compose down

echo "Starting stack (init-config will regenerate server certs)..."
docker compose up -d

echo ""
echo "Done. Server cert regenerated for $NEW_ADDRESS ($NEW_MODE)."
echo "Existing client certs and enrollments are unaffected."
echo ""
echo "NOTE: Devices enrolled against the old address ($OLD_ADDRESS) need their"
echo "connection server updated to $NEW_ADDRESS. Certs are still valid —"
echo "just change the server address in the TAK client settings."

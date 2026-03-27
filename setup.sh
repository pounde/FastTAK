#!/bin/bash
# setup.sh — Set up FastTAK from a tak.gov Docker release ZIP.
# Usage: ./setup.sh [-d <target-dir>] <takserver-docker-X.X-RELEASE-X.zip>
#
# Options:
#   -d <dir>  Target directory for tak/ and .env (default: script's directory).
#             Used by integration tests to set up an isolated environment.
#
# Fresh install:  extracts tak/, builds images, creates .env with generated secrets
# Upgrade:        updates application files, preserves certs/config/logs
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$SCRIPT_DIR"

while getopts "d:" opt; do
  case $opt in
    d) TARGET_DIR="$OPTARG" ;;
    *) echo "Usage: ./setup.sh [-d <target-dir>] <zip>" >&2; exit 1 ;;
  esac
done
shift $((OPTIND - 1))

ZIP="${1:?Usage: ./setup.sh [-d <target-dir>] <takserver-docker-X.X-RELEASE-X.zip>}"

if [ ! -f "$ZIP" ]; then
  echo "ERROR: File not found: $ZIP" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          FastTAK Setup                   ║"
echo "╚══════════════════════════════════════════╝"

# ── Extract release ──────────────────────────────────────────────────────────
echo ""
echo "▸ Extracting release..."
unzip -q "$ZIP" -d "$WORK_DIR"

RELEASE_DIR=$(find "$WORK_DIR" -maxdepth 1 -type d -name 'takserver-docker-*' | head -1)
if [ -z "$RELEASE_DIR" ]; then
  echo "  ERROR: Could not find takserver-docker-* directory in ZIP" >&2
  exit 1
fi

VERSION=$(tr -d '[:space:]' < "$RELEASE_DIR/tak/version.txt" 2>/dev/null)
if [ -z "$VERSION" ]; then
  echo "  ERROR: Could not read version from tak/version.txt" >&2
  exit 1
fi
echo "  TAK Server version: $VERSION"

# ── Build Docker images ─────────────────────────────────────────────────────
echo ""
echo "▸ Building Docker images (this may take a few minutes)..."
echo "  Building takserver-database:${VERSION}..."
docker build -t "takserver-database:${VERSION}" \
  -f "$RELEASE_DIR/docker/Dockerfile.takserver-db" "$RELEASE_DIR" 2>&1 | tail -1

echo "  Building takserver:${VERSION}..."
docker build -t "takserver:${VERSION}" \
  -f "$RELEASE_DIR/docker/Dockerfile.takserver" "$RELEASE_DIR" 2>&1 | tail -1

# ── Set up tak/ directory ────────────────────────────────────────────────────
echo ""
if [ -d "$TARGET_DIR/tak" ]; then
  echo "▸ Upgrading tak/ directory (preserving certs, config, logs)..."

  PRESERVE_DIR=$(mktemp -d)
  for item in certs CoreConfig.xml CoreConfig.example.xml UserAuthenticationFile.xml logs portal; do
    [ -e "$TARGET_DIR/tak/$item" ] && cp -a "$TARGET_DIR/tak/$item" "$PRESERVE_DIR/"
  done

  rm -rf "$TARGET_DIR/tak"
  cp -a "$RELEASE_DIR/tak" "$TARGET_DIR/tak"

  for item in certs CoreConfig.xml CoreConfig.example.xml UserAuthenticationFile.xml logs portal; do
    [ -e "$PRESERVE_DIR/$item" ] && cp -a "$PRESERVE_DIR/$item" "$TARGET_DIR/tak/"
  done
  rm -rf "$PRESERVE_DIR"

  echo "  Application files updated. Certs, config, and logs preserved."
else
  echo "▸ Fresh install — extracting tak/ directory..."
  cp -a "$RELEASE_DIR/tak" "$TARGET_DIR/tak"

  # The tak.gov release may contain cert files from their build process.
  # Remove them so FastTAK generates a fresh CA on first boot.
  if [ -d "$TARGET_DIR/tak/certs/files" ]; then
    rm -f "$TARGET_DIR/tak/certs/files"/*.pem \
          "$TARGET_DIR/tak/certs/files"/*.key \
          "$TARGET_DIR/tak/certs/files"/*.jks \
          "$TARGET_DIR/tak/certs/files"/*.p12 \
          "$TARGET_DIR/tak/certs/files"/*.csr \
          "$TARGET_DIR/tak/certs/files"/*.cfg \
          "$TARGET_DIR/tak/certs/files"/*.crl \
          "$TARGET_DIR/tak/certs/files"/*.txt \
          "$TARGET_DIR/tak/certs/files"/*.attr 2>/dev/null
  fi
  echo "  Done."
fi

# ── Handle .env ──────────────────────────────────────────────────────────────
if [ ! -f "$TARGET_DIR/.env" ]; then
  echo ""
  echo "▸ Creating .env and generating secrets..."
  cp "$SCRIPT_DIR/.env.example" "$TARGET_DIR/.env"
  sed -i.bak "s/^TAK_VERSION=.*/TAK_VERSION=${VERSION}/" "$TARGET_DIR/.env"
  rm -f "$TARGET_DIR/.env.bak"

  # Fill empty values with generated secrets
  fill_secret() {
    local key="$1" val="$2"
    sed -i.bak "s|^${key}=$|${key}=${val}|" "$TARGET_DIR/.env"
    rm -f "$TARGET_DIR/.env.bak"
  }

  fill_secret TAK_DB_PASSWORD "$(openssl rand -hex 16)"
  fill_secret AUTHENTIK_SECRET_KEY "$(openssl rand -hex 32)"
  fill_secret APP_DB_PASSWORD "$(openssl rand -hex 16)"
  fill_secret AUTHENTIK_API_TOKEN "$(openssl rand -hex 32)"
  fill_secret LDAP_BIND_PASSWORD "$(openssl rand -hex 16)"

  echo "  All secrets generated on this device."
else
  # Upgrade: update TAK_VERSION if changed
  CURRENT_VERSION=$(grep '^TAK_VERSION=' "$TARGET_DIR/.env" | cut -d= -f2)
  if [ "$CURRENT_VERSION" != "$VERSION" ]; then
    sed -i.bak "s/^TAK_VERSION=.*/TAK_VERSION=${VERSION}/" "$TARGET_DIR/.env"
    rm -f "$TARGET_DIR/.env.bak"
    echo ""
    echo "▸ Updated TAK_VERSION in .env: ${CURRENT_VERSION} → ${VERSION}"
  fi
fi

# ── Verify ───────────────────────────────────────────────────────────────────
ENV_VERSION=$(grep '^TAK_VERSION=' "$TARGET_DIR/.env" | cut -d= -f2)
if [ "$ENV_VERSION" != "$VERSION" ]; then
  echo ""
  echo "  ⚠ WARNING: .env has TAK_VERSION=${ENV_VERSION} but images are ${VERSION}"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          Setup Complete                  ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Images:    takserver:${VERSION}  takserver-database:${VERSION}"
echo "  TAK dir:   ${TARGET_DIR}/tak/"
echo "  Config:    ${TARGET_DIR}/.env"
echo ""
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │ BEFORE YOU START:                                   │"
echo "  │                                                     │"
echo "  │   vim .env    ← set FQDN to your domain            │"
echo "  │                                                     │"
echo "  │ Admin login (TAK Server + TAK Portal):               │"
echo "  │   User: webadmin   Password: FastTAK-Admin-1!       │"
echo "  │                                                     │"
echo "  │ ⚠ Change TAK_WEBADMIN_PASSWORD for production.      │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
echo "  Start FastTAK:"
echo "    ./start.sh"
echo ""

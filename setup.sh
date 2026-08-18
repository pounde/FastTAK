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
# TAK_VERSION_FLOOR comes from the library too — see its header.
# shellcheck source=scripts/lib-tak-version.sh
. "$SCRIPT_DIR/scripts/lib-tak-version.sh"
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

# A version this parser cannot read is not the same failure as a version below
# the floor, and the two need different fixes: one is a malformed version.txt,
# the other is a genuinely old bundle. Reporting an unparseable string as
# "below 5.8" sends the operator hunting for a newer release they already have.
# check-env.sh draws the same distinction for TAK_VERSION in .env.
if ! tak_version_major_minor "$VERSION" >/dev/null; then
  cat >&2 <<EOF

  ERROR: TAK Server version "$VERSION" could not be parsed.

  Expected the form X.Y or X.Y-anything, e.g. 5.8-RELEASE-65 — the string
  the release bundle writes to tak/version.txt.

  This says nothing about how old the bundle is. Check
  $RELEASE_DIR/tak/version.txt, and re-download the bundle if it looks
  truncated or edited.
EOF
  exit 1
fi

# FastTAK supports the hardened bundle from TAK Server 5.8 onward only. A 5.6
# bundle would build cleanly but put PGDATA at /var/lib/postgresql/15/data
# rather than the volume mount, silently un-persisting the database — so this
# refuses rather than tolerates. See DD-051.
if ! tak_version_meets_floor "$VERSION" "$TAK_VERSION_FLOOR"; then
  cat >&2 <<EOF

  ERROR: TAK Server $VERSION is below the supported floor of $TAK_VERSION_FLOOR.

  FastTAK requires the hardened Docker bundle from TAK Server $TAK_VERSION_FLOOR
  or later (takserver-docker-hardened-X.Y-RELEASE-N.zip).

  Earlier releases place the PostgreSQL data directory outside the volume
  FastTAK mounts, so the database would not survive a container recreate.

  Download a current bundle from https://tak.gov/products/tak-server
EOF
  exit 1
fi

DOCKERFILE_DB="$RELEASE_DIR/docker/Dockerfile.hardened-takserver-db"
DOCKERFILE_SERVER="$RELEASE_DIR/docker/Dockerfile.hardened-takserver"
for f in "$DOCKERFILE_DB" "$DOCKERFILE_SERVER"; do
  if [ ! -f "$f" ]; then
    cat >&2 <<EOF

  ERROR: $(basename "$f") not found in the release bundle.

  FastTAK builds the hardened images. This looks like a standard (non-hardened)
  bundle — download takserver-docker-hardened-X.Y-RELEASE-N.zip instead.
EOF
    exit 1
  fi
done

# ── Build Docker images ─────────────────────────────────────────────────────
# Build one image, or abort with the tail of the build log. A bare
# `docker build ... | tail -1` under `set -e` cannot fail the script: the
# pipeline's status is tail's, so a failed build would print "Setup Complete"
# with no images ever created.
build_image() {
  local tag="$1" dockerfile="$2" log status
  log="$(mktemp)"
  status=0
  docker build -t "$tag" -f "$dockerfile" "$RELEASE_DIR" > "$log" 2>&1 || status=$?
  if [ "$status" -ne 0 ]; then
    echo "" >&2
    echo "  ERROR: build of $tag failed. Last 40 lines:" >&2
    tail -40 "$log" >&2
    rm -f "$log"
    exit 1
  fi
  tail -1 "$log"
  rm -f "$log"
}

echo ""
echo "▸ Building Docker images (this may take a few minutes)..."
echo "  Note: the hardened images install packages from the Rocky, EPEL,"
echo "        Adoptium and PGDG repositories during the build. This step"
echo "        requires outbound network access."
echo "  Building takserver-database:${VERSION}..."
build_image "takserver-database:${VERSION}" "$DOCKERFILE_DB"

echo "  Building takserver:${VERSION}..."
build_image "takserver:${VERSION}" "$DOCKERFILE_SERVER"

# ── Set up tak/ directory ────────────────────────────────────────────────────
echo ""
# Which closing instruction the operator gets depends on this: an upgrade must
# go through `just upgrade`, not ./start.sh. See the summary below.
IS_UPGRADE=false
if [ -d "$TARGET_DIR/tak" ]; then
  IS_UPGRADE=true
  echo "▸ Upgrading tak/ directory (preserving certs, config, logs)..."

  PRESERVE_DIR=$(mktemp -d)
  for item in certs CoreConfig.xml CoreConfig.example.xml UserAuthenticationFile.xml logs; do
    [ -e "$TARGET_DIR/tak/$item" ] && cp -a "$TARGET_DIR/tak/$item" "$PRESERVE_DIR/"
  done

  rm -rf "$TARGET_DIR/tak"
  cp -a "$RELEASE_DIR/tak" "$TARGET_DIR/tak"

  for item in certs CoreConfig.xml CoreConfig.example.xml UserAuthenticationFile.xml logs; do
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

# The release zip stores every file mode 0666 — no execute bit, on any file,
# including the vendor's own *.sh. TAK's Dockerfile.hardened-takserver-db
# compensates at build time (find /opt/tak -name "*.sh" -exec chmod u=rx), but
# FastTAK bind-mounts this host-extracted tak/ over /opt/tak in tak-server,
# shadowing that build-time fix — the container sees the host tree's modes,
# not the image's. The vendor's own README_hardened_docker.md recommends
# `chmod -R u+rwX ./tak` for exactly this reason.
find "$TARGET_DIR/tak" -name "*.sh" -exec chmod u+rx {} +

# Copy scripts into tak/ so file bind mounts overlay correctly on Docker Desktop.
# Without this, Docker creates empty mountpoints that virtiofs can't resolve.
# Must run after both fresh-install and upgrade paths since both replace tak/.
# Uses cat+redirect to avoid cp -i alias prompting in interactive shells.
for script in healthcheck.sh register-api-cert.sh; do
  cat "$SCRIPT_DIR/tak-server/$script" > "$TARGET_DIR/tak/$script"
  chmod +x "$TARGET_DIR/tak/$script"
done

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

  # Fresh install only: empty means "skip webadmin user creation", so this must
  # not be filled on an upgrade. Every other required secret is provisioned
  # below by ensure-secrets.sh, which runs for fresh installs and upgrades
  # alike.
  fill_secret TAK_WEBADMIN_PASSWORD "$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
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

# Required secrets, for fresh installs and upgrades alike. Outside the branch
# above on purpose: work placed inside the fresh-install arm never runs for an
# existing deployment, which is how a secret added by a later release goes
# missing everywhere it was already installed.
if ! "$SCRIPT_DIR/scripts/ensure-secrets.sh" "$TARGET_DIR/.env"; then
  echo "ERROR: could not provision required secrets in $TARGET_DIR/.env" >&2
  exit 1
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
echo "  │   vim .env ← set SERVER_ADDRESS and DEPLOY_MODE    │"
echo "  │                                                     │"
echo "  │ Admin login (TAK Server):                           │"
echo "  │   User: webadmin                                    │"
echo "  │   Password: in .env as TAK_WEBADMIN_PASSWORD        │"
echo "  │                                                     │"
echo "  │ View:  grep TAK_WEBADMIN_PASSWORD .env              │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
if [ "$IS_UPGRADE" = true ]; then
  # ./start.sh here is destructive on a pre-5.8 deployment. It runs
  # `docker compose up -d`, the tak-database image tag has just changed, so the
  # container is recreated — and before 5.8 the whole cot database lives in
  # that container's writable layer rather than on the volume. The CoT history
  # is gone before any backup exists. `just upgrade` takes the backup first.
  echo "  Finish the upgrade:"
  echo "    just upgrade"
  echo ""
  echo "  Do NOT run ./start.sh first. Releases before TAK Server 5.8 keep the"
  echo "  CoT history inside the tak-database container, not on its volume, so"
  echo "  starting the stack now recreates that container on the new image and"
  echo "  destroys the history before any backup has been taken."
  echo ""
  echo "  \`just upgrade\` takes the backup, migrates the databases and brings"
  echo "  the stack up. If nothing needs migrating it says so and leaves the"
  echo "  stack alone. See docs/upgrading.md."
else
  echo "  Start FastTAK:"
  echo "    ./start.sh"
fi
echo ""

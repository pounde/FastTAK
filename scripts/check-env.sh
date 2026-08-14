#!/bin/bash
# scripts/check-env.sh — Preflight .env validator for FastTAK.
# Exits 0 on success, 1 with a clear error message on failure.
# Usage: ./scripts/check-env.sh <path-to-.env>
#
# Rules apply unconditionally — DEPLOY_MODE is not consulted.
# Security defaults are universal; DEPLOY_MODE stays a pure routing/cert choice (DD-029).

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/lib-env.sh"
# shellcheck source=scripts/lib-tak-version.sh
. "$SCRIPT_DIR/lib-tak-version.sh"
TAK_VERSION_FLOOR="5.8"

ENV_FILE="${1:-.env}"
DEFAULT_WEBADMIN_PASSWORD="FastTAK-Admin-1!"

# Shared with ensure-secrets.sh via lib-env.sh, so the validator and the
# provisioner cannot disagree about whether a key is set.
get_env_value() {
  env_get "$ENV_FILE" "$1"
}

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found at $ENV_FILE" >&2
  echo "Run ./setup.sh <takserver-docker-X.X.zip> first." >&2
  exit 1
fi

SERVER_ADDRESS=$(get_env_value SERVER_ADDRESS)
WEBADMIN_PASSWORD=$(get_env_value TAK_WEBADMIN_PASSWORD)
TOKENS_API_SECRET=$(get_env_value TOKENS_API_SECRET)
TAK_VERSION=$(get_env_value TAK_VERSION)

# ── SERVER_ADDRESS ─────────────────────────────────────────────────────────
if [ -z "$SERVER_ADDRESS" ] || [ "$SERVER_ADDRESS" = "tak.example.com" ]; then
  echo "ERROR: SERVER_ADDRESS is unset or still the placeholder in $ENV_FILE." >&2
  echo "Edit $ENV_FILE and set SERVER_ADDRESS to your IP, hostname, or FQDN." >&2
  exit 1
fi

# ── TAK_WEBADMIN_PASSWORD ──────────────────────────────────────────────────
# Empty is permitted — it's the existing "skip webadmin user creation" escape
# hatch from .env.example, preserved so cert-only deployments keep working.
# The documented default is always rejected — it is public knowledge (README
# and .env.example history), so any install running it has effectively
# published its admin credentials.
if [ "$WEBADMIN_PASSWORD" = "$DEFAULT_WEBADMIN_PASSWORD" ]; then
  cat >&2 <<EOF
ERROR: TAK_WEBADMIN_PASSWORD is set to the documented default in $ENV_FILE.

The default password ($DEFAULT_WEBADMIN_PASSWORD) is public knowledge —
documented in prior README versions and .env.example — and must be
changed before the stack can start.

Generate a random replacement:
  NEW_PW=\$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)
  sed -i.bak "s|^TAK_WEBADMIN_PASSWORD=.*|TAK_WEBADMIN_PASSWORD=\${NEW_PW}|" $ENV_FILE && rm -f ${ENV_FILE}.bak

Or set your own strong password in $ENV_FILE.
Or leave it empty to skip webadmin user creation entirely.
EOF
  exit 1
fi

# ── TOKENS_API_SECRET ──────────────────────────────────────────────────────
# ldap-proxy calls log.Fatal without it, init-ldap-ready waits on ldap-proxy
# and tak-server waits on init-ldap-ready, so an empty value takes the whole
# stack down. ensure-secrets.sh normally fills it before this check runs; this
# rule is the backstop for a .env reached some other way, so the failure is a
# named key rather than a stack that never comes up.
if [ -z "$TOKENS_API_SECRET" ]; then
  cat >&2 <<EOF
ERROR: TOKENS_API_SECRET is unset or empty in $ENV_FILE.

ldap-proxy refuses to start without it, and tak-server waits on ldap-proxy,
so the whole stack stays down. An unauthenticated token API would let any
workload on the Docker network mint LDAP credentials for any user (DD-050).

Generate one:
  ./scripts/ensure-secrets.sh $ENV_FILE

Or set it by hand:
  openssl rand -hex 32
EOF
  exit 1
fi

# ── TAK_VERSION ────────────────────────────────────────────────────────────
# setup.sh refuses a below-floor release bundle, but .env can be edited by
# hand. Compose interpolates this into the image tags, so an empty value
# produces `takserver:` — an unresolvable tag and a confusing pull error.
if [ -z "$TAK_VERSION" ]; then
  cat >&2 <<EOF
ERROR: TAK_VERSION is unset or empty in $ENV_FILE.

Compose builds the image tags from it, so an empty value leaves the stack
pulling "takserver:" — which does not exist.

Re-run ./setup.sh <takserver-docker-hardened-X.Y-RELEASE-N.zip>, which sets
this from the bundle's tak/version.txt.
EOF
  exit 1
fi

if ! tak_version_major_minor "$TAK_VERSION" >/dev/null; then
  cat >&2 <<EOF
ERROR: TAK_VERSION=$TAK_VERSION in $ENV_FILE could not be parsed.

Expected the form X.Y or X.Y-anything, e.g. 5.8-RELEASE-65.

Check $ENV_FILE for a typo — a patch-style value like 5.8.65 is not the
same as the release-bundle version string 5.8-RELEASE-65.
EOF
  exit 1
fi

if ! tak_version_meets_floor "$TAK_VERSION" "$TAK_VERSION_FLOOR"; then
  cat >&2 <<EOF
ERROR: TAK_VERSION=$TAK_VERSION in $ENV_FILE is below the supported floor of $TAK_VERSION_FLOOR.

FastTAK supports the hardened TAK Server bundle from $TAK_VERSION_FLOOR onward.
Earlier releases place the PostgreSQL data directory outside the volume FastTAK
mounts, so the database would not survive a container recreate.

Re-run ./setup.sh with a current bundle from https://tak.gov/products/tak-server
EOF
  exit 1
fi

exit 0

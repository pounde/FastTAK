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

exit 0

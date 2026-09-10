#!/bin/bash
# start.sh — Start and verify FastTAK
# Usage:
#   ./start.sh [--capture] [--checks|--no-checks] [--no-wait] [service...]
#
#   service...    rebuild and force-recreate only these services
#   --capture     include the mitmproxy capture overlay
#   --checks      run the post-start checks (default for a whole-stack start)
#   --no-checks   skip them (default when services are named)
#   --no-wait     do not wait for tak-server to report healthy

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# The shared .env reader, so this script and scripts/check-env.sh resolve a
# value identically. It follows Compose's own dotenv semantics — notably
# stripping surrounding quotes, which the `grep | cut -d= -f2` this replaced did
# not: `DEPLOY_MODE="direct"` read as `"direct"` here, matched nothing, and
# silently selected the subdomain compose files — while Compose, parsing the
# same .env for its own variable substitution, had resolved it to `direct`.
[ -r "$SCRIPT_DIR/scripts/lib-env.sh" ] || {
  echo "ERROR: scripts/lib-env.sh is missing; cannot read .env." >&2
  exit 1
}
# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/scripts/lib-env.sh"

# shellcheck source=scripts/lib-stack.sh
. "$SCRIPT_DIR/scripts/lib-stack.sh"

# The deployment this run operates on. FASTAK_ENV_FILE points the preflight
# and Compose at another directory, which is how the test suite drives this
# script against a scratch deployment.
ENV_FILE="${FASTAK_ENV_FILE:-$SCRIPT_DIR/.env}"
DEPLOY_DIR="$(cd "$(dirname "$ENV_FILE")" && pwd)"

compose() { docker compose --env-file "$ENV_FILE" "$@"; }

PASS=0
FAIL=0
VERBOSE=false

CAPTURE=false
WAIT=true
CHECKS=""        # empty: decided below from whether services were named
SERVICES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --capture)   CAPTURE=true ;;
    --checks)    CHECKS=true ;;
    --no-checks) CHECKS=false ;;
    --no-wait)   WAIT=false ;;
    -*) echo "Unknown option: $1" >&2; exit 2 ;;
    *) SERVICES+=("$1") ;;
  esac
  shift
done

# A targeted rebuild cannot affect CoreConfig, the certs or TAK's processes,
# so the whole-stack checks are noise there. The invocation says which was
# meant; the flags override.
if [ -z "$CHECKS" ]; then
  if [ ${#SERVICES[@]} -gt 0 ]; then CHECKS=false; else CHECKS=true; fi
fi

# ── Helpers ──────────────────────────────────────────────────────────────────

log() { if $VERBOSE; then echo "  $1"; fi; }

pass() {
  PASS=$((PASS + 1))
  if $VERBOSE; then echo "  ✅ $1"; fi
}

fail() {
  FAIL=$((FAIL + 1))
  echo "  ❌ $1"
}

assert()      { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got: $1)"; fi; }
assert_not()  { if [ "$1" != "$2" ]; then pass "$3"; else fail "$3 (got: $1)"; fi; }
assert_file() { if [ -f "$1" ]; then pass "$2"; else fail "$2"; fi; }
assert_grep() { if grep -q "$1" "$2" 2>/dev/null; then pass "$3"; else fail "$3"; fi; }
assert_port() { if nc -z localhost "$1" 2>/dev/null; then pass "Port $1 ($2)"; else fail "Port $1 ($2)"; fi; }

# A condition that is legitimately absent rather than broken. Not counted as a
# failure — a start script that reports "5 checks failed" on a healthy stack
# trains the operator to ignore it.
note() { if $VERBOSE; then echo "  – $1"; fi; }

# Verify a port only if this deployment actually publishes it. Which ports are
# published depends on DEPLOY_MODE (docker-compose.direct.yml adds the UI
# ports) and on the operator's docker-compose.override.yml, which may remove
# some deliberately. Asking compose is the only reading that stays true across
# both.
assert_published_port() {
  _svc="$1"; _cport="$2"; _label="$3"
  _mapping=$(compose port "$_svc" "$_cport" 2>/dev/null | head -1)
  if [ -n "$_mapping" ]; then
    assert_port "${_mapping##*:}" "$_label"
    return
  fi
  # No runtime mapping. Distinguish "deliberately not published" from "the
  # service is not running" — the latter would otherwise read as a config
  # choice and pass silently.
  if [ -z "$(compose ps -q "$_svc" 2>/dev/null)" ]; then
    fail "$_label — $_svc is not running"
  else
    note "$_label not published by this compose configuration"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
# PREFLIGHT
# ═══════════════════════════════════════════════════════════════════════════

if [ ! -d "$DEPLOY_DIR/tak" ]; then
  echo "ERROR: tak/ not found. Run: ./setup.sh <zip>" >&2; exit 1
fi
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found. Run: ./setup.sh <zip>" >&2; exit 1
fi
# Provision here as well as in setup.sh: a FastTAK-only upgrade is `git pull`
# with no new TAK zip, which never runs setup.sh. The launch is the one step
# every upgrade path takes. This script does not use `set -e`, so the exit
# status is checked explicitly.
if ! "$SCRIPT_DIR/scripts/ensure-secrets.sh" "$ENV_FILE"; then
  exit 1
fi
if ! "$SCRIPT_DIR/scripts/check-env.sh" "$ENV_FILE"; then
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════
# START
# ═══════════════════════════════════════════════════════════════════════════

SERVER_ADDRESS=$(env_get "$ENV_FILE" SERVER_ADDRESS)
DEPLOY_MODE=$(env_get "$ENV_FILE" DEPLOY_MODE)
DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"

if $CAPTURE; then
  FASTAK_ENV_FILE="$ENV_FILE" stack_export_compose_file --capture
  mkdir -p captures capture/mitm
else
  FASTAK_ENV_FILE="$ENV_FILE" stack_export_compose_file
fi
stack_export_version

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       Starting FastTAK                   ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Address: $SERVER_ADDRESS"
echo "  Mode:    $DEPLOY_MODE"
echo ""

log ""
log "Start"
log "─────"

echo "  ⏳ Building containers..."
if [ ${#SERVICES[@]} -gt 0 ]; then
  compose build --quiet "${SERVICES[@]}" 2>/dev/null
else
  compose build --quiet 2>/dev/null
fi

echo "  ⏳ Starting services..."
# --remove-orphans: containers for services deleted from the compose file are
# NOT removed by a plain `up`; tak-portal kept running for months after
# DD-043 removed it. Only for a whole-stack up — a targeted rebuild must not
# prune the project, which is what silently removed the capture sidecars.
if [ ${#SERVICES[@]} -gt 0 ]; then
  compose up -d --force-recreate "${SERVICES[@]}" > /dev/null 2>&1
else
  compose up -d --remove-orphans > /dev/null 2>&1
fi

if $WAIT; then
  echo "  ⏳ Waiting for tak-server..."
  STATUS="unknown"
  for _ in $(seq 1 48); do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q tak-server 2>/dev/null)" 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "healthy" ]; then break; fi
    if [ "$STATUS" = "unhealthy" ]; then
      echo "  ❌ tak-server failed — run: docker compose logs tak-server"
      exit 1
    fi
    sleep 10
  done

  if [ "$STATUS" != "healthy" ]; then
    echo "  ❌ tak-server timed out — run: docker compose logs tak-server"
    exit 1
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECKS
# ═══════════════════════════════════════════════════════════════════════════

if $CHECKS; then

log ""
log "Services"
log "────────"

TAK_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q tak-server 2>/dev/null)" 2>/dev/null || echo unknown)
assert "$TAK_STATUS" "healthy" "TAK Server healthy"

DB_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q tak-database 2>/dev/null)" 2>/dev/null)
assert "$DB_STATUS" "healthy" "TAK Database healthy"

INIT_EXIT=$(docker inspect --format='{{.State.ExitCode}}' "$(compose ps -aq init-config 2>/dev/null)" 2>/dev/null)
assert "$INIT_EXIT" "0" "init-config exited 0"

ID_EXIT=$(docker inspect --format='{{.State.ExitCode}}' "$(compose ps -aq init-identity 2>/dev/null)" 2>/dev/null)
assert "$ID_EXIT" "0" "init-identity exited 0"

LLDAP_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(compose ps -q lldap 2>/dev/null)" 2>/dev/null)
assert "$LLDAP_STATUS" "healthy" "LLDAP healthy"

PROXY_STATE=$(docker inspect --format='{{.State.Status}}' "$(compose ps -q ldap-proxy 2>/dev/null)" 2>/dev/null)
assert "$PROXY_STATE" "running" "ldap-proxy running"

log ""
log "Config"
log "──────"

assert_file "$DEPLOY_DIR/tak/CoreConfig.xml" "CoreConfig.xml"
CC_PASS=$(grep -o '<connection[^>]*password="[^"]*"' "$DEPLOY_DIR/tak/CoreConfig.xml" | sed 's/.*password="//;s/"//')
assert_not "$CC_PASS" "" "DB password set"
assert_grep "tak-database:5432" "$DEPLOY_DIR/tak/CoreConfig.xml" "DB host"
assert_grep 'enableAdminUI="true"' "$DEPLOY_DIR/tak/CoreConfig.xml" "Admin UI enabled"
assert_grep '<certificateSigning CA="TAKServer">' "$DEPLOY_DIR/tak/CoreConfig.xml" "Certificate signing"
assert_grep "adm_ldapservice" "$DEPLOY_DIR/tak/CoreConfig.xml" "LDAP auth"
assert_grep 'adminGroup="ROLE_ADMIN"' "$DEPLOY_DIR/tak/CoreConfig.xml" "ROLE_ADMIN"

log ""
log "Certificates"
log "────────────"

assert_file "$DEPLOY_DIR/tak/certs/files/root-ca.pem" "Root CA"
assert_file "$DEPLOY_DIR/tak/certs/files/ca.pem" "Intermediate CA"
assert_file "$DEPLOY_DIR/tak/certs/files/takserver.jks" "Server cert"
assert_file "$DEPLOY_DIR/tak/certs/files/svc_fasttakapi.p12" "API service cert"
# svc_nodered is NOT created at bootstrap — init-identity's SERVICE_ACCOUNTS
# is just svc_fasttakapi. The monitor writes these PEMs when a data-mode
# service account is created, so on a fresh install the file is absent and
# that is correct.
if [ -f "$DEPLOY_DIR/tak/certs/files/svc_nodered.p12" ]; then
  pass "Node-RED service cert"
else
  note "Node-RED service cert not present (created on demand via the Monitor)"
fi
assert_file "$DEPLOY_DIR/tak/certs/files/ca-signing.jks" "CA signing keystore"
if ./certs.sh ca-info > /dev/null 2>&1; then pass "certs.sh ca-info"; else fail "certs.sh ca-info"; fi
if ./certs.sh list > /dev/null 2>&1; then pass "certs.sh list"; else fail "certs.sh list"; fi

log ""
log "Ports"
log "─────"

TAKSERVER_ADMIN_PORT=$(env_get "$ENV_FILE" TAKSERVER_ADMIN_PORT)
TAKSERVER_ADMIN_PORT="${TAKSERVER_ADMIN_PORT:-8446}"
MEDIAMTX_PORT=$(env_get "$ENV_FILE" MEDIAMTX_PORT)
MEDIAMTX_PORT="${MEDIAMTX_PORT:-8888}"
NODERED_PORT=$(env_get "$ENV_FILE" NODERED_PORT)
NODERED_PORT="${NODERED_PORT:-1880}"

assert_published_port tak-server 8089 "CoT TLS"
assert_published_port tak-server 8443 "Cert HTTPS"
assert_published_port tak-server "$TAKSERVER_ADMIN_PORT" "Admin HTTPS"
assert_published_port mediamtx "$MEDIAMTX_PORT" "MediaMTX HLS"
assert_published_port mediamtx 8554 "MediaMTX RTSP"
assert_published_port nodered "$NODERED_PORT" "Node-RED"

HTTP_8446=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "https://localhost:${TAKSERVER_ADMIN_PORT}" 2>/dev/null)
assert_not "$HTTP_8446" "000" "${TAKSERVER_ADMIN_PORT} TLS (HTTP $HTTP_8446)"

log ""
log "Health"
log "──────"

# Delegate to the container's own healthcheck rather than counting with
# `ps`: the TAK 5.8 hardened image ships no procps, so `ps aux` fails and
# every process reads as missing on a perfectly healthy server. The
# healthcheck matches /proc/*/cmdline and already knows the five processes.
if TAK_HEALTH=$(docker exec "$(compose ps -q tak-server)" /opt/tak/healthcheck.sh 2>&1); then
  pass "TAK Server processes ($TAK_HEALTH)"
else
  fail "TAK Server processes ($TAK_HEALTH)"
fi

DB_FAILS=$(docker exec "$(compose ps -q tak-server)" grep -c "password authentication failed" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
DB_FAILS="${DB_FAILS:-0}"
if [ "$DB_FAILS" -le 2 ] 2>/dev/null; then pass "DB auth (failures: $DB_FAILS)"; else fail "DB auth failures: $DB_FAILS"; fi

OOM=$(docker exec "$(compose ps -q tak-server)" grep -c "OutOfMemoryError" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
OOM="${OOM:-0}"
assert "$OOM" "0" "No OutOfMemoryError"

SEC_COUNT=$(docker exec "$(compose ps -q tak-server)" grep -c "Security status" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
SEC_COUNT="${SEC_COUNT:-0}"
if [ "$SEC_COUNT" -le 4 ] 2>/dev/null; then pass "Single start (status: $SEC_COUNT)"; else fail "Multiple starts ($SEC_COUNT)"; fi

fi

# ═══════════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════════

if $CHECKS; then
  TOTAL=$((PASS + FAIL))
  if [ $FAIL -eq 0 ]; then
    echo "  ✅ All checks passed ($PASS/$TOTAL)"
  else
    echo "  ⚠️  $FAIL checks failed ($PASS/$TOTAL passed)"
  fi
else
  echo "  – Checks skipped (targeted start). Run ./start.sh --checks to verify the whole stack."
fi

WA_PASS=$(env_get "$ENV_FILE" TAK_WEBADMIN_PASSWORD)
WA_MASKED="${WA_PASS:0:4}***"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       FastTAK is running                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  TAK Server:  https://${SERVER_ADDRESS}:${TAKSERVER_ADMIN_PORT}"
echo "               webadmin / ${WA_MASKED}"
if [ "$DEPLOY_MODE" = "direct" ]; then
  MONITOR_PORT_OUT=$(env_get "$ENV_FILE" MONITOR_PORT)
  MONITOR_PORT_OUT="${MONITOR_PORT_OUT:-8180}"
  echo "  Monitor:     https://${SERVER_ADDRESS}:${MONITOR_PORT_OUT}"
else
  MONITOR_SUB=$(env_get "$ENV_FILE" MONITOR_SUBDOMAIN)
  MONITOR_SUB="${MONITOR_SUB:-monitor}"
  echo "  Monitor:     https://${MONITOR_SUB}.${SERVER_ADDRESS}"
fi
echo ""
echo "  Passwords:   cat .env"
echo "  Stop:        just down"
echo "  Reset DBs:   docker compose down -v"
echo ""

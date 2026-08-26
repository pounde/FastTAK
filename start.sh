#!/bin/bash
# start.sh — Start and verify FastTAK
# Usage:
#   ./start.sh                       Start the stack, run checks
#   ./start.sh --test <zip>          Greenfield: setup → start → verify → teardown

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

TEST=false
ZIP=""
PASS=0
FAIL=0
VERBOSE=false

while [ $# -gt 0 ]; do
  case "$1" in
    --test)
      TEST=true
      VERBOSE=true
      ZIP="${2:?--test requires a ZIP file path}"
      shift 2
      ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

log() { if $VERBOSE; then echo "  $1"; fi; }

pass() {
  PASS=$((PASS + 1))
  if $VERBOSE; then echo "  ✅ $1"; fi
}

fail() {
  FAIL=$((FAIL + 1))
  echo "  ❌ $1"
  if $TEST; then
    echo ""
    echo "  FAILED — tearing down..."
    docker compose down -v 2>/dev/null
    rm -rf tak/ .env
    echo "  $PASS passed, $FAIL failed"
    exit 1
  fi
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
  _mapping=$(docker compose port "$_svc" "$_cport" 2>/dev/null | head -1)
  if [ -n "$_mapping" ]; then
    assert_port "${_mapping##*:}" "$_label"
    return
  fi
  # No runtime mapping. Distinguish "deliberately not published" from "the
  # service is not running" — the latter would otherwise read as a config
  # choice and pass silently.
  if [ -z "$(docker compose ps -q "$_svc" 2>/dev/null)" ]; then
    fail "$_label — $_svc is not running"
  else
    note "$_label not published by this compose configuration"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
# TEST MODE — greenfield setup
# ═══════════════════════════════════════════════════════════════════════════

if $TEST; then
  if [ -d "$SCRIPT_DIR/tak" ] || [ -f "$SCRIPT_DIR/.env" ]; then
    echo "ERROR: Existing deployment found (tak/ or .env exist)." >&2
    echo "Tear down first: docker compose down -v && rm -rf tak/ .env" >&2
    exit 1
  fi

  echo ""
  echo "╔══════════════════════════════════════════╗"
  echo "║        FastTAK Integration Test          ║"
  echo "╚══════════════════════════════════════════╝"

  log ""
  log "Setup"
  log "─────"

  if ./setup.sh "$ZIP" > /tmp/fastak-setup.log 2>&1; then pass "setup.sh"; else fail "setup.sh"; fi

  assert_file "tak/CoreConfig.example.xml" "CoreConfig template"

  CERT_COUNT=$(find tak/certs/files -name '*.pem' -o -name '*.jks' -o -name '*.p12' 2>/dev/null | wc -l | tr -d ' ')
  assert "$CERT_COUNT" "0" "Clean cert directory"
  assert_file ".env" ".env created"

  DB_PASS=$(env_get .env TAK_DB_PASSWORD)
  LDAP_PASS=$(env_get .env LDAP_BIND_PASSWORD)
  assert_not "$DB_PASS" "" "TAK_DB_PASSWORD generated"
  assert_not "$LDAP_PASS" "" "LDAP_BIND_PASSWORD generated"

  TAK_VER=$(env_get .env TAK_VERSION)
  if docker image inspect "takserver:${TAK_VER}" > /dev/null 2>&1; then pass "Image: takserver:${TAK_VER}"; else fail "Image: takserver:${TAK_VER}"; fi
  if docker image inspect "takserver-database:${TAK_VER}" > /dev/null 2>&1; then pass "Image: takserver-database:${TAK_VER}"; else fail "Image: takserver-database:${TAK_VER}"; fi

  sed -i.bak 's/^SERVER_ADDRESS=.*/SERVER_ADDRESS=localhost/' .env && rm -f .env.bak
  sed -i.bak 's/^DEPLOY_MODE=.*/DEPLOY_MODE=direct/' .env && rm -f .env.bak
fi

# ═══════════════════════════════════════════════════════════════════════════
# PREFLIGHT
# ═══════════════════════════════════════════════════════════════════════════

if ! $TEST; then
  if [ ! -d "$SCRIPT_DIR/tak" ]; then
    echo "ERROR: tak/ not found. Run: ./setup.sh <zip>" >&2; exit 1
  fi
  if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "ERROR: .env not found. Run: ./setup.sh <zip>" >&2; exit 1
  fi
  # Provision here as well as in setup.sh: a FastTAK-only upgrade is `git pull`
  # with no new TAK zip, which never runs setup.sh. The launch is the one step
  # every upgrade path takes. This script does not use `set -e`, so the exit
  # status is checked explicitly.
  if ! "$SCRIPT_DIR/scripts/ensure-secrets.sh" "$SCRIPT_DIR/.env"; then
    exit 1
  fi
  if ! "$SCRIPT_DIR/scripts/check-env.sh" "$SCRIPT_DIR/.env"; then
    exit 1
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# START
# ═══════════════════════════════════════════════════════════════════════════

SERVER_ADDRESS=$(env_get .env SERVER_ADDRESS)
DEPLOY_MODE=$(env_get .env DEPLOY_MODE)
DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"

# Set compose file based on deploy mode. An explicit COMPOSE_FILE disables
# compose's override auto-load; re-append docker-compose.override.yml last
# so it still wins.
if [ "$DEPLOY_MODE" = "direct" ]; then
  export COMPOSE_FILE="docker-compose.yml:docker-compose.direct.yml"
  if [ -f docker-compose.override.yml ]; then
    export COMPOSE_FILE="$COMPOSE_FILE:docker-compose.override.yml"
  fi
fi

# Surface FastTAK version + commit to the monitor image build.
if [ -f pyproject.toml ]; then
    FASTTAK_VERSION="$(awk -F'"' '/^version *=/{print $2; exit}' pyproject.toml 2>/dev/null || true)"
fi
if [ -z "${FASTTAK_VERSION:-}" ] && command -v git >/dev/null 2>&1; then
    FASTTAK_VERSION="$(git describe --tags --always 2>/dev/null || echo dev)"
fi
FASTTAK_VERSION="${FASTTAK_VERSION:-dev}"

if command -v git >/dev/null 2>&1; then
    FASTTAK_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
else
    FASTTAK_COMMIT="unknown"
fi
export FASTTAK_VERSION FASTTAK_COMMIT

if ! $TEST; then
  echo ""
  echo "╔══════════════════════════════════════════╗"
  echo "║       Starting FastTAK                   ║"
  echo "╚══════════════════════════════════════════╝"
  echo ""
  echo "  Address: $SERVER_ADDRESS"
  echo "  Mode:    $DEPLOY_MODE"
  echo ""
fi

log ""
log "Start"
log "─────"

echo "  ⏳ Building containers..."
docker compose build --quiet 2>/dev/null

echo "  ⏳ Starting services..."
# --remove-orphans: containers for services deleted from the compose file are
# NOT removed by a plain `up`. Without this an upgrade leaves the old container
# running on its previous config, invisible to compose — e.g. tak-portal kept
# running for months after DD-043 removed it, unauthenticated.
docker compose up -d --remove-orphans > /dev/null 2>&1

echo "  ⏳ Waiting for tak-server..."
STATUS="unknown"
for _ in $(seq 1 48); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q tak-server 2>/dev/null)" 2>/dev/null || echo "unknown")
  if [ "$STATUS" = "healthy" ]; then break; fi
  if [ "$STATUS" = "unhealthy" ]; then
    echo "  ❌ tak-server failed — run: docker compose logs tak-server"
    if $TEST; then docker compose down -v 2>/dev/null; rm -rf tak/ .env; fi
    exit 1
  fi
  sleep 10
done

if [ "$STATUS" != "healthy" ]; then
  echo "  ❌ tak-server timed out — run: docker compose logs tak-server"
  if $TEST; then docker compose down -v 2>/dev/null; rm -rf tak/ .env; fi
  exit 1
fi

# ═══════════════════════════════════════════════════════════════════════════
# CHECKS
# ═══════════════════════════════════════════════════════════════════════════

log ""
log "Services"
log "────────"

assert "$STATUS" "healthy" "TAK Server healthy"

DB_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q tak-database 2>/dev/null)" 2>/dev/null)
assert "$DB_STATUS" "healthy" "TAK Database healthy"

INIT_EXIT=$(docker inspect --format='{{.State.ExitCode}}' "$(docker compose ps -aq init-config 2>/dev/null)" 2>/dev/null)
assert "$INIT_EXIT" "0" "init-config exited 0"

ID_EXIT=$(docker inspect --format='{{.State.ExitCode}}' "$(docker compose ps -aq init-identity 2>/dev/null)" 2>/dev/null)
assert "$ID_EXIT" "0" "init-identity exited 0"

LLDAP_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q lldap 2>/dev/null)" 2>/dev/null)
assert "$LLDAP_STATUS" "healthy" "LLDAP healthy"

PROXY_STATE=$(docker inspect --format='{{.State.Status}}' "$(docker compose ps -q ldap-proxy 2>/dev/null)" 2>/dev/null)
assert "$PROXY_STATE" "running" "ldap-proxy running"

log ""
log "Config"
log "──────"

assert_file "tak/CoreConfig.xml" "CoreConfig.xml"
CC_PASS=$(grep -o '<connection[^>]*password="[^"]*"' tak/CoreConfig.xml | sed 's/.*password="//;s/"//')
assert_not "$CC_PASS" "" "DB password set"
assert_grep "tak-database:5432" "tak/CoreConfig.xml" "DB host"
assert_grep 'enableAdminUI="true"' "tak/CoreConfig.xml" "Admin UI enabled"
assert_grep '<certificateSigning CA="TAKServer">' "tak/CoreConfig.xml" "Certificate signing"
assert_grep "adm_ldapservice" "tak/CoreConfig.xml" "LDAP auth"
assert_grep 'adminGroup="ROLE_ADMIN"' "tak/CoreConfig.xml" "ROLE_ADMIN"

log ""
log "Certificates"
log "────────────"

assert_file "tak/certs/files/root-ca.pem" "Root CA"
assert_file "tak/certs/files/ca.pem" "Intermediate CA"
assert_file "tak/certs/files/takserver.jks" "Server cert"
assert_file "tak/certs/files/svc_fasttakapi.p12" "API service cert"
# svc_nodered is NOT created at bootstrap — init-identity's SERVICE_ACCOUNTS
# is just svc_fasttakapi. The monitor writes these PEMs when a data-mode
# service account is created, so on a fresh install the file is absent and
# that is correct.
if [ -f "tak/certs/files/svc_nodered.p12" ]; then
  pass "Node-RED service cert"
else
  note "Node-RED service cert not present (created on demand via the Monitor)"
fi
assert_file "tak/certs/files/ca-signing.jks" "CA signing keystore"
if ./certs.sh ca-info > /dev/null 2>&1; then pass "certs.sh ca-info"; else fail "certs.sh ca-info"; fi
if ./certs.sh list > /dev/null 2>&1; then pass "certs.sh list"; else fail "certs.sh list"; fi

log ""
log "Ports"
log "─────"

TAKSERVER_ADMIN_PORT=$(env_get .env TAKSERVER_ADMIN_PORT)
TAKSERVER_ADMIN_PORT="${TAKSERVER_ADMIN_PORT:-8446}"
MEDIAMTX_PORT=$(env_get .env MEDIAMTX_PORT)
MEDIAMTX_PORT="${MEDIAMTX_PORT:-8888}"
NODERED_PORT=$(env_get .env NODERED_PORT)
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
if TAK_HEALTH=$(docker exec "$(docker compose ps -q tak-server)" /opt/tak/healthcheck.sh 2>&1); then
  pass "TAK Server processes ($TAK_HEALTH)"
else
  fail "TAK Server processes ($TAK_HEALTH)"
fi

DB_FAILS=$(docker exec "$(docker compose ps -q tak-server)" grep -c "password authentication failed" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
DB_FAILS="${DB_FAILS:-0}"
if [ "$DB_FAILS" -le 2 ] 2>/dev/null; then pass "DB auth (failures: $DB_FAILS)"; else fail "DB auth failures: $DB_FAILS"; fi

OOM=$(docker exec "$(docker compose ps -q tak-server)" grep -c "OutOfMemoryError" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
OOM="${OOM:-0}"
assert "$OOM" "0" "No OutOfMemoryError"

SEC_COUNT=$(docker exec "$(docker compose ps -q tak-server)" grep -c "Security status" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
SEC_COUNT="${SEC_COUNT:-0}"
if [ "$SEC_COUNT" -le 4 ] 2>/dev/null; then pass "Single start (status: $SEC_COUNT)"; else fail "Multiple starts ($SEC_COUNT)"; fi

# ═══════════════════════════════════════════════════════════════════════════
# TEARDOWN (test mode only)
# ═══════════════════════════════════════════════════════════════════════════

if $TEST; then
  log ""
  log "Teardown"
  log "────────"

  if docker compose down -v > /dev/null 2>&1; then pass "docker compose down"; else fail "docker compose down"; fi

  VOL_COUNT=$(docker volume ls --filter name=fasttak --format '{{.Name}}' | wc -l | tr -d ' ')
  assert "$VOL_COUNT" "0" "No orphaned volumes"

  rm -rf tak/ .env
fi

# ═══════════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════════

TOTAL=$((PASS + FAIL))

if $TEST; then
  echo ""
  echo "╔══════════════════════════════════════════╗"
  printf "║  %-40s║\n" "$PASS passed, $FAIL failed"
  echo "╚══════════════════════════════════════════╝"
  echo ""
  if [ $FAIL -eq 0 ]; then echo "All tests passed."; else echo "Some tests failed."; fi
  exit $FAIL
fi

# Normal mode
if [ $FAIL -eq 0 ]; then
  echo "  ✅ All checks passed ($PASS/$TOTAL)"
else
  echo "  ⚠️  $FAIL checks failed ($PASS/$TOTAL passed)"
fi

WA_PASS=$(env_get .env TAK_WEBADMIN_PASSWORD)
WA_MASKED="${WA_PASS:0:4}***"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       FastTAK is running                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  TAK Server:  https://${SERVER_ADDRESS}:${TAKSERVER_ADMIN_PORT}"
echo "               webadmin / ${WA_MASKED}"
if [ "$DEPLOY_MODE" = "direct" ]; then
  MONITOR_PORT_OUT=$(env_get .env MONITOR_PORT)
  MONITOR_PORT_OUT="${MONITOR_PORT_OUT:-8180}"
  echo "  Monitor:     https://${SERVER_ADDRESS}:${MONITOR_PORT_OUT}"
else
  MONITOR_SUB=$(env_get .env MONITOR_SUBDOMAIN)
  MONITOR_SUB="${MONITOR_SUB:-monitor}"
  echo "  Monitor:     https://${MONITOR_SUB}.${SERVER_ADDRESS}"
fi
echo ""
echo "  Passwords:   cat .env"
echo "  Stop:        docker compose down"
echo "  Reset DBs:   docker compose down -v"
echo ""

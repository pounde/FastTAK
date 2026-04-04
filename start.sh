#!/bin/bash
# start.sh — Start and verify FastTAK
# Usage:
#   ./start.sh                       Start the stack, run checks
#   ./start.sh --test <zip>          Greenfield: setup → start → verify → teardown

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

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

  DB_PASS=$(grep '^TAK_DB_PASSWORD=' .env | cut -d= -f2)
  LDAP_PASS=$(grep '^LDAP_BIND_PASSWORD=' .env | cut -d= -f2)
  assert_not "$DB_PASS" "" "TAK_DB_PASSWORD generated"
  assert_not "$LDAP_PASS" "" "LDAP_BIND_PASSWORD generated"

  TAK_VER=$(grep '^TAK_VERSION=' .env | cut -d= -f2)
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
  SERVER_ADDRESS=$(grep '^SERVER_ADDRESS=' .env | cut -d= -f2)
  if [ "$SERVER_ADDRESS" = "tak.example.com" ] || [ -z "$SERVER_ADDRESS" ]; then
    echo "ERROR: Set SERVER_ADDRESS in .env first. vim .env" >&2; exit 1
  fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# START
# ═══════════════════════════════════════════════════════════════════════════

SERVER_ADDRESS=$(grep '^SERVER_ADDRESS=' .env | cut -d= -f2)
DEPLOY_MODE=$(grep '^DEPLOY_MODE=' .env | cut -d= -f2)
DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"

# Set compose file based on deploy mode
if [ "$DEPLOY_MODE" = "direct" ]; then
  export COMPOSE_FILE="docker-compose.yml:docker-compose.direct.yml"
fi

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
docker compose up -d > /dev/null 2>&1

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

AK_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q authentik-server 2>/dev/null)" 2>/dev/null)
assert "$AK_STATUS" "healthy" "Authentik healthy"

LDAP_STATE=$(docker inspect --format='{{.State.Status}}' "$(docker compose ps -q authentik-ldap 2>/dev/null)" 2>/dev/null)
assert "$LDAP_STATE" "running" "LDAP outpost running"

PORTAL_STATE=$(docker inspect --format='{{.State.Status}}' "$(docker compose ps -q tak-portal 2>/dev/null)" 2>/dev/null)
assert "$PORTAL_STATE" "running" "TAK Portal running"

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
assert_file "tak/certs/files/svc_nodered.p12" "Node-RED service cert"
assert_file "tak/certs/files/ca-signing.jks" "CA signing keystore"
if ./certs.sh ca-info > /dev/null 2>&1; then pass "certs.sh ca-info"; else fail "certs.sh ca-info"; fi
if ./certs.sh list > /dev/null 2>&1; then pass "certs.sh list"; else fail "certs.sh list"; fi

log ""
log "Ports"
log "─────"

TAKSERVER_ADMIN_PORT=$(grep '^TAKSERVER_ADMIN_PORT=' .env | cut -d= -f2)
TAKSERVER_ADMIN_PORT="${TAKSERVER_ADMIN_PORT:-8446}"
MEDIAMTX_PORT=$(grep '^MEDIAMTX_PORT=' .env | cut -d= -f2)
MEDIAMTX_PORT="${MEDIAMTX_PORT:-8888}"
NODERED_PORT=$(grep '^NODERED_PORT=' .env | cut -d= -f2)
NODERED_PORT="${NODERED_PORT:-1880}"

assert_port 8089 "CoT TLS"
assert_port 8443 "Cert HTTPS"
assert_port "$TAKSERVER_ADMIN_PORT" "Admin HTTPS"
assert_port "$MEDIAMTX_PORT" "MediaMTX HLS"
assert_port 8554 "MediaMTX RTSP"
assert_port "$NODERED_PORT" "Node-RED"

HTTP_8446=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "https://localhost:${TAKSERVER_ADMIN_PORT}" 2>/dev/null)
assert_not "$HTTP_8446" "000" "${TAKSERVER_ADMIN_PORT} TLS (HTTP $HTTP_8446)"

log ""
log "Health"
log "──────"

JAVA_COUNT=$(docker exec "$(docker compose ps -q tak-server)" sh -c "ps aux | grep java | grep -v grep | wc -l" 2>/dev/null | tr -d ' ')
assert "$JAVA_COUNT" "5" "Java processes: $JAVA_COUNT/5"

DB_FAILS=$(docker exec "$(docker compose ps -q tak-server)" grep -c "password authentication failed" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
DB_FAILS="${DB_FAILS:-0}"
if [ "$DB_FAILS" -le 2 ] 2>/dev/null; then pass "DB auth (failures: $DB_FAILS)"; else fail "DB auth failures: $DB_FAILS"; fi

OOM=$(docker exec "$(docker compose ps -q tak-server)" grep -c "OutOfMemoryError" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
OOM="${OOM:-0}"
assert "$OOM" "0" "No OutOfMemoryError"

SEC_COUNT=$(docker exec "$(docker compose ps -q tak-server)" grep -c "Security status" /opt/tak/logs/takserver.log 2>/dev/null | tr -d '[:space:]')
SEC_COUNT="${SEC_COUNT:-0}"
if [ "$SEC_COUNT" -le 4 ] 2>/dev/null; then pass "Single start (status: $SEC_COUNT)"; else fail "Multiple starts ($SEC_COUNT)"; fi

log ""
log "Portal"
log "──────"

assert_file "tak/portal/settings.json" "Portal settings.json"
assert_file "tak/portal/certs/tak-ca.pem" "Portal CA cert"
assert_file "tak/portal/certs/svc_fasttakapi.p12" "Portal API service cert"

PORTAL_HTTP="000"
for _ in $(seq 1 12); do
  PORTAL_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:3000 2>/dev/null)
  if [ "$PORTAL_HTTP" != "000" ]; then break; fi
  sleep 5
done
assert_not "$PORTAL_HTTP" "000" "Portal HTTP ($PORTAL_HTTP)"

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

WA_PASS=$(grep '^TAK_WEBADMIN_PASSWORD=' .env | cut -d= -f2)
WA_MASKED="${WA_PASS:0:4}***"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       FastTAK is running                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  TAK Server:  https://${SERVER_ADDRESS}:${TAKSERVER_ADMIN_PORT}"
echo "               webadmin / ${WA_MASKED}"
if [ "$DEPLOY_MODE" = "direct" ]; then
  echo "  TAK Portal:  https://${SERVER_ADDRESS}"
else
  PORTAL_SUB=$(grep '^TAKPORTAL_SUBDOMAIN=' .env | cut -d= -f2)
  PORTAL_SUB="${PORTAL_SUB:-portal}"
  echo "  TAK Portal:  https://${PORTAL_SUB}.${SERVER_ADDRESS}"
fi
echo ""
echo "  Passwords:   cat .env"
echo "  Stop:        docker compose down"
echo "  Reset DBs:   docker compose down -v"
echo ""

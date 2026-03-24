#!/usr/bin/env bash
# test-stack.sh — Stand up the FastTAK stack, validate APIs, tear down.
#
# Isolated: uses a unique project name and temp .env so it won't
# interfere with a running stack or modify the working directory.
#
# Usage: ./tests-integration/test-stack.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TS=$(date +%s)
PROJECT="fastak-test-${TS}"
TMPDIR="/tmp/${PROJECT}"
COMPOSE="docker compose -p ${PROJECT} -f ${REPO_DIR}/docker-compose.yml --env-file ${TMPDIR}/.env"
TIMEOUT=300  # 5 minutes max for healthchecks
FAILURES=0

# ── Cleanup on exit ────────────────────────────────────────────────────
# shellcheck disable=SC2317,SC2329  # invoked via trap
cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    ${COMPOSE} down -v --remove-orphans 2>/dev/null || true
    rm -rf "${TMPDIR}"
}
trap cleanup EXIT INT TERM

# ── Generate test .env ─────────────────────────────────────────────────
echo "=== Generating test environment ==="
mkdir -p "${TMPDIR}"
cp "${REPO_DIR}/.env.example" "${TMPDIR}/.env"

# Fill in required secrets with random values
sed -i.bak \
    -e "s/^TAK_DB_PASSWORD=.*/TAK_DB_PASSWORD=$(openssl rand -hex 16)/" \
    -e "s/^AUTHENTIK_SECRET_KEY=.*/AUTHENTIK_SECRET_KEY=$(openssl rand -hex 32)/" \
    -e "s/^APP_DB_PASSWORD=.*/APP_DB_PASSWORD=$(openssl rand -hex 16)/" \
    -e "s/^AUTHENTIK_API_TOKEN=.*/AUTHENTIK_API_TOKEN=$(openssl rand -hex 32)/" \
    -e "s/^LDAP_BIND_PASSWORD=.*/LDAP_BIND_PASSWORD=$(openssl rand -hex 16)/" \
    -e "s/^FQDN=.*/FQDN=test.fastak.local/" \
    "${TMPDIR}/.env"
rm -f "${TMPDIR}/.env.bak"

# ── Build and start ────────────────────────────────────────────────────
echo "=== Starting stack (project: ${PROJECT}) ==="
${COMPOSE} up -d --build

# ── Wait for healthchecks ──────────────────────────────────────────────
echo "=== Waiting for services to become healthy (timeout: ${TIMEOUT}s) ==="
ELAPSED=0
INTERVAL=10
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    # Check if all services with healthchecks are healthy
    UNHEALTHY=$(${COMPOSE} ps --format json 2>/dev/null | \
        python3 -c "
import sys, json
unhealthy = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    svc = json.loads(line)
    health = svc.get('Health', '')
    status = svc.get('State', '')
    name = svc.get('Service', '')
    # Skip init containers (exited 0 is success)
    if status == 'exited' and svc.get('ExitCode', 1) == 0:
        continue
    # Skip services without healthchecks that are running
    if health == '' and status == 'running':
        continue
    if health not in ('healthy', ''):
        unhealthy.append(f'{name}({health})')
    elif status not in ('running', 'exited'):
        unhealthy.append(f'{name}({status})')
print(','.join(unhealthy))
" 2>/dev/null || echo "parse-error")

    if [ -z "$UNHEALTHY" ]; then
        echo "All services healthy after ${ELAPSED}s"
        break
    fi
    echo "  Waiting... (${ELAPSED}s) unhealthy: ${UNHEALTHY}"
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
    echo "FAIL: Timed out waiting for services"
    ${COMPOSE} ps
    exit 1
fi

# ── API assertions ─────────────────────────────────────────────────────
assert_endpoint() {
    local path="$1"
    local check="$2"  # python expression that should print "true"
    local desc="$3"

    local resp
    resp=$(${COMPOSE} exec -T monitor curl -sf "http://localhost:8080${path}" 2>/dev/null) || {
        echo "FAIL: ${desc} — curl failed"
        FAILURES=$((FAILURES + 1))
        return
    }

    local ok
    ok=$(echo "$resp" | python3 -c "
import sys, json
data = json.load(sys.stdin)
${check}
" 2>/dev/null) || ok="false"

    if [ "$ok" = "true" ]; then
        echo "PASS: ${desc}"
    else
        echo "FAIL: ${desc}"
        echo "  Response: ${resp:0:200}"
        FAILURES=$((FAILURES + 1))
    fi
}

echo ""
echo "=== Testing API endpoints ==="

assert_endpoint "/api/ping" \
    "print('true' if data.get('status') == 'ok' else 'false')" \
    "GET /api/ping returns ok"

assert_endpoint "/api/health/containers" \
    "print('true' if isinstance(data, list) and len(data) > 0 else 'false')" \
    "GET /api/health/containers returns non-empty list"

assert_endpoint "/api/health/resources" \
    "print('true' if isinstance(data, list) else 'false')" \
    "GET /api/health/resources returns list"

assert_endpoint "/api/health/certs" \
    "print('true' if isinstance(data, list) else 'false')" \
    "GET /api/health/certs returns list"

assert_endpoint "/api/health/database" \
    "print('true' if 'size_bytes' in data or 'error' in data else 'false')" \
    "GET /api/health/database returns size or error"

assert_endpoint "/api/health/disk" \
    "print('true' if isinstance(data, list) else 'false')" \
    "GET /api/health/disk returns list"

assert_endpoint "/api/health/config" \
    "print('true' if data.get('status') in ('ok', 'unavailable') else 'false')" \
    "GET /api/health/config returns status"

# Test log retrieval for a known service
assert_endpoint "/api/ops/service/tak-server/logs?tail=10" \
    "print('true' if 'logs' in data and len(data['logs']) > 0 else 'false')" \
    "GET /api/ops/service/tak-server/logs returns logs"

# ── Summary ────────────────────────────────────────────────────────────
echo ""
if [ "$FAILURES" -eq 0 ]; then
    echo "=== ALL TESTS PASSED ==="
    exit 0
else
    echo "=== ${FAILURES} TEST(S) FAILED ==="
    exit 1
fi

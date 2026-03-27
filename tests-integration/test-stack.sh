#!/usr/bin/env bash
# test-stack.sh — Stand up the FastTAK stack, validate APIs, tear down.
#
# Fully isolated: runs setup.sh -d to extract tak/ from the ZIP into a temp
# directory, uses a unique project name and generated .env. Won't interfere
# with a running stack or modify the working directory.
#
# Usage: ./tests-integration/test-stack.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TS=$(date +%s)
PROJECT="fastak-test-${TS}"
TEST_DIR="/tmp/${PROJECT}"
TIMEOUT=300  # 5 minutes max for healthchecks
INTERVAL=10
FAILURES=0

# Isolate tak/ directory so tests don't depend on or pollute host state.
# See TAK_HOST_PATH in docker-compose.yml for details.
export TAK_HOST_PATH="${TEST_DIR}/tak"

COMPOSE="docker compose -p ${PROJECT} -f ${REPO_DIR}/docker-compose.yml --env-file ${TEST_DIR}/.env"

# ── Cleanup on exit ────────────────────────────────────────────────────
# shellcheck disable=SC2317,SC2329  # invoked via trap
cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    ${COMPOSE} down -v --remove-orphans 2>/dev/null || true
    rm -rf "${TEST_DIR}"
}
trap cleanup EXIT INT TERM

# ── Helper: wait for all services to become healthy ───────────────────
wait_healthy() {
    local label="${1:-services}"
    echo "  Waiting for ${label} to become healthy (timeout: ${TIMEOUT}s)..."
    local elapsed=0
    while [ "$elapsed" -lt "$TIMEOUT" ]; do
        local unhealthy
        unhealthy=$(${COMPOSE} ps --format json 2>/dev/null | \
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

        if [ -z "$unhealthy" ]; then
            echo "  All ${label} healthy after ${elapsed}s"
            return 0
        fi
        echo "  Waiting... (${elapsed}s) unhealthy: ${unhealthy}"
        sleep "$INTERVAL"
        elapsed=$((elapsed + INTERVAL))
    done

    echo "FAIL: Timed out waiting for ${label}"
    ${COMPOSE} ps
    return 1
}

# ── Run setup.sh to extract tak/ and generate .env in isolated dir ────
echo "=== Running setup.sh into isolated directory ==="
ZIP=$(find "${REPO_DIR}" -maxdepth 1 -name 'takserver-docker-*.zip' | head -1)
if [ -z "${ZIP}" ]; then
    echo "FAIL: No takserver-docker-*.zip found in ${REPO_DIR}"
    echo "  Run setup.sh first or place the ZIP in the repo root."
    exit 1
fi
"${REPO_DIR}/setup.sh" -d "${TEST_DIR}" "${ZIP}"

# Override FQDN for test environment
sed -i.bak "s/^FQDN=.*/FQDN=test.fastak.local/" "${TEST_DIR}/.env"
rm -f "${TEST_DIR}/.env.bak"

# ── Build and start ────────────────────────────────────────────────────
echo "=== Starting stack (project: ${PROJECT}) ==="
${COMPOSE} up -d --build

# ── Wait for healthchecks ──────────────────────────────────────────────
echo "=== Waiting for services to become healthy ==="
if ! wait_healthy "services"; then
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

# ── Service account cert registration ────────────────────────────────
echo ""
echo "=== Testing service account cert registration ==="

# Wait for register-api-cert.sh to complete (it runs in the background after TAK Server starts).
# TAK Server has a 240s start_period — it takes several minutes to fully boot, so we must
# wait long enough for the "Server started" log line + certmod to run.
echo "  Waiting for cert registration (up to 300s)..."
REG_WAITED=0
while [ $REG_WAITED -lt 300 ]; do
  CERTMOD_OUT=$(${COMPOSE} exec -T tak-server \
    java -jar /opt/tak/utils/UserManager.jar certmod -s /opt/tak/certs/files/svc_fasttakapi.pem 2>&1) || true
  if echo "${CERTMOD_OUT}" | grep -q "ROLE_ADMIN"; then
    echo "  Cert registered after ${REG_WAITED}s"
    break
  fi
  if [ $((REG_WAITED % 60)) -eq 0 ] && [ $REG_WAITED -gt 0 ]; then
    echo "  Still waiting... (${REG_WAITED}s)"
  fi
  sleep 10
  REG_WAITED=$((REG_WAITED + 10))
done
if [ $REG_WAITED -ge 300 ]; then
  echo "  WARNING: cert registration timed out after 300s"
  echo "  Last certmod output: ${CERTMOD_OUT:0:200}"
fi

# Test: svc_fasttakapi cert can call TAK Server API.
# Use host curl against the published port (8443) with the cert from the isolated tak/ directory.
CERT_P12="${TAK_HOST_PATH}/certs/files/svc_fasttakapi.p12"
if [ ! -f "${CERT_P12}" ]; then
  echo "  FAIL: svc_fasttakapi.p12 not found at ${CERT_P12}"
  FAILURES=$((FAILURES + 1))
  HTTP_CODE="missing"
else
  HTTP_CODE=$(curl -sk --cert-type P12 \
    --cert "${CERT_P12}":atakatak \
    "https://localhost:8443/Marti/api/plugins/info/all" \
    -w "%{http_code}" -o /dev/null 2>&1) || true
  # Strip to last 3 chars (the HTTP code) in case of extra output
  HTTP_CODE="${HTTP_CODE: -3}"
fi
if [ "${HTTP_CODE}" = "200" ]; then
  echo "  PASS: svc_fasttakapi cert gets 200 from TAK Server API"
else
  echo "  FAIL: svc_fasttakapi cert got HTTP ${HTTP_CODE} (expected 200)"
  FAILURES=$((FAILURES + 1))
fi

# ── Passwordless users cannot authenticate via LDAP ──────────────────
echo ""
echo "=== Testing passwordless users cannot authenticate ==="

# Create a temporary user with no password, get the pk for cleanup
AUTHENTIK_TOKEN=$(grep AUTHENTIK_API_TOKEN "${TEST_DIR}/.env" | cut -d= -f2)
TEST_USER_PK=$(${COMPOSE} exec -T monitor python3 -c "
import urllib.request, json
data = json.dumps({'username':'test_nopassword','name':'Test','is_active':True,'path':'users'}).encode()
req = urllib.request.Request('http://authentik-server:9000/api/v3/core/users/',
    data=data, headers={'Authorization': 'Bearer ${AUTHENTIK_TOKEN}', 'Content-Type': 'application/json'})
resp = json.loads(urllib.request.urlopen(req).read())
print(resp['pk'])
" 2>/dev/null) || TEST_USER_PK=""

# Attempt LDAP bind with empty password — should fail
BIND_RESULT=$(${COMPOSE} exec -T monitor python3 -c "
import socket
s = socket.socket()
s.settimeout(5)
s.connect(('authentik-ldap', 3389))
dn = b'cn=test_nopassword,ou=users,dc=takldap'
pw = b''
version = b'\x02\x01\x03'
name = bytes([0x04, len(dn)]) + dn
auth = bytes([0x80, len(pw)]) + pw
bind_req = version + name + auth
bind_app = bytes([0x60, len(bind_req)]) + bind_req
msg_id = b'\x02\x01\x01'
msg = msg_id + bind_app
envelope = bytes([0x30, len(msg)]) + msg
s.send(envelope)
resp = s.recv(1024)
s.close()
for i in range(len(resp)):
    if resp[i] == 0x61:
        j = i + 2
        if resp[j] in (0x0a, 0x02):
            print(resp[j+2])
            break
" 2>/dev/null || echo "error")

if [ "${BIND_RESULT}" = "49" ]; then
  echo "  PASS: Passwordless user correctly rejected by LDAP (code 49)"
else
  echo "  FAIL: Passwordless user LDAP bind returned ${BIND_RESULT} (expected 49)"
  FAILURES=$((FAILURES + 1))
fi

# Clean up test user by pk
if [ -n "${TEST_USER_PK}" ]; then
  ${COMPOSE} exec -T monitor python3 -c "
import urllib.request
req = urllib.request.Request('http://authentik-server:9000/api/v3/core/users/${TEST_USER_PK}/',
    method='DELETE', headers={'Authorization': 'Bearer ${AUTHENTIK_TOKEN}'})
urllib.request.urlopen(req)
" 2>/dev/null || true
fi

# ── User Management API tests ─────────────────────────────────────────
echo ""
echo "=== Testing User Management API ==="

# List users
assert_endpoint "/api/users" \
    "print('true' if data.get('count', -1) >= 0 else 'false')" \
    "GET /api/users returns paginated list"

# Search users
assert_endpoint "/api/users?search=webadmin" \
    "print('true' if data.get('count', 0) >= 1 else 'false')" \
    "GET /api/users?search=webadmin finds the bootstrapped user"

# Hidden prefix users should not appear
assert_endpoint "/api/users?search=svc_" \
    "print('true' if data.get('count', -1) == 0 else 'false')" \
    "Hidden prefix users excluded from search results"

# Create a test user via the API
CREATE_RESP=$(${COMPOSE} exec -T monitor curl -sf \
    -X POST "http://localhost:8080/api/users" \
    -H "Content-Type: application/json" \
    -d '{"username":"test_integ_user","name":"Integration Test User"}' 2>/dev/null) || CREATE_RESP=""

if echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('username')=='test_integ_user' else 'false')" 2>/dev/null | grep -q "true"; then
    echo "PASS: POST /api/users creates user"
    TEST_USER_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

    # Get user detail
    assert_endpoint "/api/users/${TEST_USER_ID}" \
        "print('true' if data.get('username') == 'test_integ_user' else 'false')" \
        "GET /api/users/{id} returns user detail"

    # Set password
    PW_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${TEST_USER_ID}/password" \
        -H "Content-Type: application/json" \
        -d '{"password":"TestPass123!"}' 2>/dev/null) || PW_RESP=""
    if echo "$PW_RESP" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: POST /api/users/{id}/password sets password"
    else
        echo "FAIL: POST /api/users/{id}/password"
        FAILURES=$((FAILURES + 1))
    fi

    # Generate enrollment URL
    ENROLL_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${TEST_USER_ID}/enroll" 2>/dev/null) || ENROLL_RESP=""
    if echo "$ENROLL_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if 'tak://' in d.get('enrollment_url','') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: POST /api/users/{id}/enroll returns tak:// URL"
    else
        echo "FAIL: POST /api/users/{id}/enroll"
        echo "  Response: ${ENROLL_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Re-enroll — should return same token
    ENROLL_RESP2=$(${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${TEST_USER_ID}/enroll" 2>/dev/null) || ENROLL_RESP2=""
    URL1=$(echo "$ENROLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('enrollment_url',''))" 2>/dev/null)
    URL2=$(echo "$ENROLL_RESP2" | python3 -c "import sys,json; print(json.load(sys.stdin).get('enrollment_url',''))" 2>/dev/null)
    if [ -n "$URL1" ] && [ "$URL1" = "$URL2" ]; then
        echo "PASS: Re-enrollment returns same token"
    else
        echo "FAIL: Re-enrollment returned different token"
        FAILURES=$((FAILURES + 1))
    fi

    # Deactivate user
    DEL_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X DELETE "http://localhost:8080/api/users/${TEST_USER_ID}" 2>/dev/null) || DEL_RESP=""
    if echo "$DEL_RESP" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: DELETE /api/users/{id} deactivates user"
    else
        echo "FAIL: DELETE /api/users/{id}"
        FAILURES=$((FAILURES + 1))
    fi

    # Enrollment should fail for deactivated user
    ENROLL_FAIL=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w "%{http_code}" \
        -X POST "http://localhost:8080/api/users/${TEST_USER_ID}/enroll" 2>/dev/null) || ENROLL_FAIL=""
    if [ "${ENROLL_FAIL}" = "400" ]; then
        echo "PASS: Enrollment rejected for deactivated user (400)"
    else
        echo "FAIL: Enrollment for deactivated user returned ${ENROLL_FAIL} (expected 400)"
        FAILURES=$((FAILURES + 1))
    fi

    # Reactivate user
    REACT_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X PATCH "http://localhost:8080/api/users/${TEST_USER_ID}" \
        -H "Content-Type: application/json" \
        -d '{"is_active":true}' 2>/dev/null) || REACT_RESP=""
    if echo "$REACT_RESP" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('is_active') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: PATCH /api/users/{id} reactivates user"

        # Re-enroll after reactivation — should get a new enrollment URL
        ENROLL_RESP3=$(${COMPOSE} exec -T monitor curl -sf \
            -X POST "http://localhost:8080/api/users/${TEST_USER_ID}/enroll" 2>/dev/null) || ENROLL_RESP3=""
        if echo "$ENROLL_RESP3" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if 'tak://' in d.get('enrollment_url','') else 'false')" 2>/dev/null | grep -q "true"; then
            echo "PASS: Re-enrollment after reactivation returns tak:// URL"
        else
            echo "FAIL: Re-enrollment after reactivation"
            echo "  Response: ${ENROLL_RESP3:0:200}"
            FAILURES=$((FAILURES + 1))
        fi
    else
        echo "FAIL: PATCH /api/users/{id} reactivation"
        echo "  Response: ${REACT_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi
else
    echo "FAIL: POST /api/users — could not create test user"
    echo "  Response: ${CREATE_RESP:0:200}"
    FAILURES=$((FAILURES + 1))
fi

# Groups
assert_endpoint "/api/groups" \
    "print('true' if isinstance(data, list) else 'false')" \
    "GET /api/groups returns list"

# Note: TTL expiry test intentionally omitted — requires waiting for the scheduler
# to fire (configurable interval, default 60s) which is too slow for this test.
# TTL enforcement is thoroughly covered by unit tests in tests/unit/test_ttl_task.py.

# ── Idempotency test: restart and re-validate ─────────────────────────
echo ""
echo "=== Testing idempotency (restart stack) ==="
${COMPOSE} down
${COMPOSE} up -d

if ! wait_healthy "services after restart"; then
    FAILURES=$((FAILURES + 1))
else
    echo "PASS: Stack restarts cleanly"

    # Re-run basic health checks after restart
    assert_endpoint "/api/ping" \
        "print('true' if data.get('status') == 'ok' else 'false')" \
        "GET /api/ping returns ok after restart"

    assert_endpoint "/api/health/containers" \
        "print('true' if isinstance(data, list) and len(data) > 0 else 'false')" \
        "GET /api/health/containers healthy after restart"

    assert_endpoint "/api/users" \
        "print('true' if data.get('count', -1) >= 0 else 'false')" \
        "GET /api/users works after restart"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
if [ "$FAILURES" -eq 0 ]; then
    echo "=== ALL TESTS PASSED ==="
    exit 0
else
    echo "=== ${FAILURES} TEST(S) FAILED ==="
    exit 1
fi

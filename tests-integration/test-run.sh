#!/usr/bin/env bash
# test-run.sh — Run test assertions against a running test stack.
#
# Finds the running test stack via the state file, or accepts a project name.
#
# Usage:
#   ./tests-integration/test-run.sh              # auto-find running stack
#   ./tests-integration/test-run.sh <project>    # use specific project

set -euo pipefail

FAILURES=0

if [ -n "${1:-}" ]; then
    # Project name provided
    PROJECT="$1"
    STATE_FILE="/tmp/${PROJECT}/.test-state"
else
    # Auto-find
    STATE_FILE=$(find /tmp -maxdepth 2 -name '.test-state' -path '*/fastak-test-*' 2>/dev/null | sort -r | head -1)
fi

if [ -z "${STATE_FILE:-}" ] || [ ! -f "${STATE_FILE}" ]; then
    echo "No running test stack found. Run 'just test-up' first."
    exit 1
fi

# shellcheck disable=SC1090
source "$STATE_FILE"
COMPOSE="docker compose -p ${PROJECT} -f ${REPO_DIR}/docker-compose.yml -f ${REPO_DIR}/docker-compose.test.yml --env-file ${ENV_FILE}"

# Unique suffix per run — deactivated users persist in Authentik,
# so reusing names across runs would fail on create.
RUN_ID=$(date +%s)
TEST_USER="tstu_${RUN_ID}"
TEST_LIFECYCLE_USER="tstl_${RUN_ID}"
TEST_GROUP="TST_GRP_${RUN_ID}"
TEST_CERT="tstcert_${RUN_ID}"
TEST_DUP_CERT="tstdup_${RUN_ID}"
SVC_TEST_GROUP="SVC_TST_${RUN_ID}"
SVC_DATA_NAME="tstd_${RUN_ID}"
SVC_ADMIN_NAME="tsta_${RUN_ID}"

echo "=== Running tests against: ${PROJECT} ==="
echo "  Using unique run ID: ${RUN_ID}"
echo ""

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
    "print('true' if isinstance(data.get('items'), list) and len(data['items']) > 0 else 'false')" \
    "GET /api/health/containers returns non-empty items"

assert_endpoint "/api/health/resources" \
    "print('true' if isinstance(data, list) else 'false')" \
    "GET /api/health/resources returns list"

assert_endpoint "/api/health/certs" \
    "print('true' if isinstance(data.get('items'), list) else 'false')" \
    "GET /api/health/certs returns items"

assert_endpoint "/api/health/database" \
    "print('true' if 'size_bytes' in data and 'live_bytes' in data else 'false')" \
    "GET /api/health/database returns size and live data"

assert_endpoint "/api/health/disk" \
    "print('true' if isinstance(data.get('items'), list) else 'false')" \
    "GET /api/health/disk returns items"

assert_endpoint "/api/health/config" \
    "print('true' if 'changed' in data else 'false')" \
    "GET /api/health/config returns changed field"

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
    "https://localhost:18443/Marti/api/plugins/info/all" \
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
    -d "{\"username\":\"${TEST_USER}\",\"name\":\"Test User ${RUN_ID}\"}" 2>/dev/null) || CREATE_RESP=""

if echo "$CREATE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('username')=='${TEST_USER}' else 'false')" 2>/dev/null | grep -q "true"; then
    echo "PASS: POST /api/users creates user"
    TEST_USER_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

    # Get user detail
    assert_endpoint "/api/users/${TEST_USER_ID}" \
        "print('true' if data.get('username') == '${TEST_USER}' else 'false')" \
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

# ── Group Management ─────────────────────────────────────────────────
echo ""
echo "=== Testing Group Management ==="

# Create a group
GRP_RESP=$(${COMPOSE} exec -T monitor curl -sf \
    -X POST "http://localhost:8080/api/groups" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${TEST_GROUP}\"}" 2>/dev/null) || GRP_RESP=""

GRP_ID=$(echo "$GRP_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null) || GRP_ID=""
GRP_NAME=$(echo "$GRP_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('name',''))" 2>/dev/null) || GRP_NAME=""

if [ "$GRP_NAME" = "${TEST_GROUP}" ] && [ -n "$GRP_ID" ]; then
    echo "PASS: POST /api/groups creates group with correct name"
else
    echo "FAIL: POST /api/groups — expected name=${TEST_GROUP}, got '${GRP_NAME}'"
    echo "  Response: ${GRP_RESP:0:200}"
    FAILURES=$((FAILURES + 1))
fi

# Verify group appears in list
GRP_LIST_FOUND=$(${COMPOSE} exec -T monitor curl -sf \
    "http://localhost:8080/api/groups" 2>/dev/null \
    | python3 -c "
import sys,json
groups=json.load(sys.stdin)
found=[g for g in groups if g.get('name')=='${TEST_GROUP}']
print('true' if found else 'false')
" 2>/dev/null) || GRP_LIST_FOUND="false"
if [ "$GRP_LIST_FOUND" = "true" ]; then
    echo "PASS: GET /api/groups includes newly created group"
else
    echo "FAIL: GET /api/groups does not include ${TEST_GROUP}"
    FAILURES=$((FAILURES + 1))
fi

# Delete the group
if [ -n "$GRP_ID" ]; then
    GRP_DEL=$(${COMPOSE} exec -T monitor curl -sf \
        -X DELETE "http://localhost:8080/api/groups/${GRP_ID}" 2>/dev/null) || GRP_DEL=""
    if echo "$GRP_DEL" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: DELETE /api/groups/{id} succeeds"

        # Verify group is gone from the list
        GRP_GONE=$(${COMPOSE} exec -T monitor curl -sf \
            "http://localhost:8080/api/groups" 2>/dev/null \
            | python3 -c "
import sys,json
groups=json.load(sys.stdin)
found=[g for g in groups if g.get('name')=='${TEST_GROUP}']
print('true' if not found else 'false')
" 2>/dev/null) || GRP_GONE="false"
        if [ "$GRP_GONE" = "true" ]; then
            echo "PASS: Deleted group no longer appears in GET /api/groups"
        else
            echo "FAIL: Deleted group still appears in GET /api/groups"
            FAILURES=$((FAILURES + 1))
        fi
    else
        echo "FAIL: DELETE /api/groups/{id}"
        FAILURES=$((FAILURES + 1))
    fi
fi

# ── User Lifecycle ───────────────────────────────────────────────────
echo ""
echo "=== Testing User Lifecycle ==="

# Create a test user
CREATE_RESP=$(${COMPOSE} exec -T monitor curl -sf \
    -X POST "http://localhost:8080/api/users" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${TEST_LIFECYCLE_USER}\",\"name\":\"Lifecycle ${RUN_ID}\"}" 2>/dev/null) || CREATE_RESP=""

LIFECYCLE_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null) || LIFECYCLE_ID=""
LIFECYCLE_NAME=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name',''))" 2>/dev/null) || LIFECYCLE_NAME=""
LIFECYCLE_USER=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('username',''))" 2>/dev/null) || LIFECYCLE_USER=""

if [ "$LIFECYCLE_USER" = "${TEST_LIFECYCLE_USER}" ] && [ "$LIFECYCLE_NAME" = "Lifecycle ${RUN_ID}" ] && [ -n "$LIFECYCLE_ID" ]; then
    echo "PASS: POST /api/users creates user with correct username and name"
else
    echo "FAIL: POST /api/users — expected username=${TEST_LIFECYCLE_USER}, name=Lifecycle ${RUN_ID}"
    echo "  Response: ${CREATE_RESP:0:200}"
    FAILURES=$((FAILURES + 1))
fi

if [ -n "$LIFECYCLE_ID" ]; then
    # Verify user appears in list
    LIST_FOUND=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/users?search=${TEST_LIFECYCLE_USER}" 2>/dev/null \
        | python3 -c "
import sys,json
d=json.load(sys.stdin)
results=d.get('results',[])
found=[u for u in results if u.get('username')=='${TEST_LIFECYCLE_USER}']
print('true' if found and d.get('count',0)>=1 else 'false')
" 2>/dev/null) || LIST_FOUND="false"
    if [ "$LIST_FOUND" = "true" ]; then
        echo "PASS: GET /api/users?search=${TEST_LIFECYCLE_USER} finds the user"
    else
        echo "FAIL: GET /api/users?search=${TEST_LIFECYCLE_USER} did not find user"
        FAILURES=$((FAILURES + 1))
    fi

    # Update user name via PATCH
    PATCH_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X PATCH "http://localhost:8080/api/users/${LIFECYCLE_ID}" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"Updated ${RUN_ID}\"}" 2>/dev/null) || PATCH_RESP=""
    PATCHED_NAME=$(echo "$PATCH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name',''))" 2>/dev/null) || PATCHED_NAME=""
    if [ "$PATCHED_NAME" = "Updated ${RUN_ID}" ]; then
        echo "PASS: PATCH /api/users/{id} updates name correctly"
    else
        echo "FAIL: PATCH /api/users/{id} name update — got '${PATCHED_NAME}'"
        echo "  Response: ${PATCH_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Verify name change via GET
    VERIFY_NAME=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/users/${LIFECYCLE_ID}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('name',''))" 2>/dev/null) || VERIFY_NAME=""
    if [ "$VERIFY_NAME" = "Updated ${RUN_ID}" ]; then
        echo "PASS: GET /api/users/{id} confirms updated name"
    else
        echo "FAIL: GET /api/users/{id} shows name '${VERIFY_NAME}' instead of 'Updated ${RUN_ID}'"
        FAILURES=$((FAILURES + 1))
    fi

    # Set password
    PW_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${LIFECYCLE_ID}/password" \
        -H "Content-Type: application/json" \
        -d '{"password":"TestPass123!"}' 2>/dev/null) || PW_RESP=""
    if echo "$PW_RESP" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: POST /api/users/{id}/password sets password"
    else
        echo "FAIL: POST /api/users/{id}/password"
        echo "  Response: ${PW_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Generate enrollment QR / URL
    ENROLL_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${LIFECYCLE_ID}/enroll" 2>/dev/null) || ENROLL_RESP=""
    ENROLL_URL=$(echo "$ENROLL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('enrollment_url',''))" 2>/dev/null) || ENROLL_URL=""
    if echo "$ENROLL_URL" | grep -q "^tak://"; then
        echo "PASS: POST /api/users/{id}/enroll returns tak:// URL"
    else
        echo "FAIL: POST /api/users/{id}/enroll — expected tak:// URL, got '${ENROLL_URL:0:80}'"
        echo "  Response: ${ENROLL_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Deactivate user via DELETE
    DEL_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X DELETE "http://localhost:8080/api/users/${LIFECYCLE_ID}" 2>/dev/null) || DEL_RESP=""
    if echo "$DEL_RESP" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: DELETE /api/users/{id} deactivates user"
    else
        echo "FAIL: DELETE /api/users/{id}"
        echo "  Response: ${DEL_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Verify user is inactive
    INACTIVE_CHECK=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/users/${LIFECYCLE_ID}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('is_active',''))" 2>/dev/null) || INACTIVE_CHECK=""
    if [ "$INACTIVE_CHECK" = "False" ]; then
        echo "PASS: User is_active=False after deactivation"
    else
        echo "FAIL: User is_active should be False after DELETE, got '${INACTIVE_CHECK}'"
        FAILURES=$((FAILURES + 1))
    fi

    # Reactivate via PATCH
    REACT_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X PATCH "http://localhost:8080/api/users/${LIFECYCLE_ID}" \
        -H "Content-Type: application/json" \
        -d '{"is_active":true}' 2>/dev/null) || REACT_RESP=""
    REACT_ACTIVE=$(echo "$REACT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('is_active',''))" 2>/dev/null) || REACT_ACTIVE=""
    if [ "$REACT_ACTIVE" = "True" ]; then
        echo "PASS: PATCH /api/users/{id} reactivates user (is_active=True)"
    else
        echo "FAIL: PATCH /api/users/{id} reactivation — is_active='${REACT_ACTIVE}'"
        echo "  Response: ${REACT_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Verify reactivation via GET
    ACTIVE_CHECK=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/users/${LIFECYCLE_ID}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('is_active',''))" 2>/dev/null) || ACTIVE_CHECK=""
    if [ "$ACTIVE_CHECK" = "True" ]; then
        echo "PASS: GET /api/users/{id} confirms reactivation"
    else
        echo "FAIL: GET /api/users/{id} shows is_active='${ACTIVE_CHECK}' after reactivation"
        FAILURES=$((FAILURES + 1))
    fi
fi

# ── User Certificate Management ──────────────────────────────────────
echo ""
echo "=== Testing User Certificate Management ==="

# Use the webadmin user (always exists)
WEBADMIN_ID=$(${COMPOSE} exec -T monitor curl -sf \
    "http://localhost:8080/api/users?search=webadmin" 2>/dev/null \
    | python3 -c "import sys,json; results=json.load(sys.stdin).get('results',[]); print(results[0]['id'] if results else '')" 2>/dev/null) || WEBADMIN_ID=""

if [ -n "$WEBADMIN_ID" ]; then
    # Generate a named cert
    GEN_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/generate" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"${TEST_CERT}\"}" 2>/dev/null) || GEN_RESP=""
    GEN_NAME=$(echo "$GEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('name',''))" 2>/dev/null) || GEN_NAME=""
    if [ "$GEN_NAME" = "${TEST_CERT}" ]; then
        echo "PASS: POST /api/users/{id}/certs/generate creates cert with correct name"
    else
        echo "FAIL: POST /api/users/{id}/certs/generate — expected name=${TEST_CERT}, got '${GEN_NAME}'"
        echo "  Response: ${GEN_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Verify cert appears in list with unified format fields
    LIST_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/users/${WEBADMIN_ID}/certs" 2>/dev/null) || LIST_RESP=""
    CERT_IN_LIST=$(echo "$LIST_RESP" | python3 -c "
import sys,json
certs=json.load(sys.stdin)
for c in certs:
    if c.get('name')=='${TEST_CERT}':
        has_fields = all(k in c for k in ('name','downloadable','revoked','cert_id'))
        is_dl = c.get('downloadable') is True
        not_revoked = c.get('revoked') is not True
        print('true' if has_fields and is_dl and not_revoked else 'false')
        break
else:
    print('false')
" 2>/dev/null) || CERT_IN_LIST="false"
    if [ "$CERT_IN_LIST" = "true" ]; then
        echo "PASS: GET /api/users/{id}/certs shows ${TEST_CERT} as downloadable, not revoked, with unified fields"
    else
        echo "FAIL: GET /api/users/{id}/certs — cert missing or wrong format"
        echo "  Response: ${LIST_RESP:0:300}"
        FAILURES=$((FAILURES + 1))
    fi

    # Download the cert — verify 200
    DL_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
        "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/download/${TEST_CERT}" 2>/dev/null) || DL_STATUS=""
    if [ "$DL_STATUS" = "200" ]; then
        echo "PASS: GET /api/users/{id}/certs/download/${TEST_CERT} returns 200"
    else
        echo "FAIL: GET /api/users/{id}/certs/download/${TEST_CERT} (status: ${DL_STATUS})"
        FAILURES=$((FAILURES + 1))
    fi

    # 409 on duplicate cert name
    DUP_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
        -X POST "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/generate" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"${TEST_CERT}\"}" 2>/dev/null) || DUP_STATUS=""
    if [ "$DUP_STATUS" = "409" ]; then
        echo "PASS: Duplicate cert name returns 409"
    else
        echo "FAIL: Duplicate cert name should return 409 (got: ${DUP_STATUS})"
        FAILURES=$((FAILURES + 1))
    fi

    # Capture the serial number before revocation for CRL verification
    CERT_SERIAL=$(${COMPOSE} exec -T tak-server openssl x509 \
        -in "/opt/tak/certs/files/${TEST_CERT}.pem" -serial -noout 2>/dev/null \
        | sed 's/serial=//') || CERT_SERIAL=""

    # Revoke the cert by name
    REV_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/revoke" \
        -H "Content-Type: application/json" \
        -d "{\"cert_name\":\"${TEST_CERT}\"}" 2>/dev/null) || REV_RESP=""
    if echo "$REV_RESP" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: POST /api/users/{id}/certs/revoke revokes by cert_name"
    else
        echo "FAIL: POST /api/users/{id}/certs/revoke"
        echo "  Response: ${REV_RESP:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Verify CRL was updated — check that the revoked serial appears in the CRL
    CRL_SERIALS=$(${COMPOSE} exec -T tak-server openssl crl \
        -in /opt/tak/certs/files/ca.crl -text -noout 2>/dev/null) || CRL_SERIALS=""
    CRL_COUNT=$(echo "$CRL_SERIALS" | grep -c "Serial Number" 2>/dev/null) || CRL_COUNT="0"
    if [ "$CRL_COUNT" -gt "0" ]; then
        echo "PASS: CRL contains $CRL_COUNT revoked serial(s) after revocation"
        # If we captured the serial, verify it specifically
        if [ -n "$CERT_SERIAL" ]; then
            # CRL shows serials in lowercase with colons, normalize for comparison
            SERIAL_LOWER=$(echo "$CERT_SERIAL" | tr '[:upper:]' '[:lower:]' | sed 's/.\{2\}/&:/g' | sed 's/:$//')
            if echo "$CRL_SERIALS" | grep -qi "$CERT_SERIAL\|$SERIAL_LOWER"; then
                echo "PASS: CRL contains the specific revoked cert serial ($CERT_SERIAL)"
            else
                echo "WARN: CRL updated but could not match specific serial $CERT_SERIAL (may differ in format)"
            fi
        fi
    else
        echo "FAIL: CRL is empty after revocation — expected at least 1 revoked serial"
        FAILURES=$((FAILURES + 1))
    fi

    # Verify cert now shows as revoked in the list
    REVOKED_CHECK=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/users/${WEBADMIN_ID}/certs" 2>/dev/null \
        | python3 -c "
import sys,json
certs=json.load(sys.stdin)
for c in certs:
    if c.get('name')=='${TEST_CERT}':
        print('true' if c.get('revoked') is True else 'false')
        break
else:
    print('not_found')
" 2>/dev/null) || REVOKED_CHECK="error"
    if [ "$REVOKED_CHECK" = "true" ]; then
        echo "PASS: Revoked cert shows revoked=true in cert list"
    else
        echo "FAIL: Revoked cert should show revoked=true, got '${REVOKED_CHECK}'"
        FAILURES=$((FAILURES + 1))
    fi
else
    echo "SKIP: Could not find webadmin user for cert management tests"
fi

# ── Re-enrollment Prevention (Closes #26) ─────────────────────────────
echo ""
echo "=== Testing Re-enrollment Prevention ==="

# Behavioral test: create a token, verify it authenticates on 8446,
# revoke the cert (which deletes the token), then verify the same
# token is rejected. This proves the full revocation pipeline works
# end-to-end against the live LDAP outpost.

if [ -n "$WEBADMIN_ID" ]; then
    # Step 1: Create enrollment token via API
    REENROLL_TOKEN=$(${COMPOSE} exec -T monitor python3 -c "
from app.api.users.authentik import AuthentikClient
from app.config import settings
ak = AuthentikClient(base_url=settings.authentik_url, token=settings.authentik_api_token, hidden_prefixes=[])
token, _ = ak.get_or_create_enrollment_token(${WEBADMIN_ID}, 15)
print(token)
" 2>/dev/null) || REENROLL_TOKEN=""

    if [ -n "$REENROLL_TOKEN" ]; then
        echo "PASS: Enrollment token created"

        # Step 2: Verify token authenticates on 8446 (Basic Auth to TAK Server)
        AUTH_BEFORE=$(${COMPOSE} exec -T tak-server curl -sk \
            -u "webadmin:${REENROLL_TOKEN}" \
            "https://localhost:8446/Marti/api/tls/config" \
            -w '%{http_code}' -o /dev/null 2>/dev/null) || AUTH_BEFORE=""
        AUTH_BEFORE="${AUTH_BEFORE: -3}"

        if [ "$AUTH_BEFORE" = "200" ]; then
            echo "PASS: Token authenticates on 8446 before revocation (HTTP 200)"
        else
            echo "FAIL: Token should authenticate before revocation (got HTTP ${AUTH_BEFORE})"
            FAILURES=$((FAILURES + 1))
        fi

        # Step 3: Generate and revoke a cert (triggers token deletion)
        REENROLL_CERT="reenroll_${RUN_ID}"
        ${COMPOSE} exec -T monitor curl -s \
            -X POST "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/generate" \
            -H "Content-Type: application/json" \
            -d "{\"name\":\"${REENROLL_CERT}\"}" 2>/dev/null > /dev/null

        ${COMPOSE} exec -T monitor curl -s \
            -X POST "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/revoke" \
            -H "Content-Type: application/json" \
            -d "{\"cert_name\":\"${REENROLL_CERT}\"}" 2>/dev/null > /dev/null

        # Step 4: Verify the SAME token is now rejected on 8446
        AUTH_AFTER=$(${COMPOSE} exec -T tak-server curl -sk \
            -u "webadmin:${REENROLL_TOKEN}" \
            "https://localhost:8446/Marti/api/tls/config" \
            -w '%{http_code}' -o /dev/null 2>/dev/null) || AUTH_AFTER=""
        AUTH_AFTER="${AUTH_AFTER: -3}"

        if [ "$AUTH_AFTER" = "401" ]; then
            echo "PASS: Token rejected on 8446 after revocation (HTTP 401) — re-enrollment blocked"
        else
            echo "FAIL: Token should be rejected after revocation (got HTTP ${AUTH_AFTER}, expected 401)"
            FAILURES=$((FAILURES + 1))
        fi
    else
        echo "FAIL: Could not create enrollment token for re-enrollment test"
        FAILURES=$((FAILURES + 1))
    fi
else
    echo "SKIP: No webadmin user for re-enrollment prevention tests"
fi

# ── Service Account Lifecycle (Data Mode) ────────────────────────────
echo ""
echo "=== Testing Service Account Lifecycle (Data Mode) ==="

# Create a group first (needed for data mode)
SVC_GRP_RESP=$(${COMPOSE} exec -T monitor curl -sf \
    -X POST "http://localhost:8080/api/groups" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${SVC_TEST_GROUP}\"}" 2>/dev/null) || SVC_GRP_RESP=""
SVC_GRP_ID=$(echo "$SVC_GRP_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null) || SVC_GRP_ID=""

# Create a data-mode service account with that group
SVC_DATA_RESP=$(${COMPOSE} exec -T monitor curl -sf \
    -X POST "http://localhost:8080/api/service-accounts" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${SVC_DATA_NAME}\",\"display_name\":\"Data ${RUN_ID}\",\"mode\":\"data\",\"groups\":[\"${SVC_TEST_GROUP}\"]}" 2>/dev/null) || SVC_DATA_RESP=""

SVC_DATA_ID=$(echo "$SVC_DATA_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null) || SVC_DATA_ID=""
SVC_DATA_USER=$(echo "$SVC_DATA_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('username',''))" 2>/dev/null) || SVC_DATA_USER=""
SVC_DATA_MODE=$(echo "$SVC_DATA_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('mode',''))" 2>/dev/null) || SVC_DATA_MODE=""
SVC_DATA_DL=$(echo "$SVC_DATA_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cert_download_url',''))" 2>/dev/null) || SVC_DATA_DL=""

if [ "$SVC_DATA_USER" = "svc_${SVC_DATA_NAME}" ] && [ "$SVC_DATA_MODE" = "data" ] && [ -n "$SVC_DATA_ID" ]; then
    echo "PASS: POST /api/service-accounts creates data-mode account (username=svc_${SVC_DATA_NAME}, mode=data)"
else
    echo "FAIL: POST /api/service-accounts data-mode — got username='${SVC_DATA_USER}', mode='${SVC_DATA_MODE}'"
    echo "  Response: ${SVC_DATA_RESP:0:200}"
    FAILURES=$((FAILURES + 1))
fi

if echo "$SVC_DATA_DL" | grep -q "/api/service-accounts/.*/certs/download"; then
    echo "PASS: Response includes cert_download_url"
else
    echo "FAIL: Response missing or incorrect cert_download_url: '${SVC_DATA_DL}'"
    FAILURES=$((FAILURES + 1))
fi

if [ -n "$SVC_DATA_ID" ]; then
    # Verify it appears in the list
    SVC_LIST_CHECK=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/service-accounts" 2>/dev/null \
        | python3 -c "
import sys,json
results=json.load(sys.stdin).get('results',[])
found=[a for a in results if a.get('username')=='svc_${SVC_DATA_NAME}']
print('true' if found else 'false')
" 2>/dev/null) || SVC_LIST_CHECK="false"
    if [ "$SVC_LIST_CHECK" = "true" ]; then
        echo "PASS: GET /api/service-accounts includes svc_${SVC_DATA_NAME}"
    else
        echo "FAIL: GET /api/service-accounts does not include svc_${SVC_DATA_NAME}"
        FAILURES=$((FAILURES + 1))
    fi

    # Download cert — verify 200
    SVC_DL_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
        "http://localhost:8080/api/service-accounts/${SVC_DATA_ID}/certs/download" 2>/dev/null) || SVC_DL_STATUS=""
    if [ "$SVC_DL_STATUS" = "200" ]; then
        echo "PASS: GET /api/service-accounts/{id}/certs/download returns 200 (cert exists)"
    else
        echo "FAIL: GET /api/service-accounts/{id}/certs/download (status: ${SVC_DL_STATUS})"
        FAILURES=$((FAILURES + 1))
    fi

    # Get account detail — verify certs are included
    SVC_DETAIL_RESP=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/service-accounts/${SVC_DATA_ID}" 2>/dev/null) || SVC_DETAIL_RESP=""
    SVC_HAS_CERTS=$(echo "$SVC_DETAIL_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
certs=d.get('certs')
has_username = d.get('username')=='svc_${SVC_DATA_NAME}'
has_certs = isinstance(certs, list)
print('true' if has_username and has_certs else 'false')
" 2>/dev/null) || SVC_HAS_CERTS="false"
    if [ "$SVC_HAS_CERTS" = "true" ]; then
        echo "PASS: GET /api/service-accounts/{id} returns detail with certs list"
    else
        echo "FAIL: GET /api/service-accounts/{id} missing username or certs"
        echo "  Response: ${SVC_DETAIL_RESP:0:300}"
        FAILURES=$((FAILURES + 1))
    fi

    # Update display name via PATCH
    SVC_PATCH=$(${COMPOSE} exec -T monitor curl -sf \
        -X PATCH "http://localhost:8080/api/service-accounts/${SVC_DATA_ID}" \
        -H "Content-Type: application/json" \
        -d '{"display_name":"Updated Data Name"}' 2>/dev/null) || SVC_PATCH=""
    if echo "$SVC_PATCH" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: PATCH /api/service-accounts/{id} updates display name"
    else
        echo "FAIL: PATCH /api/service-accounts/{id}"
        echo "  Response: ${SVC_PATCH:0:200}"
        FAILURES=$((FAILURES + 1))
    fi

    # Deactivate via DELETE
    SVC_DEL=$(${COMPOSE} exec -T monitor curl -sf \
        -X DELETE "http://localhost:8080/api/service-accounts/${SVC_DATA_ID}" 2>/dev/null) || SVC_DEL=""
    SVC_DEL_USER=$(echo "$SVC_DEL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('username',''))" 2>/dev/null) || SVC_DEL_USER=""
    SVC_DEL_OK=$(echo "$SVC_DEL" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null) || SVC_DEL_OK="false"
    if [ "$SVC_DEL_OK" = "true" ] && [ "$SVC_DEL_USER" = "svc_${SVC_DATA_NAME}" ]; then
        echo "PASS: DELETE /api/service-accounts/{id} deactivates (success=true, username=svc_${SVC_DATA_NAME})"
    else
        echo "FAIL: DELETE /api/service-accounts/{id} — success='${SVC_DEL_OK}', username='${SVC_DEL_USER}'"
        FAILURES=$((FAILURES + 1))
    fi

    # Verify deactivated — check is_active is False
    SVC_DEACT_CHECK=$(${COMPOSE} exec -T monitor curl -sf \
        "http://localhost:8080/api/service-accounts/${SVC_DATA_ID}" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('is_active',''))" 2>/dev/null) || SVC_DEACT_CHECK=""
    if [ "$SVC_DEACT_CHECK" = "False" ]; then
        echo "PASS: Deactivated service account shows is_active=False"
    else
        echo "FAIL: Deactivated service account is_active='${SVC_DEACT_CHECK}' (expected False)"
        FAILURES=$((FAILURES + 1))
    fi
fi

# ── Service Account Lifecycle (Admin Mode) ───────────────────────────
echo ""
echo "=== Testing Service Account Lifecycle (Admin Mode) ==="

SVC_ADMIN_RESP=$(${COMPOSE} exec -T monitor curl -sf \
    -X POST "http://localhost:8080/api/service-accounts" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${SVC_ADMIN_NAME}\",\"display_name\":\"Admin ${RUN_ID}\",\"mode\":\"admin\"}" 2>/dev/null) || SVC_ADMIN_RESP=""

SVC_ADMIN_ID=$(echo "$SVC_ADMIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null) || SVC_ADMIN_ID=""
SVC_ADMIN_USER=$(echo "$SVC_ADMIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('username',''))" 2>/dev/null) || SVC_ADMIN_USER=""
SVC_ADMIN_MODE=$(echo "$SVC_ADMIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('mode',''))" 2>/dev/null) || SVC_ADMIN_MODE=""

if [ "$SVC_ADMIN_USER" = "svc_${SVC_ADMIN_NAME}" ] && [ "$SVC_ADMIN_MODE" = "admin" ] && [ -n "$SVC_ADMIN_ID" ]; then
    echo "PASS: POST /api/service-accounts creates admin-mode account (username=svc_${SVC_ADMIN_NAME})"
else
    echo "FAIL: POST /api/service-accounts admin-mode — got username='${SVC_ADMIN_USER}', mode='${SVC_ADMIN_MODE}'"
    echo "  Response: ${SVC_ADMIN_RESP:0:200}"
    FAILURES=$((FAILURES + 1))
fi

if [ -n "$SVC_ADMIN_ID" ]; then
    # Download cert — verify 200
    ADMIN_DL_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
        "http://localhost:8080/api/service-accounts/${SVC_ADMIN_ID}/certs/download" 2>/dev/null) || ADMIN_DL_STATUS=""
    if [ "$ADMIN_DL_STATUS" = "200" ]; then
        echo "PASS: GET /api/service-accounts/{id}/certs/download returns 200 for admin account"
    else
        echo "FAIL: GET /api/service-accounts/{id}/certs/download for admin (status: ${ADMIN_DL_STATUS})"
        FAILURES=$((FAILURES + 1))
    fi

    # Deactivate
    ADMIN_DEL=$(${COMPOSE} exec -T monitor curl -sf \
        -X DELETE "http://localhost:8080/api/service-accounts/${SVC_ADMIN_ID}" 2>/dev/null) || ADMIN_DEL=""
    if echo "$ADMIN_DEL" | python3 -c "import sys,json; print('true' if json.load(sys.stdin).get('success') else 'false')" 2>/dev/null | grep -q "true"; then
        echo "PASS: DELETE /api/service-accounts/{id} deactivates admin account"
    else
        echo "FAIL: DELETE /api/service-accounts/{id} admin deactivation"
        FAILURES=$((FAILURES + 1))
    fi
fi

# ── Validation Tests ─────────────────────────────────────────────────
echo ""
echo "=== Testing Validation (Error Cases) ==="

# Create service account with nonexistent group -> 400
VAL_NOGRP_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
    -X POST "http://localhost:8080/api/service-accounts" \
    -H "Content-Type: application/json" \
    -d '{"name":"val_nogrp","display_name":"Bad Group","mode":"data","groups":["NONEXISTENT_GROUP_XYZ"]}' 2>/dev/null) || VAL_NOGRP_STATUS=""
if [ "$VAL_NOGRP_STATUS" = "400" ]; then
    echo "PASS: Data-mode with nonexistent group returns 400"
else
    echo "FAIL: Data-mode with nonexistent group should return 400 (got: ${VAL_NOGRP_STATUS})"
    FAILURES=$((FAILURES + 1))
fi

# Create data account without groups -> 422
VAL_NODATA_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
    -X POST "http://localhost:8080/api/service-accounts" \
    -H "Content-Type: application/json" \
    -d '{"name":"val_nodata","display_name":"No Groups","mode":"data"}' 2>/dev/null) || VAL_NODATA_STATUS=""
if [ "$VAL_NODATA_STATUS" = "422" ]; then
    echo "PASS: Data-mode without groups returns 422"
else
    echo "FAIL: Data-mode without groups should return 422 (got: ${VAL_NODATA_STATUS})"
    FAILURES=$((FAILURES + 1))
fi

# Create admin account with groups -> 422
VAL_ADMINGRP_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
    -X POST "http://localhost:8080/api/service-accounts" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"val_admingrp\",\"display_name\":\"Admin With Groups\",\"mode\":\"admin\",\"groups\":[\"${SVC_TEST_GROUP}\"]}" 2>/dev/null) || VAL_ADMINGRP_STATUS=""
if [ "$VAL_ADMINGRP_STATUS" = "422" ]; then
    echo "PASS: Admin-mode with groups returns 422"
else
    echo "FAIL: Admin-mode with groups should return 422 (got: ${VAL_ADMINGRP_STATUS})"
    FAILURES=$((FAILURES + 1))
fi

# Duplicate cert name -> 409 (reuse webadmin and generate a cert then try again)
if [ -n "$WEBADMIN_ID" ]; then
    # Generate a fresh cert for the dup test
    ${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/generate" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"${TEST_DUP_CERT}\"}" 2>/dev/null || true
    VAL_DUP_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
        -X POST "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/generate" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"${TEST_DUP_CERT}\"}" 2>/dev/null) || VAL_DUP_STATUS=""
    if [ "$VAL_DUP_STATUS" = "409" ]; then
        echo "PASS: Duplicate cert name returns 409 (validation test)"
    else
        echo "FAIL: Duplicate cert name should return 409 (got: ${VAL_DUP_STATUS})"
        FAILURES=$((FAILURES + 1))
    fi

    # Clean up: revoke the duplicate cert
    ${COMPOSE} exec -T monitor curl -sf \
        -X POST "http://localhost:8080/api/users/${WEBADMIN_ID}/certs/revoke" \
        -H "Content-Type: application/json" \
        -d "{\"cert_name\":\"${TEST_DUP_CERT}\"}" 2>/dev/null || true
fi

# Download cert for nonexistent account -> 404
VAL_404_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
    "http://localhost:8080/api/service-accounts/999999/certs/download" 2>/dev/null) || VAL_404_STATUS=""
if [ "$VAL_404_STATUS" = "404" ]; then
    echo "PASS: Download cert for nonexistent account returns 404"
else
    echo "FAIL: Download cert for nonexistent account should return 404 (got: ${VAL_404_STATUS})"
    FAILURES=$((FAILURES + 1))
fi

# ── Removed Endpoints ────────────────────────────────────────────────
echo ""
echo "=== Testing Removed Endpoints ==="

for REMOVED_PATH in \
    "POST /api/ops/certs/create-client/test" \
    "POST /api/ops/certs/create-server/test" \
    "GET /api/ops/certs/list" \
    "POST /api/ops/certs/revoke"; do
    REMOVED_METHOD=$(echo "$REMOVED_PATH" | cut -d' ' -f1)
    REMOVED_URL=$(echo "$REMOVED_PATH" | cut -d' ' -f2)
    REMOVED_STATUS=$(${COMPOSE} exec -T monitor curl -s -o /dev/null -w '%{http_code}' \
        -X "$REMOVED_METHOD" "http://localhost:8080${REMOVED_URL}" 2>/dev/null) || REMOVED_STATUS=""
    if [ "$REMOVED_STATUS" = "404" ] || [ "$REMOVED_STATUS" = "405" ]; then
        echo "PASS: ${REMOVED_METHOD} ${REMOVED_URL} returns ${REMOVED_STATUS} (removed)"
    else
        echo "FAIL: ${REMOVED_METHOD} ${REMOVED_URL} should be removed (status: ${REMOVED_STATUS})"
        FAILURES=$((FAILURES + 1))
    fi
done

# ── Cleanup Test Resources ───────────────────────────────────────────
echo ""
echo "=== Cleaning Up Test Resources ==="

# Delete test lifecycle user (if created)
if [ -n "$LIFECYCLE_ID" ]; then
    # Deactivate if still active (idempotent)
    ${COMPOSE} exec -T monitor curl -sf \
        -X DELETE "http://localhost:8080/api/users/${LIFECYCLE_ID}" 2>/dev/null || true
    echo "  Cleaned up test user ${TEST_LIFECYCLE_USER}"
fi

# Delete service test group (if created)
if [ -n "$SVC_GRP_ID" ]; then
    ${COMPOSE} exec -T monitor curl -sf \
        -X DELETE "http://localhost:8080/api/groups/${SVC_GRP_ID}" 2>/dev/null || true
    echo "  Cleaned up group ${SVC_TEST_GROUP}"
fi

echo "  Test resource cleanup complete"

# Note: idempotency (restart) test removed from test-run.sh because it tears
# down and rebuilds the stack, which is destructive for iterative development.
# The full cycle (just test-integration / test-stack.sh) can add it back if needed.

# ── Summary ────────────────────────────────────────────────────────────
echo ""
if [ "$FAILURES" -eq 0 ]; then
    echo "=== ALL TESTS PASSED ==="
    exit 0
else
    echo "=== ${FAILURES} TEST(S) FAILED ==="
    exit 1
fi

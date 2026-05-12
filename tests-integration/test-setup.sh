#!/usr/bin/env bash
# test-setup.sh — Stand up an isolated FastTAK test stack.
#
# Creates a unique project with isolated tak/ directory and .env.
# Leaves the stack running for manual testing or test-run.sh.
#
# Usage:
#   ./tests-integration/test-setup.sh              # detached, waits for healthy
#   ./tests-integration/test-setup.sh --foreground # stop detached, re-run attached
#   ./tests-integration/test-setup.sh --no-up      # extract tak/, generate .env,
#                                                  # write state, but do NOT start
#                                                  # containers. Used by the
#                                                  # backup-restore test; restore.sh
#                                                  # boots the stack itself after
#                                                  # overwriting .env with the
#                                                  # archive's env.
# Output: prints the project name to stdout (last line)
#
# To rebuild just the monitor after code changes:
#   docker compose -p <project> -f docker-compose.yml -f docker-compose.test.yml build monitor
#   docker compose -p <project> -f docker-compose.yml -f docker-compose.test.yml up -d --force-recreate monitor

set -euo pipefail

NO_UP=false
FOREGROUND=false
for arg in "$@"; do
    case "$arg" in
        --no-up) NO_UP=true ;;
        --foreground) FOREGROUND=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TS=$(date +%s)
PROJECT="fastak-test-${TS}"
TEST_DIR="/tmp/${PROJECT}"
TIMEOUT=300
INTERVAL=10

export TAK_HOST_PATH="${TEST_DIR}/tak"
mkdir -p "${TEST_DIR}/backups"
export BACKUP_DIR="${TEST_DIR}/backups"
# Point the monitor's /host/.env mount at the real test .env (lives under
# TEST_DIR, not the repo root). docker-compose.test.yml consumes this.
export HOST_ENV_FILE="${TEST_DIR}/.env"

COMPOSE="docker compose -p ${PROJECT} -f ${REPO_DIR}/docker-compose.yml -f ${REPO_DIR}/docker-compose.test.yml --env-file ${TEST_DIR}/.env"

# ── Helper: wait for all services to become healthy ───────────────────
wait_healthy() {
    local label="${1:-services}"
    echo "  Waiting for ${label} to become healthy (timeout: ${TIMEOUT}s)..." >&2
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
    if status == 'exited' and svc.get('ExitCode', 1) == 0:
        continue
    if health == '' and status == 'running':
        continue
    if health not in ('healthy', ''):
        unhealthy.append(f'{name}({health})')
    elif status not in ('running', 'exited'):
        unhealthy.append(f'{name}({status})')
print(','.join(unhealthy))
" 2>/dev/null || echo "parse-error")

        if [ -z "$unhealthy" ]; then
            echo "  All ${label} healthy after ${elapsed}s" >&2
            return 0
        fi
        echo "  Waiting... (${elapsed}s) unhealthy: ${unhealthy}" >&2
        sleep "$INTERVAL"
        elapsed=$((elapsed + INTERVAL))
    done

    echo "FAIL: Timed out waiting for ${label}" >&2
    ${COMPOSE} ps >&2
    return 1
}

# ── Run setup.sh to extract tak/ and generate .env ────────────────────
# ── Clean up any existing test stacks before creating a new one ────────
for existing_dir in /tmp/fastak-test-*/; do
    [ -d "$existing_dir" ] || continue
    existing_name=$(basename "$existing_dir")
    echo "Cleaning up existing test stack: ${existing_name}" >&2
    # Subshell so a sourced .test-state can't overwrite this script's
    # PROJECT/REPO_DIR/ENV_FILE. Tear down by project name only — the
    # compose files referenced in old state may not exist anymore (e.g.
    # the stack was created from a since-deleted worktree).
    (
        project_name="${existing_name}"
        if [ -f "${existing_dir}.test-state" ]; then
            # shellcheck disable=SC1090
            source "${existing_dir}.test-state" 2>/dev/null || true
            project_name="${PROJECT:-${existing_name}}"
        fi
        docker compose -p "${project_name}" down -v 2>/dev/null || true
    )
    rm -rf "$existing_dir"
done

echo "=== Setting up test stack: ${PROJECT} ===" >&2
ZIP=$(find "${REPO_DIR}" -maxdepth 1 -name 'takserver-docker-*.zip' | head -1)
if [ -z "${ZIP}" ]; then
    echo "FAIL: No takserver-docker-*.zip found in ${REPO_DIR}" >&2
    exit 1
fi
"${REPO_DIR}/setup.sh" -d "${TEST_DIR}" "${ZIP}" >&2

sed -i.bak "s/^SERVER_ADDRESS=.*/SERVER_ADDRESS=test.fastak.local/" "${TEST_DIR}/.env"
sed -i.bak "s/^DEPLOY_MODE=.*/DEPLOY_MODE=direct/" "${TEST_DIR}/.env"
rm -f "${TEST_DIR}/.env.bak"

# Copy repo scripts into TAK_HOST_PATH so the single volume mount picks them
# up. This avoids overlaying individual file bind mounts onto the TAK volume,
# which fails on Docker Desktop virtiofs when TAK_HOST_PATH is on a different
# filesystem (e.g., /private/tmp).
cp "${REPO_DIR}/tak-server/healthcheck.sh" "${TAK_HOST_PATH}/healthcheck.sh"
cp "${REPO_DIR}/tak-server/register-api-cert.sh" "${TAK_HOST_PATH}/register-api-cert.sh"

# Write project info to a state file. We do this BEFORE bringing the stack
# up so that --no-up callers (restore.sh) and any teardown after a failed
# `up` can still find the state.
mkdir -p "${TEST_DIR}"
cat > "${TEST_DIR}/.test-state" << EOF
PROJECT="${PROJECT}"
TEST_DIR="${TEST_DIR}"
TAK_HOST_PATH="${TAK_HOST_PATH}"
REPO_DIR="${REPO_DIR}"
ENV_FILE="${TEST_DIR}/.env"
EOF

if [ "$NO_UP" = "true" ]; then
    echo "=== Test stack scaffolded (no-up mode) ===" >&2
    echo "  Project: ${PROJECT}" >&2
    echo "  State:   ${TEST_DIR}/.test-state" >&2
    echo "" >&2
    echo "${PROJECT}"
    exit 0
fi

# ── Build and start ────────────────────────────────────────────────────
echo "=== Starting stack ===" >&2
${COMPOSE} up -d --build >&2

echo "=== Waiting for services ===" >&2
if ! wait_healthy "services"; then
    exit 1
fi

echo "=== Test stack ready ===" >&2
echo "  Project: ${PROJECT}" >&2
echo "  State:   ${TEST_DIR}/.test-state" >&2
echo "" >&2

# Print project name as last line (for callers to capture)
echo "${PROJECT}"

# If --foreground flag passed, stop the detached stack and re-run in foreground.
# When the foreground process is killed, containers stop automatically.
if [ "$FOREGROUND" = "true" ]; then
    echo "=== Switching to foreground mode (containers stop when process dies) ===" >&2
    ${COMPOSE} stop >&2
    exec ${COMPOSE} up
fi

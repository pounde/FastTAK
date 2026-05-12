#!/usr/bin/env bash
# restore.sh — Canonical FastTAK restore procedure. Mirrors
# docs/backup-and-restore.md "Restoring to a fresh host" (steps 1-8). The
# integration test invokes this script; operators following the doc can
# adapt the same invocation against their own deployment.
#
# Pre-conditions:
#   - The target stack is NOT running for this project. (Bring it down first.)
#   - The TAK Server release zip has been extracted via setup.sh, so tak/
#     exists and a fresh .env sits at <env-file>. restore.sh will overwrite
#     that .env with the archive's env BEFORE booting any container.
#
# Usage:
#   restore.sh <project> <backup.age> <key-file> <env-file> <tak-host-path> <repo-dir> [<extra-compose-file>...]
#
# <project>           docker compose project name (e.g. fastak-test-1234567890)
# <backup.age>        encrypted backup tarball
# <key-file>          age identity used to decrypt the tarball
# <env-file>          path to the freshly-generated .env (WILL BE OVERWRITTEN)
# <tak-host-path>     path to the extracted tak/ tree
# <repo-dir>          FastTAK repo root (for docker-compose.yml)
# <extra-compose>     additional -f compose files (test passes docker-compose.test.yml)

set -euo pipefail

PROJECT="${1:?project required}"
BACKUP="${2:?backup path required}"
KEYFILE="${3:?key file required}"
ENV_FILE="${4:?env file required}"
TAK_HOST_PATH="${5:?tak host path required}"
REPO_DIR="${6:?repo dir required}"
shift 6

# Export TAK_HOST_PATH so docker compose picks it up for the bind mounts
# defined as `${TAK_HOST_PATH:-./tak}:...`. The restored .env doesn't
# contain it (and shouldn't — the path is host-specific, not data), so
# without this export compose would fall back to ./tak relative to the
# compose file's directory (the repo's tak/), giving the restored stack
# the wrong cert/config tree. BACKUP_DIR and HOST_ENV_FILE follow the
# same pattern; default them to test-style locations under the env file's
# dir if the caller hasn't already exported them.
export TAK_HOST_PATH
ENV_DIR="$(cd "$(dirname "$ENV_FILE")" && pwd)"
: "${BACKUP_DIR:=${ENV_DIR}/backups}"
: "${HOST_ENV_FILE:=${ENV_FILE}}"
export BACKUP_DIR HOST_ENV_FILE

COMPOSE=(docker compose -p "$PROJECT" -f "${REPO_DIR}/docker-compose.yml")
for f in "$@"; do
    COMPOSE+=(-f "$f")
done
COMPOSE+=(--env-file "$ENV_FILE")

TIMEOUT=300
INTERVAL=10

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Wait for the named services to all report Health=healthy. Services with
# no healthcheck are accepted once State=running; init containers that have
# already exited 0 are accepted.
wait_healthy() {
    local label="$1"
    shift
    echo "[restore] waiting for ${label} (timeout: ${TIMEOUT}s)" >&2
    local elapsed=0
    while [ "$elapsed" -lt "$TIMEOUT" ]; do
        local unhealthy
        unhealthy=$("${COMPOSE[@]}" ps --format json "$@" 2>/dev/null | \
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
            echo "[restore] ${label} healthy after ${elapsed}s" >&2
            return 0
        fi
        echo "[restore] waiting... (${elapsed}s) unhealthy: ${unhealthy}" >&2
        sleep "$INTERVAL"
        elapsed=$((elapsed + INTERVAL))
    done
    echo "FAIL: ${label} did not become healthy in ${TIMEOUT}s" >&2
    "${COMPOSE[@]}" ps >&2
    return 1
}

# ── Step 1: decrypt the archive ───────────────────────────────────────
echo "[restore] decrypting $BACKUP"
age -d -i "$KEYFILE" "$BACKUP" | tar xz -C "$WORK"

# ── Step 2: inspect the manifest ──────────────────────────────────────
echo "[restore] manifest:"
cat "$WORK/MANIFEST.json"
echo ""

# ── Step 3: replace .env with the archive's env ──────────────────────
# This must happen BEFORE any container starts so DB containers initialize
# with the restored POSTGRES_PASSWORD / TAK_DB_PASSWORD (matching the role
# hashes the restored data will reference).
echo "[restore] replacing $ENV_FILE with archive env"
cp "$WORK/env" "$ENV_FILE"

# ── Step 4: restore TAK certificates ──────────────────────────────────
echo "[restore] restoring tak-certs to $TAK_HOST_PATH/certs"
mkdir -p "$TAK_HOST_PATH/certs"
tar -x -C "$TAK_HOST_PATH/certs" -f "$WORK/tak-certs.tar"

# ── Step 4b: restore TAK config files (CoreConfig.xml etc.) ──────────
# These files live at the tak/ root, not under tak/certs. Without them,
# TAK Server reverts to defaults from CoreConfig.example.xml on first
# boot of the restored stack and loses operator customization.
if [ -f "$WORK/tak-config.tar" ]; then
    echo "[restore] restoring tak-config to $TAK_HOST_PATH"
    tar -x -C "$TAK_HOST_PATH" -f "$WORK/tak-config.tar"
fi

# ── Step 5: restore the Node-RED volume ───────────────────────────────
# Pre-create the project's nodered-data named volume with the compose
# project/volume labels — without them, `docker compose down -v` won't
# recognize the volume as ours and it'd leak across runs. When compose
# later starts the nodered service, it attaches to this existing volume
# rather than auto-creating an empty one.
NODERED_VOL="${PROJECT}_nodered-data"
echo "[restore] restoring nodered-data volume (${NODERED_VOL})"
docker volume create \
    --label "com.docker.compose.project=${PROJECT}" \
    --label "com.docker.compose.volume=nodered-data" \
    "$NODERED_VOL" >/dev/null
docker run --rm -i \
    -v "${NODERED_VOL}:/data" \
    alpine sh -c 'cd /data && tar x' < "$WORK/nodered-data.tar"

# ── Step 6: start DB services only ────────────────────────────────────
# This is the first boot of these containers, so postgres initdb runs with
# the restored secrets — role passwords in pg_authid will match .env.
echo "[restore] starting DB services"
"${COMPOSE[@]}" up -d --build tak-database app-db
wait_healthy "DB services" tak-database app-db

# ── Step 7: restore application databases ─────────────────────────────
# Dumps are --format=plain without --clean; the target DB must exist and
# be empty. DROP/CREATE first, then pipe the dump in. -h localhost forces
# TCP so psql doesn't fall through to peer auth on the Unix socket (the
# postgres OS user mapping doesn't include martiuser/fastak). PGPASSWORD
# and TAK_DB_PASSWORD / POSTGRES_PASSWORD are read from the container's
# environment (set by compose from .env).
echo "[restore] restoring cot database"
# shellcheck disable=SC2016  # vars expand inside the container, not on the host
"${COMPOSE[@]}" exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d postgres -c "DROP DATABASE IF EXISTS cot WITH (FORCE);" -c "CREATE DATABASE cot OWNER martiuser;"'
# shellcheck disable=SC2016
"${COMPOSE[@]}" exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot' \
    < "$WORK/postgres/cot.sql"

for db in lldap nodered fastak; do
    echo "[restore] restoring $db database"
    "${COMPOSE[@]}" exec -T app-db \
        sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -h localhost -U fastak -d postgres -c \"DROP DATABASE IF EXISTS $db WITH (FORCE);\" -c \"CREATE DATABASE $db OWNER fastak;\""
    "${COMPOSE[@]}" exec -T app-db \
        sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -h localhost -U fastak -d $db" \
        < "$WORK/postgres/$db.sql"
done

# ── Step 8: start the rest of the stack ───────────────────────────────
echo "[restore] starting the rest of the stack"
"${COMPOSE[@]}" up -d --build
wait_healthy "all services"

echo "[restore] complete"

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

# ── Extension pre-creation ────────────────────────────────────────────
# See DD-054. docs/backup-and-restore.md spells the same SQL out longhand for
# an operator restoring by hand; this script is canonical if the two disagree.
#
# upgrade_dump_extensions <plain-sql-dump>
#
# Prints the extensions the dump asks for, one per line, in the dump's own
# order and deduplicated.
#
# Why this exists: every TAK cot dump carries `CREATE EXTENSION IF NOT EXISTS
# postgis`, postgis is not a trusted extension, and the restore below connects
# as martiuser (rolsuper=f). The statement therefore fails with "permission
# denied to create extension" and ON_ERROR_STOP aborts the restore. Creating
# the extensions as a superuser first turns the dump's own IF NOT EXISTS into
# a notice.
#
# Discovered rather than hardcoded: the set varies by TAK release — this dump
# also carries pgcrypto, and postgis_topology/fuzzystrmatch appear elsewhere —
# and a hardcoded `postgis` would put the next release back on this same
# failure path.
#
# Scanning stops at the first COPY: pg_dump writes every CREATE EXTENSION in
# the pre-data section, ahead of all data, so nothing is missed — and a
# multi-GB cot dump is not read twice. It also keeps a row of CoT data that
# happens to begin with the text "CREATE EXTENSION" from being read as one.
#
# Names are restricted to [A-Za-z0-9_-], which covers every real extension
# name and cannot carry a quote, a semicolon or a shell metacharacter — they
# are interpolated into SQL below.
upgrade_dump_extensions() {
    awk '
      /^[[:space:]]*COPY[[:space:]].*[[:space:]]FROM[[:space:]]+stdin;[[:space:]]*$/ { exit }
      /^[[:space:]]*CREATE[[:space:]]+EXTENSION[[:space:]]/ {
        s = $0
        sub(/^[[:space:]]*CREATE[[:space:]]+EXTENSION[[:space:]]+/, "", s)
        sub(/^IF[[:space:]]+NOT[[:space:]]+EXISTS[[:space:]]+/, "", s)
        name = ""
        if (match(s, /^"[A-Za-z0-9_-]+"/)) {
          name = substr(s, RSTART + 1, RLENGTH - 2)
        } else if (match(s, /^[A-Za-z0-9_-]+/)) {
          name = substr(s, RSTART, RLENGTH)
        }
        if (name != "" && !(name in seen)) {
          seen[name] = 1
          print name
        }
      }
    ' "$1"
}

# upgrade_extension_sql <role>  (extension names on stdin, one per line)
#
# Prints the SQL that pre-creates those extensions and hands each one to
# <role>. Returns 1 when stdin held no names, so the caller can skip the psql
# round-trip entirely.
#
# Creating the extension is only half the job. The dump was taken with
# --no-owner (monitor/app/backup/runner.py), so the restoring role owns
# whatever the dump creates — which is why the restore runs as martiuser and
# not as the superuser. But two statements in the dump then act on objects the
# superuser just created:
#
#   COMMENT ON EXTENSION postgis IS '...'   — requires owning the extension
#   COPY public.spatial_ref_sys ...         — requires INSERT on a table that
#                                             CREATE EXTENSION created
#
# Both fail against a postgres-owned extension; confirmed on a live stack.
# PostgreSQL has no ALTER EXTENSION ... OWNER TO, so extowner is set directly
# — a superuser-only UPDATE of one OID column. extconfig is the extension's
# own list of tables whose *data* pg_dump emits, so it is exactly the set the
# restore will write to. The result is the state martiuser would have reached
# had postgis been trusted enough for it to create itself.
upgrade_extension_sql() {
    local role="$1" list="" name
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        # `if`, not `[ -n "$list" ] && ...`: this script runs under `set -e`,
        # where an AND-list whose test fails would exit on the first name.
        if [ -n "$list" ]; then list="${list}, "; fi
        list="${list}'${name}'"
    done
    [ -n "$list" ] || return 1
    cat <<EOF
DO \$fastak\$
DECLARE
  target_oid oid;
  ext_name   text;
  ext_oid    oid;
  cfg        oid;
BEGIN
  SELECT oid INTO target_oid FROM pg_catalog.pg_roles WHERE rolname = '${role}';
  IF target_oid IS NULL THEN
    RAISE EXCEPTION 'role ${role} does not exist on this cluster';
  END IF;
  FOREACH ext_name IN ARRAY ARRAY[${list}] LOOP
    EXECUTE format('CREATE EXTENSION IF NOT EXISTS %I', ext_name);
    SELECT oid INTO ext_oid FROM pg_catalog.pg_extension WHERE extname = ext_name;
    UPDATE pg_catalog.pg_extension SET extowner = target_oid WHERE oid = ext_oid;
    FOR cfg IN SELECT unnest(extconfig) FROM pg_catalog.pg_extension WHERE oid = ext_oid LOOP
      EXECUTE format('ALTER TABLE %s OWNER TO %I', cfg::regclass, '${role}');
    END LOOP;
  END LOOP;
END
\$fastak\$;
EOF
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
#
# -v ON_ERROR_STOP=1 on every psql that reads a dump: without it psql reports
# each statement error and still exits 0, so `set -e` catches nothing short of
# a dropped connection and a restore that errored on every row still "passes".
#
# cot is created SQL_ASCII, matching TAK Server's own cluster
# (monitor/app/backup/manifest.py documents this — SHOW server_version comes
# back as bytes from it). The encoding is pinned explicitly rather than left
# to inherit whatever template1's default is on the target cluster — this
# restore's target is tak-database's own cluster, which is itself SQL_ASCII,
# but pinning it makes the restore correct independent of the target
# cluster's default, defensively. TEMPLATE template0 is required: a database
# whose encoding differs from template1's cannot be cloned from template1.
echo "[restore] restoring cot database"
# shellcheck disable=SC2016  # vars expand inside the container, not on the host
"${COMPOSE[@]}" exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -v ON_ERROR_STOP=1 -h localhost -U martiuser -d postgres -c "DROP DATABASE IF EXISTS cot WITH (FORCE);" -c "CREATE DATABASE cot OWNER martiuser ENCODING '"'"'SQL_ASCII'"'"' TEMPLATE template0;"'

# The dump's extensions, created as a superuser before the unprivileged
# restore reaches them. See upgrade_dump_extensions and upgrade_extension_sql
# above for what fails without this and why the ownership handoff is part of it.
#
# postgres over the Unix socket, not -h localhost: TAK ships its own
# pg_hba.conf, and it is `local all all peer` / `host all all 0.0.0.0/0 md5`.
# Over TCP that means a password FastTAK does not have — .env carries
# TAK_DB_PASSWORD for martiuser, and nothing for postgres. On the socket, peer
# matches the container's own uid 26 (postgres), which is what `compose exec`
# runs as. Verified against a live tak-database container.
COT_EXTENSIONS="$(upgrade_dump_extensions "$WORK/postgres/cot.sql")"
if [ -n "$COT_EXTENSIONS" ]; then
    echo "[restore] pre-creating cot extensions: $(printf '%s' "$COT_EXTENSIONS" | tr '\n' ' ')"
    printf '%s\n' "$COT_EXTENSIONS" \
        | upgrade_extension_sql martiuser \
        | "${COMPOSE[@]}" exec -T tak-database \
            psql -q -v ON_ERROR_STOP=1 -U postgres -d cot -f -
fi

# shellcheck disable=SC2016
"${COMPOSE[@]}" exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -v ON_ERROR_STOP=1 -h localhost -U martiuser -d cot' \
    < "$WORK/postgres/cot.sql"

for db in lldap nodered fastak; do
    echo "[restore] restoring $db database"
    "${COMPOSE[@]}" exec -T app-db \
        sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -v ON_ERROR_STOP=1 -h localhost -U fastak -d postgres -c \"DROP DATABASE IF EXISTS $db WITH (FORCE);\" -c \"CREATE DATABASE $db OWNER fastak;\""
    "${COMPOSE[@]}" exec -T app-db \
        sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -v ON_ERROR_STOP=1 -h localhost -U fastak -d $db" \
        < "$WORK/postgres/$db.sql"
done

# ── Step 8: start the rest of the stack ───────────────────────────────
echo "[restore] starting the rest of the stack"
"${COMPOSE[@]}" up -d --build
wait_healthy "all services"

echo "[restore] complete"

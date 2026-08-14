#!/bin/bash
# scripts/upgrade.sh — FastTAK upgrade entry point.
#
# Usage: ./scripts/upgrade.sh [--skip-cot] [--yes]
#
#   --skip-cot   Do not carry the CoT history across. The tak-db-data volume is
#                recreated empty and TAK Server starts with no history.
#   --yes        Do not prompt before the destructive phase.
#
# Today this migrates databases across a PostgreSQL major version. It is the
# general upgrade entry point, so later upgrade steps attach here rather than
# accreting in start.sh.
#
# Method: take a backup, then restore from it onto fresh volumes. The operator
# reaches this script after `git pull`, at which point docker-compose.yml names
# postgres:18-alpine and compose can no longer start app-db at all — an 18
# server refuses a 15 data directory. So dumping through the compose service is
# not available. The backup archive already contains per-database plain-SQL
# dumps (monitor/app/backup/runner.py) that tests-integration/restore.sh is
# proven to restore, and a backup has to be taken before this anyway.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Target majors, kept beside the images they describe in docker-compose.yml.
APP_DB_TARGET_MAJOR="18"
TAK_DB_TARGET_MAJOR="18"

APP_DB_DATABASES="lldap nodered fastak"

# ── Library-only mode, for unit tests ────────────────────────────────────
# Sourcing with FASTAK_UPGRADE_LIB_ONLY=1 defines the planning helpers and
# returns without touching Docker.

# upgrade_volume_pg_major <volume-name>
#
# Prints the PostgreSQL major recorded in the volume's PG_VERSION file.
# Prints nothing when the volume does not exist — which is the fresh-install
# case, not an error.
upgrade_volume_pg_major() {
  local volume="$1"
  docker volume inspect "$volume" >/dev/null 2>&1 || return 0
  docker run --rm -v "${volume}:/v:ro" alpine:3.20 \
    sh -c 'cat /v/PG_VERSION 2>/dev/null' 2>/dev/null | tr -d '[:space:]'
}

# upgrade_needs_migration <current-major> <target-major>
#
# 0 when the data must be moved. An empty current major means no volume, so
# there is nothing to move.
upgrade_needs_migration() {
  local current="$1" target="$2"
  [ -z "$current" ] && return 1
  [ "$current" = "$target" ] && return 1
  return 0
}

if [ "${FASTAK_UPGRADE_LIB_ONLY:-}" = "1" ]; then
  # shellcheck disable=SC2317  # reached when executed rather than sourced
  return 0 2>/dev/null || exit 0
fi

# ── Argument parsing ─────────────────────────────────────────────────────
SKIP_COT=false
ASSUME_YES=false
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-cot) SKIP_COT=true; shift ;;
    --yes|-y)   ASSUME_YES=true; shift ;;
    *) echo "Unknown argument: $1" >&2
       echo "Usage: $0 [--skip-cot] [--yes]" >&2
       exit 2 ;;
  esac
done

cd "$REPO_DIR" || exit 1

# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/lib-env.sh"

# ── Compose target resolution ────────────────────────────────────────────
# Defaults are the operator's path: the repo's .env, and whatever compose
# auto-loads (docker-compose.yml plus docker-compose.override.yml).
#
# The two overrides exist so the rehearsal integration test can drive this
# script against an isolated test stack, which runs with -f docker-compose.test.yml
# and an .env under /tmp. Without them the test would target the right project
# with the wrong compose files — production port bindings and the wrong .env.
#
# FASTAK_COMPOSE_FILES is colon-separated, matching COMPOSE_FILE's own syntax.
ENV_FILE="${FASTAK_ENV_FILE:-$REPO_DIR/.env}"

COMPOSE_ARGS=()
if [ -n "${FASTAK_COMPOSE_FILES:-}" ]; then
  _old_ifs="$IFS"
  IFS=:
  for _f in $FASTAK_COMPOSE_FILES; do
    COMPOSE_ARGS+=(-f "$_f")
  done
  IFS="$_old_ifs"
fi
# Always explicit: bare `docker compose` would read ./.env, which is not
# necessarily ENV_FILE. The array is never empty, so "${COMPOSE_ARGS[@]}" is
# safe under `set -u` on bash 3.2.
COMPOSE_ARGS+=(--env-file "$ENV_FILE")

compose() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }

PROJECT="$(basename "$REPO_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:]')"
PROJECT="${COMPOSE_PROJECT_NAME:-$PROJECT}"

APP_DB_VOLUME="${PROJECT}_app-db-data"
TAK_DB_VOLUME="${PROJECT}_tak-db-data"

fail() { echo "" >&2; echo "ERROR: $*" >&2; exit 1; }

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          FastTAK Upgrade                 ║"
echo "╚══════════════════════════════════════════╝"

# ── 1. Preflight ─────────────────────────────────────────────────────────
echo ""
echo "▸ Preflight"
[ -f "$ENV_FILE" ] || fail ".env not found. Run ./setup.sh <bundle.zip> first."
"$SCRIPT_DIR/ensure-secrets.sh" "$ENV_FILE" || fail "could not provision required secrets"
"$SCRIPT_DIR/check-env.sh" "$ENV_FILE" || exit 1
echo "  .env validated."

# ── 2. Plan ──────────────────────────────────────────────────────────────
echo ""
echo "▸ Planning"
APP_DB_CURRENT="$(upgrade_volume_pg_major "$APP_DB_VOLUME")"
TAK_DB_CURRENT="$(upgrade_volume_pg_major "$TAK_DB_VOLUME")"

echo "  app-db:       PostgreSQL ${APP_DB_CURRENT:-none} → ${APP_DB_TARGET_MAJOR}"
echo "  tak-database: PostgreSQL ${TAK_DB_CURRENT:-none} → ${TAK_DB_TARGET_MAJOR}"

MIGRATE_APP_DB=false
upgrade_needs_migration "$APP_DB_CURRENT" "$APP_DB_TARGET_MAJOR" && MIGRATE_APP_DB=true

MIGRATE_COT=false
if [ "$SKIP_COT" = false ]; then
  upgrade_needs_migration "$TAK_DB_CURRENT" "$TAK_DB_TARGET_MAJOR" && MIGRATE_COT=true
fi

if [ "$MIGRATE_APP_DB" = false ] && [ "$MIGRATE_COT" = false ] && [ "$SKIP_COT" = false ]; then
  echo ""
  echo "  Nothing to migrate. Start the stack with ./start.sh"
  exit 0
fi

# ── 3. Disk-headroom preflight for the CoT database ──────────────────────
# pg_dump plus the restored copy need room alongside the original. tak.gov's
# own db-utils/upgrade-db.sh requires 1.5x; mirror it rather than inventing a
# number. Aborting here beats filling the disk mid-restore.
if [ "$MIGRATE_COT" = true ]; then
  echo ""
  echo "▸ Checking disk headroom for the CoT database"
  # shellcheck disable=SC2016  # expands inside the container, not on the host
  COT_BYTES="$(compose exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot -tAc "SELECT pg_database_size('"'"'cot'"'"');"' \
    2>/dev/null | tr -d '[:space:]')"
  if [ -n "$COT_BYTES" ] && [ "$COT_BYTES" -gt 0 ] 2>/dev/null; then
    COT_MB=$((COT_BYTES / 1024 / 1024))
    NEED_MB=$(( (COT_MB * 3) / 2 ))
    AVAIL_MB="$(df -m "${BACKUP_DIR:-$REPO_DIR/backups}" | awk 'NR==2 {print $4}')"
    echo "  cot database:    ${COT_MB} MB"
    echo "  required free:   ${NEED_MB} MB (1.5x, matching tak.gov's upgrade-db.sh)"
    echo "  available:       ${AVAIL_MB} MB"
    if [ -n "$AVAIL_MB" ] && [ "$AVAIL_MB" -lt "$NEED_MB" ] 2>/dev/null; then
      fail "not enough free space to migrate the CoT database. Free at least $((NEED_MB - AVAIL_MB)) MB and re-run."
    fi
    echo ""
    echo "  NOTE: the stack is DOWN for the whole migration. A multi-GB cot"
    echo "        database can take a long time to dump and restore. See"
    echo "        https://github.com/pounde/FastTAK/issues/98"
  else
    echo "  Could not measure the cot database — is the stack running?"
    fail "cannot verify disk headroom; bring the stack up or pass --skip-cot"
  fi
fi

# ── 4. Confirm ───────────────────────────────────────────────────────────
if [ "$SKIP_COT" = true ]; then
  echo ""
  echo "  ⚠  --skip-cot: the CoT history will be DISCARDED."
  echo "     TAK Server starts with an empty cot database."
fi

if [ "$ASSUME_YES" = false ]; then
  echo ""
  printf "  Proceed? The stack will be stopped. [y/N] "
  read -r reply
  case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "  Aborted."; exit 1 ;;
  esac
fi

# ── 5. Back up ───────────────────────────────────────────────────────────
echo ""
echo "▸ Taking a backup"
compose exec -T monitor python -m app.backup run \
  || fail "backup failed — refusing to continue. Everything after this point is destructive."

BACKUP_DIR_RESOLVED="$(env_get "$ENV_FILE" BACKUP_DIR)"
BACKUP_DIR_RESOLVED="${BACKUP_DIR_RESOLVED:-$REPO_DIR/backups}"
ARCHIVE="$(find "$BACKUP_DIR_RESOLVED" -maxdepth 1 -name 'fasttak-backup-*.age' -print0 2>/dev/null \
  | xargs -0 ls -t 2>/dev/null | head -1)"
[ -n "$ARCHIVE" ] || fail "no backup archive found in $BACKUP_DIR_RESOLVED after a successful run"
echo "  Archive: $ARCHIVE"

KEYFILE="${BACKUP_DIR_RESOLVED}/.age-identity"
[ -f "$KEYFILE" ] || fail "age identity not found at $KEYFILE"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
age -d -i "$KEYFILE" "$ARCHIVE" | tar xz -C "$WORK" || fail "could not decrypt $ARCHIVE"
for db in $APP_DB_DATABASES; do
  [ -s "$WORK/postgres/${db}.sql" ] || fail "archive is missing postgres/${db}.sql"
done
if [ "$MIGRATE_COT" = true ]; then
  [ -s "$WORK/postgres/cot.sql" ] || fail "archive is missing postgres/cot.sql"
fi
echo "  Archive verified."

# ── 6. Stop the stack ────────────────────────────────────────────────────
echo ""
echo "▸ Stopping the stack"
compose down || fail "docker compose down failed"

# ── 7. Recreate volumes whose major changed ──────────────────────────────
echo ""
echo "▸ Recreating database volumes"
if [ "$MIGRATE_APP_DB" = true ]; then
  docker volume rm "$APP_DB_VOLUME" >/dev/null || fail "could not remove $APP_DB_VOLUME"
  echo "  Removed $APP_DB_VOLUME (was PostgreSQL ${APP_DB_CURRENT})"
fi
# tak-db-data is always recreated: TAK 5.8 ships a build-time initdb, and an
# empty named volume is populated from the image on first use. A pre-existing
# volume is not.
if docker volume inspect "$TAK_DB_VOLUME" >/dev/null 2>&1; then
  docker volume rm "$TAK_DB_VOLUME" >/dev/null || fail "could not remove $TAK_DB_VOLUME"
  echo "  Removed $TAK_DB_VOLUME (was PostgreSQL ${TAK_DB_CURRENT:-empty})"
fi

# ── 8. Start the database services on the new majors ─────────────────────
echo ""
echo "▸ Starting database services"
compose up -d --build tak-database app-db || fail "database services failed to start"

echo "  Waiting for databases..."

# `xargs -r` is GNU-only and this script also runs on macOS during development,
# so the container id is captured into a variable and tested instead.
service_health() {
  local cid
  cid="$(compose ps -q "$1" 2>/dev/null)"
  [ -n "$cid" ] || { printf 'missing'; return; }
  docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null || printf 'unknown'
}

DEADLINE=$((SECONDS + 300))
app_ok=""
tak_ok=""
while [ "$SECONDS" -lt "$DEADLINE" ]; do
  app_ok="$(service_health app-db)"
  tak_ok="$(service_health tak-database)"
  [ "$app_ok" = "healthy" ] && [ "$tak_ok" = "healthy" ] && break
  sleep 5
done
if [ "$app_ok" != "healthy" ] || [ "$tak_ok" != "healthy" ]; then
  fail "databases did not become healthy (app-db=${app_ok:-?} tak-database=${tak_ok:-?})"
fi

# ── 9. Restore ───────────────────────────────────────────────────────────
# Dumps are --format=plain without --clean, so each target database is dropped
# and recreated first. -h localhost forces TCP; peer auth on the Unix socket
# has no mapping for martiuser/fastak. This mirrors tests-integration/restore.sh.
if [ "$MIGRATE_APP_DB" = true ]; then
  echo ""
  echo "▸ Restoring app-db databases"
  for db in $APP_DB_DATABASES; do
    echo "  $db"
    compose exec -T app-db \
      sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -h localhost -U fastak -d postgres -c \"DROP DATABASE IF EXISTS $db WITH (FORCE);\" -c \"CREATE DATABASE $db OWNER fastak;\"" \
      || fail "could not recreate database $db"
    compose exec -T app-db \
      sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -h localhost -U fastak -d $db" \
      < "$WORK/postgres/${db}.sql" || fail "restore of $db failed"
  done
fi

if [ "$MIGRATE_COT" = true ]; then
  echo ""
  echo "▸ Restoring the cot database (this may take a while)"
  # shellcheck disable=SC2016  # expands inside the container, not on the host
  compose exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d postgres -c "DROP DATABASE IF EXISTS cot WITH (FORCE);" -c "CREATE DATABASE cot OWNER martiuser;"' \
    || fail "could not recreate database cot"
  # shellcheck disable=SC2016
  compose exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot' \
    < "$WORK/postgres/cot.sql" || fail "restore of cot failed"
elif [ "$SKIP_COT" = true ]; then
  echo ""
  echo "▸ CoT history NOT migrated (--skip-cot). TAK Server starts with an empty cot database."
fi

# ── 10. Bring the rest up ────────────────────────────────────────────────
echo ""
echo "▸ Starting the rest of the stack"
compose up -d --build --remove-orphans || fail "stack failed to start"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          Upgrade Complete                ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Backup taken before the upgrade: $ARCHIVE"
if [ "$SKIP_COT" = true ]; then
  echo "  CoT history: DISCARDED (--skip-cot)"
else
  echo "  CoT history: migrated"
fi
echo ""
echo "  Verify:  docker compose ps"
echo ""

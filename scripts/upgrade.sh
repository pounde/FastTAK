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

# BASH_SOURCE, not $0: under FASTAK_UPGRADE_LIB_ONLY the script is sourced, and
# $0 is then the *sourcing* shell's name — which would point REPO_DIR at the
# caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
# Three outcomes, kept separable because they call for three different
# responses:
#   0 + the major on stdout — the volume holds a PostgreSQL data directory.
#   1                       — no such volume. Fresh install, not an error.
#   2                       — the volume exists but holds no PG_VERSION, so
#                             there is no data on it to preserve. This is the
#                             *expected* pre-5.8 shape: TAK 5.6 ran initdb into
#                             /var/lib/postgresql/15/data while FastTAK mounted
#                             the volume at /var/lib/postgresql/data, which is
#                             the persistence defect this upgrade fixes. Reading
#                             it as an error would abort the one upgrade this
#                             script exists to perform.
#   3                       — the probe itself did not answer: the daemon is
#                             unreachable, the reader image could not be
#                             pulled, or `docker run` failed.
#
# The container prints a sentinel rather than letting `cat` fail, so a
# successful run that found no PG_VERSION (2) stays distinguishable from a run
# that never happened (3).
#
# The reader image is postgres:18-alpine — the image docker-compose.yml now
# names for app-db, so it is the one image this upgrade needs regardless. It is
# not necessarily on the host yet: the operator's sequence is `git pull` then
# `just upgrade`, and the pull is what introduces 18. Docker pulls it here, and
# on an air-gapped host that is the first thing to fail — which is why a pull
# failure reports as itself instead of as unreadable data.
upgrade_volume_pg_major() {
  local volume="$1" out
  docker volume inspect "$volume" >/dev/null 2>&1 || return 1
  out="$(docker run --rm --entrypoint sh -v "${volume}:/v:ro" postgres:18-alpine \
    -c 'if [ -f /v/PG_VERSION ]; then cat /v/PG_VERSION; else echo __NO_PG_VERSION__; fi' \
    2>/dev/null)" || return 3
  out="$(printf '%s' "$out" | tr -d '[:space:]')"
  [ "$out" = "__NO_PG_VERSION__" ] && return 2
  [ -n "$out" ] || return 3
  printf '%s' "$out"
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

# upgrade_cot_plan <tak-volume-state> <skip-cot>
#
# <tak-volume-state> is "absent", "empty", or a major version string — the
# three outcomes of upgrade_volume_pg_major.
#
# Prints "<remove-volume> <restore-cot>". The invariant:
#
#   restore_cot  ⟺  (the tak-db-data volume was removed)  ∧  ¬SKIP_COT
#
# The restore follows from what was destroyed, never from a version
# comparison. `docker volume rm` does not care whether the major changed, so
# gating the restore on a stale major discards the entire CoT history every
# time tak-database is already on the target — which is the permanent steady
# state after 5.8, and is also true on the 5.6 → 5.8 upgrade itself, where the
# volume is empty and reads as "no major to migrate".
#
# "empty" still restores. The volume holds nothing, but the live cot rows are
# in the container's writable layer (that is the 5.6 defect), `compose down`
# destroys them, and the archive's cot.sql is then the only copy.
upgrade_cot_plan() {
  local state="$1" skip_cot="$2" remove=false restore=false
  [ "$state" = absent ] || remove=true
  if [ "$remove" = true ] && [ "$skip_cot" = false ]; then
    restore=true
  fi
  printf '%s %s' "$remove" "$restore"
}

# upgrade_cot_summary <cot-restored> <skip-cot>
#
# The summary's CoT line. "migrated" is a claim about rows that exist, so only
# a restore that actually ran and succeeded earns it.
upgrade_cot_summary() {
  local restored="$1" skip_cot="$2"
  if [ "$restored" = true ]; then
    printf 'CoT history: migrated'
  elif [ "$skip_cot" = true ]; then
    printf 'CoT history: DISCARDED (--skip-cot)'
  else
    printf 'CoT history: unaffected (tak-db-data was not recreated)'
  fi
}

# upgrade_plan <app-current> <app-target> <cot-current> <cot-target> <skip-cot>
#
# Prints "<migrate-app-db> <cot-major-stale> <nothing-to-migrate>", each
# true/false. This decides whether there is any *version* work to do; what
# happens to the CoT history is upgrade_cot_plan's call, not this one's.
#
# --skip-cot only ever suppresses the *cot* restore; the app-db restore is
# decided by the majors alone. It also disables the "nothing to migrate" exit,
# because tak-db-data is recreated unconditionally and that is the whole point
# of asking for --skip-cot.
upgrade_plan() {
  local app_current="$1" app_target="$2" cot_current="$3" cot_target="$4" skip_cot="$5"
  local migrate_app=false migrate_cot=false nothing=false

  upgrade_needs_migration "$app_current" "$app_target" && migrate_app=true
  if [ "$skip_cot" = false ]; then
    upgrade_needs_migration "$cot_current" "$cot_target" && migrate_cot=true
  fi
  if [ "$migrate_app" = false ] && [ "$migrate_cot" = false ] && [ "$skip_cot" = false ]; then
    nothing=true
  fi
  printf '%s %s %s' "$migrate_app" "$migrate_cot" "$nothing"
}

# upgrade_parse_args "$@"
#
# Sets SKIP_COT and ASSUME_YES. Returns 2 on an unrecognised argument.
upgrade_parse_args() {
  SKIP_COT=false
  ASSUME_YES=false
  while [ $# -gt 0 ]; do
    case "$1" in
      --skip-cot) SKIP_COT=true; shift ;;
      --yes|-y)   ASSUME_YES=true; shift ;;
      *) echo "Unknown argument: $1" >&2
         echo "Usage: upgrade.sh [--skip-cot] [--yes]" >&2
         return 2 ;;
    esac
  done
  return 0
}

# upgrade_compose_args <env-file> [<colon-separated-compose-files>]
#
# Prints one `docker compose` argument per line. --env-file is always last and
# always present: bare `docker compose` would read ./.env, which is not
# necessarily the env file this run targets. Because of that the caller's array
# is never empty, so "${COMPOSE_ARGS[@]}" is safe under `set -u` on bash 3.2.
upgrade_compose_args() {
  local env_file="$1" files="${2:-}" old_ifs f
  if [ -n "$files" ]; then
    old_ifs="$IFS"
    IFS=:
    for f in $files; do
      printf -- '-f\n%s\n' "$f"
    done
    IFS="$old_ifs"
  fi
  printf -- '--env-file\n%s\n' "$env_file"
}

# upgrade_project_name_from_config
#
# Reads `docker compose config --format json` on stdin and prints its top-level
# "name" — the project name Compose itself will use.
#
# Deriving the name here instead would mean reimplementing compose-go's
# NormalizeProjectName, and every past attempt has diverged: `tr -cd
# '[:alnum:]'` drops the `-` and `_` that Compose keeps, so a repo in
# `Fast-TAK_Probe/` looks up `fasttakprobe_app-db-data` while Compose created
# `fast-tak_probe_app-db-data`. Asking Compose also picks up
# COMPOSE_PROJECT_NAME wherever it was set — shell *or* env file.
#
# Returns 1 when the name cannot be read, so the caller can fail loudly rather
# than guess and delete some other project's volume.
upgrade_project_name_from_config() {
  python3 -c '
import json
import sys

try:
    doc = json.load(sys.stdin)
except Exception:
    sys.exit(1)
name = doc.get("name") if isinstance(doc, dict) else None
if not isinstance(name, str) or not name:
    sys.exit(1)
sys.stdout.write(name)
'
}

# upgrade_archive_name_from_output
#
# Reads `python -m app.backup run` output on stdin and prints the archive
# filename from its `ok: <filename> (<n> bytes)` line (cli.py:_cmd_run).
#
# The alternative — newest *.age by mtime — restores a stale archive without a
# word if anything ever touched its mtime, and on an empty directory `xargs`
# still runs `ls` once, so the newest entry of the *parent* becomes the answer.
upgrade_archive_name_from_output() {
  tr -d '\r' \
    | sed -n 's/^ok: \(fasttak-backup-.*\.age\) ([0-9][0-9]* bytes)$/\1/p' \
    | tail -1
}

# upgrade_abs_path <path>
#
# Makes a path absolute against the repo root. BACKUP_DIR is documented as
# `./backups` in .env.example, and a relative path is confusing in the summary
# and wrong for `df` if the caller's cwd ever moves.
upgrade_abs_path() {
  local p="$1"
  case "$p" in
    /*) ;;
    ./*) p="${REPO_DIR}/${p#./}" ;;
    *)  p="${REPO_DIR}/${p}" ;;
  esac
  printf '%s' "$p"
}

if [ "${FASTAK_UPGRADE_LIB_ONLY:-}" = "1" ]; then
  # shellcheck disable=SC2317  # reached when executed rather than sourced
  return 0 2>/dev/null || exit 0
fi

# ── Argument parsing ─────────────────────────────────────────────────────
upgrade_parse_args "$@" || exit 2

cd "$REPO_DIR" || exit 1

[ -r "$SCRIPT_DIR/lib-env.sh" ] || {
  echo "ERROR: $SCRIPT_DIR/lib-env.sh is missing; cannot read the .env file." >&2
  exit 1
}
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
# Compose derives the project name from the directory of the *first* -f file,
# so pointing it outside the repo yields a different project than the operator
# path does. That is fine here — the name is read back from Compose below
# rather than assumed — but it does mean the two target different stacks.
ENV_FILE="${FASTAK_ENV_FILE:-$REPO_DIR/.env}"

COMPOSE_ARGS=()
while IFS= read -r _arg; do
  COMPOSE_ARGS+=("$_arg")
done < <(upgrade_compose_args "$ENV_FILE" "${FASTAK_COMPOSE_FILES:-}")

compose() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }

fail() { echo "" >&2; echo "ERROR: $*" >&2; exit 1; }

# fail_after_restore_point <message>
#
# Every failure past the first `docker volume rm`. The live data is gone by
# then and the archive is the only copy, so name it and say how to use it.
fail_after_restore_point() {
  echo "" >&2
  echo "ERROR: $*" >&2
  echo "" >&2
  echo "  The database volumes may already have been removed. The only copy" >&2
  echo "  of the pre-upgrade data is the backup archive:" >&2
  echo "" >&2
  echo "    ${ARCHIVE:-<no archive>}" >&2
  echo "" >&2
  echo "  Restore it with tests-integration/restore.sh (the canonical restore" >&2
  echo "  procedure — see docs/backup-and-restore.md, \"Restoring to a fresh" >&2
  echo "  host\"). Do not re-run this script until the data is back: it would" >&2
  echo "  take a new backup of the now-empty databases." >&2
  exit 1
}

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          FastTAK Upgrade                 ║"
echo "╚══════════════════════════════════════════╝"

# ── 1. Preflight ─────────────────────────────────────────────────────────
echo ""
echo "▸ Preflight"
for _cmd in docker age python3; do
  command -v "$_cmd" >/dev/null 2>&1 \
    || fail "'$_cmd' is required but not on PATH. Install it and re-run."
done
# Distinguishes "Docker is unreachable" from "the volume does not exist"; the
# volume probes below read an absent volume as a fresh install.
docker info >/dev/null 2>&1 \
  || fail "cannot talk to the Docker daemon. Start Docker and re-run."

[ -f "$ENV_FILE" ] || fail ".env not found. Run ./setup.sh <bundle.zip> first."
"$SCRIPT_DIR/ensure-secrets.sh" "$ENV_FILE" || fail "could not provision required secrets"
"$SCRIPT_DIR/check-env.sh" "$ENV_FILE" || exit 1
echo "  .env validated."

# The project name decides which volumes get deleted, so take Compose's answer
# rather than a local guess. See upgrade_project_name_from_config.
COMPOSE_ERR="$(mktemp)" || fail "could not create a temporary file (is \$TMPDIR writable?)"
COMPOSE_CONFIG="$(compose config --format json 2>"$COMPOSE_ERR")" || {
  echo "" >&2
  echo "ERROR: \`docker compose config\` failed; cannot determine the compose project name." >&2
  echo "" >&2
  sed 's/^/  /' "$COMPOSE_ERR" >&2
  rm -f "$COMPOSE_ERR"
  exit 1
}
rm -f "$COMPOSE_ERR"
PROJECT="$(printf '%s' "$COMPOSE_CONFIG" | upgrade_project_name_from_config)" \
  || fail "\`docker compose config\` emitted no project name; refusing to guess which volumes to delete."
unset COMPOSE_CONFIG
echo "  Compose project: $PROJECT"

# upgrade_volume_for <compose-volume-name>
#
# Resolves the real volume name. Compose labels every volume it creates, so
# the label filter is authoritative; the `<project>_<volume>` construction is
# the fallback for a volume that predates those labels.
upgrade_volume_for() {
  local name found
  found="$(docker volume ls -q \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter "label=com.docker.compose.volume=$1" 2>/dev/null | head -1)"
  if [ -n "$found" ]; then
    printf '%s' "$found"
    return 0
  fi
  name="${PROJECT}_$1"
  printf '%s' "$name"
}

APP_DB_VOLUME="$(upgrade_volume_for app-db-data)"
TAK_DB_VOLUME="$(upgrade_volume_for tak-db-data)"

# ── 2. Plan ──────────────────────────────────────────────────────────────
echo ""
echo "▸ Planning"
# A probe failure is a Docker problem, not a data problem: say which, because
# the fix (pull the image / start the daemon) is nothing like "your volume is
# corrupt". See upgrade_volume_pg_major for the three outcomes.
probe_failed() {
  echo "" >&2
  echo "ERROR: could not read the PostgreSQL major from $1." >&2
  echo "" >&2
  echo "  The probe (\`docker run postgres:18-alpine\`) did not run. The daemon" >&2
  echo "  may have gone away, or the image could not be pulled: 18 arrives with" >&2
  echo "  this release, so on an air-gapped host it has to be loaded or pulled" >&2
  echo "  first — \`docker pull postgres:18-alpine\`." >&2
  echo "" >&2
  echo "  This says nothing about the volume's data. Nothing has been changed." >&2
  exit 1
}

APP_DB_CURRENT="$(upgrade_volume_pg_major "$APP_DB_VOLUME")"
case "$?" in
  0) APP_DB_STATE="$APP_DB_CURRENT" ;;
  1) APP_DB_STATE=absent ;;
  2) APP_DB_STATE=empty ;;
  *) probe_failed "$APP_DB_VOLUME" ;;
esac
TAK_DB_CURRENT="$(upgrade_volume_pg_major "$TAK_DB_VOLUME")"
case "$?" in
  0) TAK_DB_STATE="$TAK_DB_CURRENT" ;;
  1) TAK_DB_STATE=absent ;;
  2) TAK_DB_STATE=empty ;;
  *) probe_failed "$TAK_DB_VOLUME" ;;
esac

# upgrade_describe_state <state> — the volume's shape, in the operator's terms.
upgrade_describe_state() {
  case "$1" in
    absent) printf 'no volume yet' ;;
    empty)  printf 'volume holds no data directory (pre-5.8 layout)' ;;
    *)      printf 'PostgreSQL %s' "$1" ;;
  esac
}

echo "  app-db:       $(upgrade_describe_state "$APP_DB_STATE") → ${APP_DB_TARGET_MAJOR}"
echo "  tak-database: $(upgrade_describe_state "$TAK_DB_STATE") → ${TAK_DB_TARGET_MAJOR}"

read -r MIGRATE_APP_DB COT_MAJOR_STALE NOTHING_TO_MIGRATE <<EOF
$(upgrade_plan "$APP_DB_CURRENT" "$APP_DB_TARGET_MAJOR" \
    "$TAK_DB_CURRENT" "$TAK_DB_TARGET_MAJOR" "$SKIP_COT")
EOF

# What happens to tak-db-data, and therefore to the CoT history. Decided by
# the volume's shape and --skip-cot alone — never by the major comparison
# above, which says nothing about what `docker volume rm` is about to delete.
read -r REMOVE_TAK_VOLUME RESTORE_COT <<EOF
$(upgrade_cot_plan "$TAK_DB_STATE" "$SKIP_COT")
EOF

if [ "$NOTHING_TO_MIGRATE" = true ]; then
  echo ""
  echo "  Nothing to migrate. Start the stack with ./start.sh"
  exit 0
fi

if [ "$MIGRATE_APP_DB" = false ] && [ "$COT_MAJOR_STALE" = false ] \
   && [ "$REMOVE_TAK_VOLUME" = true ]; then
  # Reached only under --skip-cot: no major changed, so nothing *needs* moving,
  # but tak-db-data is recreated regardless. On an already-migrated stack that
  # discards whatever CoT has accumulated since.
  echo ""
  echo "  ⚠  Nothing needs migrating — no database major changed."
  echo "     --skip-cot will still recreate tak-db-data, so the"
  echo "     CoT history accumulated since the last upgrade WILL BE LOST."
  echo "     Re-run without --skip-cot to exit without touching anything."
fi

# ── 3. Preflight: the services this upgrade has to talk to ───────────────
# The backup is taken through the running stack, so the stack has to be up.
# That is not a given at this point: on 18 against a 15 volume app-db will not
# start, and monitor depends on it transitively, so a reboot or a
# `compose down` before running this leaves no way to take the backup.
upgrade_service_running() {
  local cid
  cid="$(compose ps -q "$1" 2>/dev/null | head -1)"
  [ -n "$cid" ] || return 1
  [ "$(docker inspect --format='{{.State.Running}}' "$cid" 2>/dev/null)" = "true" ]
}

# tak-database is required unconditionally, --skip-cot or not: the backup dumps
# cot on every run (monitor/app/backup/runner.py _database_dsns), so with
# tak-database down the run would pass preflight and then die at "backup
# failed" — the catch-22 this check exists to explain.
REQUIRED_SERVICES="monitor tak-database"

MISSING_SERVICES=""
for _svc in $REQUIRED_SERVICES; do
  upgrade_service_running "$_svc" || MISSING_SERVICES="$MISSING_SERVICES $_svc"
done
if [ -n "$MISSING_SERVICES" ]; then
  echo "" >&2
  echo "ERROR: these services are not running:${MISSING_SERVICES}" >&2
  echo "" >&2
  echo "  This upgrade restores from a backup it takes through the running" >&2
  echo "  stack, so the stack must be up first. Bring it up with ./start.sh" >&2
  echo "" >&2
  echo "  If app-db will not start, its volume is still on PostgreSQL" >&2
  echo "  ${APP_DB_CURRENT:-15} and the new image refuses it. Check out the" >&2
  echo "  previous release (\`git checkout <previous-tag>\`), start the stack," >&2
  echo "  then \`git checkout\` back and re-run this script." >&2
  echo "" >&2
  echo "  --skip-cot does NOT avoid this: it skips the CoT *restore*, not the" >&2
  echo "  backup." >&2
  exit 1
fi

# ── 4. Disk-headroom preflight for the CoT database ──────────────────────
# pg_dump plus the restored copy need room alongside the original. tak.gov's
# own db-utils/upgrade-db.sh requires 1.5x; mirror it rather than inventing a
# number. Aborting here beats filling the disk mid-restore.
#
# BACKUP_DIR has to be read from the env file, not the environment: nothing on
# the operator path exports it, so reading `$BACKUP_DIR` would measure the
# repo's filesystem no matter what .env says.
BACKUP_DIR_RESOLVED="$(env_get "$ENV_FILE" BACKUP_DIR)"
BACKUP_DIR_RESOLVED="$(upgrade_abs_path "${BACKUP_DIR_RESOLVED:-$REPO_DIR/backups}")"

if [ "$RESTORE_COT" = true ]; then
  echo ""
  echo "▸ Checking disk headroom for the CoT database"
  # shellcheck disable=SC2016  # expands inside the container, not on the host
  COT_BYTES="$(compose exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot -tAc "SELECT pg_database_size('"'"'cot'"'"');"' \
    2>/dev/null | tr -d '[:space:]')"
  if [ -n "$COT_BYTES" ] && [ "$COT_BYTES" -gt 0 ] 2>/dev/null; then
    COT_MB=$((COT_BYTES / 1024 / 1024))
    NEED_MB=$(( (COT_MB * 3) / 2 ))
    mkdir -p "$BACKUP_DIR_RESOLVED" 2>/dev/null
    AVAIL_MB="$(df -m "$BACKUP_DIR_RESOLVED" 2>/dev/null | awk 'NR==2 {print $4}')"
    case "$AVAIL_MB" in
      ''|*[!0-9]*) fail "could not measure free space on $BACKUP_DIR_RESOLVED (df gave '${AVAIL_MB}'). Refusing to start a migration whose headroom is unknown." ;;
    esac
    echo "  cot database:    ${COT_MB} MB"
    echo "  required free:   ${NEED_MB} MB (1.5x, matching tak.gov's upgrade-db.sh)"
    echo "  available:       ${AVAIL_MB} MB (on ${BACKUP_DIR_RESOLVED})"
    if [ "$AVAIL_MB" -lt "$NEED_MB" ]; then
      fail "not enough free space to migrate the CoT database. Free at least $((NEED_MB - AVAIL_MB)) MB and re-run."
    fi
    # This measures the backup filesystem only. The decrypted plain-SQL dump
    # lands in $TMPDIR (often a tmpfs, and often larger than the live database
    # because plain SQL is uncompressed), and the restored copy lands in
    # Docker's data root. Either can fill independently of this check.
    echo ""
    echo "  NOTE: \$TMPDIR (the extracted dump) and Docker's data root (the"
    echo "        restored copy) are NOT covered by this check."
    echo ""
    echo "  NOTE: the stack is DOWN for the whole migration. A multi-GB cot"
    echo "        database can take a long time to dump and restore. See"
    echo "        https://github.com/pounde/FastTAK/issues/98"
  else
    fail "tak-database is running but the cot size query returned nothing. Check \`docker compose logs tak-database\`, or re-run with --skip-cot to discard the CoT history instead of migrating it (the backup is still taken either way)."
  fi
fi

# ── 5. Confirm ───────────────────────────────────────────────────────────
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

# ── 6. Back up ───────────────────────────────────────────────────────────
echo ""
echo "▸ Taking a backup"
# `tee -a /dev/stderr` streams the dump's progress while still capturing it for
# the archive-name parse below. A multi-GB cot database can take many minutes,
# and a capture-only pipeline makes that a silent wait with no sign of life.
# `-a` matters: plain `tee /dev/stderr` opens /dev/stderr with O_TRUNC, and
# under `./upgrade.sh >log 2>log` that reopen has its own offset starting at 0
# — it overwrites whatever stdout already wrote to the same file instead of
# appending after it. `-a` opens for append instead, avoiding that.
# `set -o pipefail` keeps the backup's own exit status, not tee's.
BACKUP_OUTPUT="$(compose exec -T monitor python -m app.backup run | tee -a /dev/stderr)" \
  || fail "backup failed — refusing to continue. Everything after this point is destructive."

ARCHIVE_NAME="$(printf '%s\n' "$BACKUP_OUTPUT" | upgrade_archive_name_from_output)"
[ -n "$ARCHIVE_NAME" ] \
  || fail "the backup reported success but printed no archive name. Expected a line like 'ok: fasttak-backup-....age (N bytes)'."
ARCHIVE="${BACKUP_DIR_RESOLVED}/${ARCHIVE_NAME}"
[ -f "$ARCHIVE" ] \
  || fail "the backup reported $ARCHIVE_NAME but it is not at $ARCHIVE. Does BACKUP_DIR in $ENV_FILE match the monitor's mount?"
echo "  Archive: $ARCHIVE"

KEYFILE="${BACKUP_DIR_RESOLVED}/.age-identity"
[ -f "$KEYFILE" ] || fail "age identity not found at $KEYFILE"

WORK="$(mktemp -d)" || fail "could not create a temporary directory (is \$TMPDIR writable?)"
trap 'rm -rf "$WORK"' EXIT
age -d -i "$KEYFILE" "$ARCHIVE" | tar xz -C "$WORK" || fail "could not decrypt $ARCHIVE"
[ -s "$WORK/MANIFEST.json" ] || fail "archive $ARCHIVE has no MANIFEST.json — it is not a FastTAK backup."
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$WORK/MANIFEST.json" >/dev/null 2>&1 \
  || fail "archive $ARCHIVE has an unreadable MANIFEST.json."
for db in $APP_DB_DATABASES; do
  [ -s "$WORK/postgres/${db}.sql" ] || fail "archive is missing postgres/${db}.sql"
done
# Checked whenever a cot restore is going to be required — i.e. whenever
# tak-db-data is about to be removed without --skip-cot. Tying this to the
# major comparison instead would skip the check on exactly the runs that need
# it, and the missing dump would surface after the data was already gone.
if [ "$RESTORE_COT" = true ]; then
  [ -s "$WORK/postgres/cot.sql" ] \
    || fail "archive is missing postgres/cot.sql, but tak-db-data is about to be recreated — the CoT history would be lost. Re-run with --skip-cot to discard it deliberately."
fi
echo "  Archive verified."

# ── 7. Stop the stack ────────────────────────────────────────────────────
echo ""
echo "▸ Stopping the stack"
compose down || fail "docker compose down failed"

# ── 8. Recreate volumes whose major changed ──────────────────────────────
# Past this point the live data is gone: use fail_after_restore_point.
echo ""
echo "▸ Recreating database volumes"
if [ "$MIGRATE_APP_DB" = true ]; then
  docker volume rm "$APP_DB_VOLUME" >/dev/null \
    || fail_after_restore_point "could not remove $APP_DB_VOLUME"
  echo "  Removed $APP_DB_VOLUME (was PostgreSQL ${APP_DB_CURRENT})"
fi
# tak-db-data is recreated whenever it exists: TAK 5.8 ships a build-time
# initdb, and an empty named volume is populated from the image on first use. A
# pre-existing volume is not.
#
# TAK_DB_VOLUME_REMOVED is the fact the cot restore turns on — not the plan,
# and not the major comparison. Whatever was on that volume is gone from here.
TAK_DB_VOLUME_REMOVED=false
if [ "$REMOVE_TAK_VOLUME" = true ]; then
  docker volume rm "$TAK_DB_VOLUME" >/dev/null \
    || fail_after_restore_point "could not remove $TAK_DB_VOLUME"
  TAK_DB_VOLUME_REMOVED=true
  echo "  Removed $TAK_DB_VOLUME ($(upgrade_describe_state "$TAK_DB_STATE"))"
fi

# ── 9. Start the database services on the new majors ─────────────────────
echo ""
echo "▸ Starting database services"
compose up -d --build tak-database app-db \
  || fail_after_restore_point "database services failed to start"

echo "  Waiting for databases..."

# `xargs -r` is GNU-only and this script also runs on macOS during development,
# so the container id is captured into a variable and tested instead.
#
# `head -1` matches upgrade_service_running: `compose ps -q` prints one id per
# replica, and two ids would make the inspect below fail on a malformed
# argument rather than report the first container's health.
service_health() {
  local cid status
  cid="$(compose ps -q "$1" 2>/dev/null | head -1)"
  [ -n "$cid" ] || { printf 'missing'; return 0; }
  status="$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null)"
  printf '%s' "${status:-unknown}"
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
  fail_after_restore_point "databases did not become healthy (app-db=${app_ok:-?} tak-database=${tak_ok:-?})"
fi

# ── 10. Restore ──────────────────────────────────────────────────────────
# Dumps are --format=plain without --clean, so each target database is dropped
# and recreated first. -h localhost forces TCP; peer auth on the Unix socket
# has no mapping for martiuser/fastak. This mirrors tests-integration/restore.sh.
#
# -v ON_ERROR_STOP=1 on every psql that reads a dump. Without it psql reports
# every statement error and still exits 0, so `|| fail` catches nothing short
# of a dropped connection — a restore that errored on every row would print
# "Upgrade Complete" over empty databases.
if [ "$MIGRATE_APP_DB" = true ]; then
  echo ""
  echo "▸ Restoring app-db databases"
  for db in $APP_DB_DATABASES; do
    echo "  $db"
    compose exec -T app-db \
      sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -v ON_ERROR_STOP=1 -h localhost -U fastak -d postgres -c \"DROP DATABASE IF EXISTS $db WITH (FORCE);\" -c \"CREATE DATABASE $db OWNER fastak;\"" \
      || fail_after_restore_point "could not recreate database $db"
    compose exec -T app-db \
      sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -v ON_ERROR_STOP=1 -h localhost -U fastak -d $db" \
      < "$WORK/postgres/${db}.sql" || fail_after_restore_point "restore of $db failed"
  done
fi

# The restore condition is what was actually destroyed, ANDed with --skip-cot:
# the volume is gone, so whatever cot held is gone with it. See upgrade_cot_plan.
COT_RESTORED=false
if [ "$TAK_DB_VOLUME_REMOVED" = true ] && [ "$SKIP_COT" = false ]; then
  echo ""
  echo "▸ Restoring the cot database (this may take a while)"
  # cot is SQL_ASCII on TAK Server (monitor/app/backup/manifest.py documents
  # this — SHOW server_version comes back as bytes from it). The encoding is
  # pinned explicitly here rather than left to inherit whatever template1's
  # default is on the target cluster — the restore target is tak-database's
  # own cluster, which is itself SQL_ASCII, but pinning it makes the restore
  # correct independent of the target cluster's default, defensively. The
  # archive's MANIFEST.json records server versions but not encodings
  # (manifest.build), so the encoding can't be read back from the archive
  # either. TEMPLATE template0 is required: a non-matching encoding cannot be
  # copied from template1.
  # shellcheck disable=SC2016  # expands inside the container, not on the host
  compose exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -v ON_ERROR_STOP=1 -h localhost -U martiuser -d postgres -c "DROP DATABASE IF EXISTS cot WITH (FORCE);" -c "CREATE DATABASE cot OWNER martiuser ENCODING '"'"'SQL_ASCII'"'"' TEMPLATE template0;"' \
    || fail_after_restore_point "could not recreate database cot"
  # shellcheck disable=SC2016
  compose exec -T tak-database \
    sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -v ON_ERROR_STOP=1 -h localhost -U martiuser -d cot' \
    < "$WORK/postgres/cot.sql" || fail_after_restore_point "restore of cot failed"
  COT_RESTORED=true
elif [ "$SKIP_COT" = true ]; then
  echo ""
  echo "▸ CoT history NOT migrated (--skip-cot). TAK Server starts with an empty cot database."
fi

# ── 11. Bring the rest up ────────────────────────────────────────────────
echo ""
echo "▸ Starting the rest of the stack"
compose up -d --build --remove-orphans \
  || fail_after_restore_point "stack failed to start"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          Upgrade Complete                ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Backup taken before the upgrade: $ARCHIVE"
# Driven by COT_RESTORED — the restore that ran and returned 0 — so the line
# can never claim a migration that did not happen.
echo "  $(upgrade_cot_summary "$COT_RESTORED" "$SKIP_COT")"
echo ""
echo "  Verify:  docker compose ps"
echo ""

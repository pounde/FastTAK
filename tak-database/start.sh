#!/bin/bash
# FastTAK tak-database entrypoint.
#
# Wraps TAK Server's own db-utils/configureInDocker.sh with three things the
# vendor image does not do:
#   1. Set the CoreConfig DB password before the vendor's setup reads it.
#   2. Apply FastTAK's PostgreSQL tuning (DD-020) and disable WAL archiving.
#   3. Verify (2) actually took effect once the server is up, and fail if not.
#
# On (2): TAK 5.6 exposed tuning as pg_ctl -o flags, and this script used to
# splice FastTAK's in with sed. TAK 5.8 dropped those flags entirely, so that
# sed matched nothing and exited 0 — the tuning disappeared with no error.
# 5.8 moved tuning into db-utils/postgresql.conf, which takserver-setup-db.sh
# copies over the live config — but only on the very first boot. That script
# checks whether the `cot` database already exists and, if it does, prompts
# `read -p "Type 'erase' ..."` before touching it. This container has no TTY,
# so the read hits EOF and the script exits 1, dozens of lines before the copy.
# Every boot after the first therefore leaves the live config untouched, and a
# changed PG_* value in .env would be written somewhere the server never reads.
#
# So the block is written to both files, before the vendor entrypoint starts:
#
#   db-utils/postgresql.conf   what the vendor copies into PGDATA on first boot
#   $PGDATA/postgresql.conf    what the server reads on every boot after that
#
# The two paths converge: on first boot the vendor's copy already carries the
# block. Step (3) checks the running server, because "the tuning reached the
# server" is the only claim that actually matters.

set -uo pipefail

TAK_DIR="${FASTAK_TAK_DIR:-/opt/tak}"
PG_CONF="${TAK_DIR}/db-utils/postgresql.conf"
VENDOR_ENTRYPOINT="${TAK_DIR}/db-utils/configureInDocker.sh"
GUARD="${FASTAK_GUARD:-/opt/fastak/check-pgdata-persistent.sh}"
BEGIN_MARKER="# FASTTAK-TUNING-BEGIN"
END_MARKER="# FASTTAK-TUNING-END"

PG_AUTOVACUUM_SCALE_FACTOR="${PG_AUTOVACUUM_SCALE_FACTOR:-0.05}"
PG_AUTOVACUUM_COST_LIMIT="${PG_AUTOVACUUM_COST_LIMIT:-1000}"
PG_MAINTENANCE_WORK_MEM="${PG_MAINTENANCE_WORK_MEM:-256MB}"

# A connection that hangs must not consume the verification deadline. Not
# exported: this is scoped to FastTAK's own psql calls (see psql_show and
# psql_stderr below) so it doesn't leak into the vendor entrypoint and TAK's
# own psql/pg_isready calls.
PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-5}"

# ── 0. Validate the tunables ─────────────────────────────────────────────
# These are interpolated into postgresql.conf, which is line-oriented and
# quotes string values with '. A value carrying a newline or a single quote
# can close the quoting and append configuration lines of its own choosing.
# Same reasoning as env_reject_newline in scripts/ensure-secrets.sh.
reject_unsafe_value() {
  case "$2" in
    *"'"* | *"
"*)
      cat >&2 <<EOF
[tak-database] ERROR: refusing to apply $1 — the value contains a newline or a
single quote, either of which would let it inject arbitrary postgresql.conf
lines. Fix the value in .env.
EOF
      exit 1
      ;;
  esac
}

reject_unsafe_value PG_AUTOVACUUM_SCALE_FACTOR "$PG_AUTOVACUUM_SCALE_FACTOR"
reject_unsafe_value PG_AUTOVACUUM_COST_LIMIT "$PG_AUTOVACUUM_COST_LIMIT"
reject_unsafe_value PG_MAINTENANCE_WORK_MEM "$PG_MAINTENANCE_WORK_MEM"

# ── Shared temp-file rewrite helpers ─────────────────────────────────────
# Both the CoreConfig password patch and the tuning block below rewrite a
# file via `<transform> target > tmp && mv tmp target` rather than `sed -i`:
# BSD sed (macOS) requires an argument to -i and GNU sed forbids one, and this
# script is executed directly by the unit tests on the developer's host as
# well as inside the container. An unchecked version of that pattern is the
# exact silent failure this script exists to remove — a failed write leaves
# the old file in place, prints nothing, and exits 0. Every step here is
# checked instead.

# seed_tmp <target> <tmp>
#
# Seeds <tmp> from <target> via `cp -p` so it inherits target's mode and
# owner — `mv` would otherwise stamp the temp file's onto the target.
seed_tmp() {
  local target="$1" tmp="$2"
  if ! cp -p "$target" "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    echo "[tak-database] ERROR: cannot create ${tmp} — ${target} is not rewritable." >&2
    return 1
  fi
}

# commit_tmp <target> <tmp>
#
# Atomically swaps <tmp> into place over <target>, once its content has been
# written by the caller.
commit_tmp() {
  local target="$1" tmp="$2"
  if ! mv "$tmp" "$target"; then
    rm -f "$tmp"
    echo "[tak-database] ERROR: failed to replace ${target}." >&2
    return 1
  fi
}

# ── 1. CoreConfig DB password ────────────────────────────────────────────
# tak-database starts before init-config and needs the password immediately.
patch_coreconfig_password() {
  local target="$1"
  local tmp="${target}.fastak.tmp"

  seed_tmp "$target" "$tmp" || return 1

  if ! sed '/<connection /s|password="[^"]*"|password="'"${TAK_DB_PASSWORD}"'"|' "$target" > "$tmp"; then
    rm -f "$tmp"
    echo "[tak-database] ERROR: failed to rewrite the DB password in ${target}." >&2
    return 1
  fi

  commit_tmp "$target" "$tmp" || return 1
}

if [ -n "${TAK_DB_PASSWORD:-}" ]; then
  for f in "${TAK_DIR}/CoreConfig.xml" "${TAK_DIR}/CoreConfig.example.xml"; do
    [ -f "$f" ] || continue
    patch_coreconfig_password "$f" || exit 1
  done
fi

# ── 2. Tuning + WAL archiving ────────────────────────────────────────────
tuning_block() {
  cat <<EOF
${BEGIN_MARKER}
# Appended by tak-database/start.sh — do not edit; rewritten on every start.
# postgresql.conf is last-wins, so these override the vendor values above.
#
# Autovacuum is tuned for TAK Server's high-write CoT workload (DD-020).
# Override via .env; set to PostgreSQL defaults (0.2 / 200 / 64MB) to disable.
autovacuum_vacuum_scale_factor = ${PG_AUTOVACUUM_SCALE_FACTOR}
autovacuum_vacuum_cost_limit = ${PG_AUTOVACUUM_COST_LIMIT}
maintenance_work_mem = '${PG_MAINTENANCE_WORK_MEM}'
#
# WAL archiving off (DD-053). The vendor sets archive_mode = on with an
# archive_command copying to /var/lib/postgresql/archivedir, which no TAK
# bundle creates. Postgres will not recycle a WAL segment until its archive
# command succeeds, so every segment is retained and pg_wal grows until the
# disk fills. FastTAK's backups are logical (pg_dump) and consume no
# archives, so archiving is pure cost.
archive_mode = off
${END_MARKER}
EOF
}

# apply_tuning <target>
#
# Idempotent: strips any previous FastTAK block, then appends a fresh one.
# Only the text between the two markers is dropped, so anything appended after
# the block by a later start (or by hand) survives.
#
# Uses the seed_tmp/commit_tmp helpers above. Every step is checked: an
# unwritable directory used to short-circuit the pattern, leaving the old
# block in place while the append added a second one and the script still
# reported success.
apply_tuning() {
  local target="$1"
  local tmp="${target}.fastak.tmp"

  seed_tmp "$target" "$tmp" || return 1

  if ! awk -v b="$BEGIN_MARKER" -v e="$END_MARKER" '
         $0 == b { skip = 1; next }
         $0 == e { skip = 0; next }
         !skip   { print }
       ' "$target" > "$tmp"; then
    rm -f "$tmp"
    echo "[tak-database] ERROR: failed to strip the previous FastTAK block from ${target}." >&2
    return 1
  fi

  commit_tmp "$target" "$tmp" || return 1

  if ! tuning_block >> "$target"; then
    echo "[tak-database] ERROR: failed to append FastTAK's tuning to ${target}." >&2
    return 1
  fi
}

if [ ! -f "$PG_CONF" ]; then
  cat >&2 <<EOF
[tak-database] ERROR: ${PG_CONF} not found.

FastTAK applies its PostgreSQL tuning by appending to this file, which
TAK Server's takserver-setup-db.sh installs as the live configuration on the
first boot of a fresh database. Its absence means the release layout changed,
and starting anyway would run with untuned autovacuum and WAL archiving left
on — the exact silent failure this check exists to prevent.
EOF
  exit 1
fi

if [ -z "${PGDATA:-}" ]; then
  cat >&2 <<EOF
[tak-database] ERROR: PGDATA is not set.

The vendor only installs db-utils/postgresql.conf on the first boot of a fresh
database, so FastTAK must write its tuning to the live configuration as well.
Without PGDATA that write cannot happen, and a changed PG_* value in .env would
silently never reach the server. The image sets PGDATA; its absence means the
image changed.
EOF
  exit 1
fi

LIVE_CONF="${PGDATA}/postgresql.conf"
if [ ! -f "$LIVE_CONF" ]; then
  cat >&2 <<EOF
[tak-database] ERROR: ${LIVE_CONF} not found.

The hardened DB image runs initdb at build time and Docker seeds an empty
named volume from the image, so the live configuration is expected to exist
before this entrypoint runs. Its absence means that assumption has broken.
Continuing would leave the running server on whatever configuration it finds,
which is the silent failure this check exists to prevent.
EOF
  exit 1
fi

apply_tuning "$PG_CONF" || exit 1
apply_tuning "$LIVE_CONF" || exit 1

echo "[tak-database] Tuning applied: scale_factor=${PG_AUTOVACUUM_SCALE_FACTOR} cost_limit=${PG_AUTOVACUUM_COST_LIMIT} work_mem=${PG_MAINTENANCE_WORK_MEM} archive_mode=off"
echo "[tak-database] Targets: ${PG_CONF} (vendor copies on first boot), ${LIVE_CONF} (live)"

# Unit tests exercise the config injection without a PostgreSQL server.
if [ "${FASTAK_INJECT_ONLY:-}" = "1" ]; then
  exit 0
fi

# ── 3. Start the vendor entrypoint, then verify ──────────────────────────
# configureInDocker.sh ends in `tail -f /dev/null` and never returns, so it
# runs in the background and this script waits on it after verifying.
"$VENDOR_ENTRYPOINT" &
VENDOR_PID=$!

# The postmaster is started by pg_ctl and is a detached daemon, not a child of
# this script — killing the vendor shell leaves it running until the container
# teardown SIGKILLs it. PGDATA is genuinely persistent now, so that means crash
# recovery on every stop. Shut it down explicitly on every exit path.
# stop_postgres
#
# Stops the postmaster and returns pg_ctl's exit status, so callers can warn
# rather than assume a clean shutdown happened. Discarding that status was the
# same "claims success without having verified it" defect the rest of this
# script exists to remove. pg_ctl's stderr is printed on failure so a missing
# binary and a stop timeout don't look identical.
stop_postgres() {
  local err status
  err="$(pg_ctl -D "$PGDATA" stop -m fast 2>&1 >/dev/null)"
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "[tak-database] pg_ctl stop: ${err:-(no output)}" >&2
  fi
  return "$status"
}

stop_vendor() {
  kill "$VENDOR_PID" 2>/dev/null
}

# As PID 1, this script would otherwise ignore SIGTERM outright rather than
# being killed by it — Linux does not apply a terminate-by-default signal
# disposition to PID 1 unless a handler is installed. Without this trap,
# `docker stop` would wait out its timeout and then SIGKILL the postmaster
# instead of giving it a clean shutdown.
on_signal() {
  trap - TERM INT
  echo "[tak-database] Signal received — stopping PostgreSQL."
  stop_postgres || echo "[tak-database] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
  stop_vendor
  exit 0
}
trap on_signal TERM INT

psql_show() {
  PGCONNECT_TIMEOUT="$PGCONNECT_TIMEOUT" PGPASSWORD="${TAK_DB_PASSWORD:-}" psql -h localhost -U martiuser -d cot \
    -tAc "SHOW $1;" 2>/dev/null | tr -d '[:space:]'
}

# On the failure path an empty value is ambiguous: a missing psql binary, an
# auth failure, a server that never came up and a genuinely wrong setting all
# render the same. Capture stderr once so the diagnostic says which.
psql_stderr() {
  PGCONNECT_TIMEOUT="$PGCONNECT_TIMEOUT" PGPASSWORD="${TAK_DB_PASSWORD:-}" psql -h localhost -U martiuser -d cot \
    -tAc "SHOW autovacuum_vacuum_scale_factor;" 2>&1 >/dev/null | tr '\n' ' '
}

# Poll rather than sleep-then-check: on a first boot takserver-setup-db.sh
# installs postgresql.conf and restarts the server partway through startup, so
# the settings are not final until after that restart.
# FASTAK_VERIFY_TIMEOUT exists so the unit tests can exercise these paths
# without waiting five minutes.
DEADLINE=$((SECONDS + ${FASTAK_VERIFY_TIMEOUT:-300}))
VERIFIED=false
while [ "$SECONDS" -lt "$DEADLINE" ]; do
  if [ "$(psql_show autovacuum_vacuum_scale_factor)" = "$PG_AUTOVACUUM_SCALE_FACTOR" ] &&
    [ "$(psql_show autovacuum_vacuum_cost_limit)" = "$PG_AUTOVACUUM_COST_LIMIT" ] &&
    [ "$(psql_show maintenance_work_mem)" = "$PG_MAINTENANCE_WORK_MEM" ] &&
    [ "$(psql_show archive_mode)" = "off" ]; then
    VERIFIED=true
    break
  fi

  # A vendor that has exited will never bring the server up, so waiting out the
  # full timeout would burn five minutes and then blame the wrong component.
  if ! kill -0 "$VENDOR_PID" 2>/dev/null; then
    cat >&2 <<EOF
[tak-database] ERROR: TAK Server's own entrypoint exited during startup.

  ${VENDOR_ENTRYPOINT} is no longer running, so the database server will never
  come up and FastTAK's settings can never be verified. This is a failure in
  TAK Server's startup, not in FastTAK's tuning — look above this line for the
  vendor's own output.
EOF
    stop_postgres || echo "[tak-database] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
    exit 1
  fi

  sleep 5
done

if [ "$VERIFIED" != true ]; then
  PSQL_ERR="$(psql_stderr)"
  cat >&2 <<EOF
[tak-database] ERROR: FastTAK's PostgreSQL settings did not take effect.

  wanted: autovacuum_vacuum_scale_factor=${PG_AUTOVACUUM_SCALE_FACTOR}
          autovacuum_vacuum_cost_limit=${PG_AUTOVACUUM_COST_LIMIT}
          maintenance_work_mem=${PG_MAINTENANCE_WORK_MEM}
          archive_mode=off
  got:    autovacuum_vacuum_scale_factor=$(psql_show autovacuum_vacuum_scale_factor)
          autovacuum_vacuum_cost_limit=$(psql_show autovacuum_vacuum_cost_limit)
          maintenance_work_mem=$(psql_show maintenance_work_mem)
          archive_mode=$(psql_show archive_mode)

  psql stderr: ${PSQL_ERR:-(none)}

An empty "got" with psql stderr above means the query failed, not that the
setting is wrong. PostgreSQL also normalises values it reports back — 0.050
becomes 0.05 and 262144kB becomes 256MB — so a wanted/got pair that looks
equivalent means the .env value needs writing in the form the server reports.

Otherwise the server never became reachable, or FastTAK's block did not reach
${LIVE_CONF}. Running on untuned autovacuum with WAL archiving on fills the
disk, so this is fatal rather than a warning.
EOF
  stop_postgres || echo "[tak-database] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
  stop_vendor
  exit 1
fi

echo "[tak-database] Settings verified against the running server."

# ── 4. PGDATA persistence guard ──────────────────────────────────────────
# Uses $PGDATA rather than `SHOW data_directory`: that setting is restricted
# to roles with pg_read_all_settings, and martiuser (the role this script's
# psql calls connect as) does not carry it, so the query silently returns
# nothing — not a permissions error, since SHOW swallows it. Do not "fix"
# this back to querying the server; PGDATA is exactly what's needed here
# (set by the image, used above for LIVE_CONF, and matches what the vendor's
# own configureInDocker.sh hardcodes for pg_ctl -D). Because that trust is
# unverified by a live server round-trip, PG_VERSION is checked below to
# confirm PGDATA really is an initialised cluster directory.
if [ -x "$GUARD" ]; then
  DATA_DIR="${PGDATA:-}"
  if [ -z "$DATA_DIR" ]; then
    cat >&2 <<EOF
[tak-database] ERROR: PGDATA is not set.

  The persistence guard needs PGDATA to know which directory to check.
  Reporting the volume as verified without having checked it is the silent
  failure this guard exists to remove, so this is fatal.
EOF
    stop_postgres || echo "[tak-database] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
    stop_vendor
    exit 1
  fi
  if [ ! -f "${DATA_DIR}/PG_VERSION" ]; then
    cat >&2 <<EOF
[tak-database] ERROR: ${DATA_DIR}/PG_VERSION not found.

  PGDATA is set to ${DATA_DIR}, but it does not look like an initialised
  PostgreSQL cluster directory. Running the persistence guard against it
  would be checking the wrong thing, which is the silent failure this guard
  exists to remove, so this is fatal.
EOF
    stop_postgres || echo "[tak-database] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
    stop_vendor
    exit 1
  fi
  if ! "$GUARD" "$DATA_DIR"; then
    stop_postgres || echo "[tak-database] WARNING: pg_ctl stop failed; PostgreSQL was not shut down cleanly." >&2
    stop_vendor
    exit 1
  fi
  echo "[tak-database] PGDATA (${DATA_DIR}) is on a mounted volume."
else
  echo "[tak-database] WARNING: ${GUARD} not found — PGDATA persistence unverified." >&2
fi

wait "$VENDOR_PID"

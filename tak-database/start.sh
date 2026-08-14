#!/bin/bash
# FastTAK tak-database entrypoint.
#
# Wraps TAK Server's own db-utils/configureInDocker.sh with three things the
# vendor image does not do:
#   1. Set the CoreConfig DB password before the vendor's setup reads it.
#   2. Apply FastTAK's PostgreSQL tuning (DD-026) and disable WAL archiving.
#   3. Verify (2) actually took effect once the server is up, and fail if not.
#
# On (2): TAK 5.6 exposed tuning as pg_ctl -o flags, and this script used to
# splice FastTAK's in with sed. TAK 5.8 dropped those flags entirely, so that
# sed matched nothing and exited 0 — the tuning disappeared with no error.
# 5.8 moved tuning into db-utils/postgresql.conf, which takserver-setup-db.sh
# copies over the live config. Appending there is the vendor's own mechanism
# and cannot silently miss: postgresql.conf is last-wins, so there is no
# pattern to match. Step (3) exists because "cannot silently miss" is a claim
# worth testing at runtime.

set -uo pipefail

TAK_DIR="${FASTAK_TAK_DIR:-/opt/tak}"
PG_CONF="${TAK_DIR}/db-utils/postgresql.conf"
VENDOR_ENTRYPOINT="${TAK_DIR}/db-utils/configureInDocker.sh"
GUARD="${FASTAK_GUARD:-/opt/fastak/check-pgdata-persistent.sh}"
MARKER="# FASTTAK-TUNING-BEGIN"

PG_AUTOVACUUM_SCALE_FACTOR="${PG_AUTOVACUUM_SCALE_FACTOR:-0.05}"
PG_AUTOVACUUM_COST_LIMIT="${PG_AUTOVACUUM_COST_LIMIT:-1000}"
PG_MAINTENANCE_WORK_MEM="${PG_MAINTENANCE_WORK_MEM:-256MB}"

# ── 1. CoreConfig DB password ────────────────────────────────────────────
# tak-database starts before init-config and needs the password immediately.
#
# Written via a temp file rather than `sed -i`: BSD sed (macOS) requires an
# argument to -i and GNU sed forbids one, and this script is executed directly
# by the unit tests on the developer's host as well as inside the container.
if [ -n "${TAK_DB_PASSWORD:-}" ]; then
  for f in "${TAK_DIR}/CoreConfig.xml" "${TAK_DIR}/CoreConfig.example.xml"; do
    [ -f "$f" ] || continue
    sed '/<connection /s|password="[^"]*"|password="'"${TAK_DB_PASSWORD}"'"|' "$f" > "${f}.fastak.tmp" \
      && mv "${f}.fastak.tmp" "$f"
  done
fi

# ── 2. Tuning + WAL archiving ────────────────────────────────────────────
if [ ! -f "$PG_CONF" ]; then
  cat >&2 <<EOF
[tak-database] ERROR: ${PG_CONF} not found.

FastTAK applies its PostgreSQL tuning by appending to this file, which
TAK Server's takserver-setup-db.sh then installs as the live configuration.
Its absence means the release layout changed, and starting anyway would run
with untuned autovacuum and WAL archiving left on — the exact silent failure
this check exists to prevent.
EOF
  exit 1
fi

# Idempotent: /opt/tak is bind-mounted from the host, so this file persists
# across restarts. Drop any previous block before appending a fresh one.
# awk rather than `sed -i` for the BSD/GNU portability reason noted above.
awk -v marker="$MARKER" '$0 == marker { exit } { print }' "$PG_CONF" > "${PG_CONF}.fastak.tmp" \
  && mv "${PG_CONF}.fastak.tmp" "$PG_CONF"

cat >> "$PG_CONF" <<EOF
${MARKER}
# Appended by tak-database/start.sh — do not edit; rewritten on every start.
# postgresql.conf is last-wins, so these override the vendor values above.
#
# Autovacuum is tuned for TAK Server's high-write CoT workload (DD-026).
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
EOF

echo "[tak-database] Tuning applied: scale_factor=${PG_AUTOVACUUM_SCALE_FACTOR} cost_limit=${PG_AUTOVACUUM_COST_LIMIT} work_mem=${PG_MAINTENANCE_WORK_MEM} archive_mode=off"

# Unit tests exercise the config injection without a PostgreSQL server.
if [ "${FASTAK_INJECT_ONLY:-}" = "1" ]; then
  exit 0
fi

# ── 3. Start the vendor entrypoint, then verify ──────────────────────────
# configureInDocker.sh ends in `tail -f /dev/null` and never returns, so it
# runs in the background and this script waits on it after verifying.
"$VENDOR_ENTRYPOINT" &
VENDOR_PID=$!

psql_show() {
  PGPASSWORD="${TAK_DB_PASSWORD:-}" psql -h localhost -U martiuser -d cot \
    -tAc "SHOW $1;" 2>/dev/null | tr -d '[:space:]'
}

# Poll rather than sleep-then-check: takserver-setup-db.sh installs
# postgresql.conf and restarts the server partway through startup, so the
# settings are not final until after that restart.
# FASTAK_VERIFY_TIMEOUT exists so the unit test can exercise the failure path
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
  sleep 5
done

if [ "$VERIFIED" != true ]; then
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

Either the server never became reachable, or TAK Server stopped installing
db-utils/postgresql.conf as the live configuration. Running on untuned
autovacuum with WAL archiving on fills the disk, so this is fatal rather
than a warning.
EOF
  kill "$VENDOR_PID" 2>/dev/null
  exit 1
fi

echo "[tak-database] Settings verified against the running server."

# ── 4. PGDATA persistence guard ──────────────────────────────────────────
if [ -x "$GUARD" ]; then
  DATA_DIR="$(psql_show data_directory)"
  if [ -n "$DATA_DIR" ] && ! "$GUARD" "$DATA_DIR"; then
    kill "$VENDOR_PID" 2>/dev/null
    exit 1
  fi
  echo "[tak-database] PGDATA (${DATA_DIR}) is on a mounted volume."
else
  echo "[tak-database] WARNING: ${GUARD} not found — PGDATA persistence unverified." >&2
fi

wait "$VENDOR_PID"

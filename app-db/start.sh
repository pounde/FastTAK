#!/bin/bash
# App database entrypoint — PostGIS shared by Authentik and Node-RED.
#
# On first boot: POSTGRES_DB creates the primary database (authentik).
# This script creates the nodered database if it doesn't exist yet.
#
# On subsequent boots: syncs the role password with POSTGRES_PASSWORD
# in case .env was regenerated while the data volume persisted.
# Works because pg_hba.conf trusts local (Unix socket) connections.

# Authentik's worker opens hundreds of DB connections and leaves them idle.
# These settings let PostgreSQL reap idle connections automatically.
# See issue #31 for details.
docker-entrypoint.sh postgres \
  -c max_connections="${PG_APP_MAX_CONNECTIONS:-300}" \
  -c idle_session_timeout=300s \
  -c idle_in_transaction_session_timeout=120s \
  -c tcp_keepalives_idle=60 \
  -c tcp_keepalives_interval=10 \
  -c tcp_keepalives_count=6 &
PG_PID=$!

until pg_isready -U "$POSTGRES_USER" -q; do sleep 1; done

# Clear stale ALTER SYSTEM settings that conflict with command-line args
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "ALTER SYSTEM RESET ALL;" > /dev/null 2>&1

# Sync password using ALTER ROLE via stdin to avoid shell interpolation issues
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "ALTER ROLE \"$POSTGRES_USER\" WITH PASSWORD :'newpass'" \
  --set=newpass="$POSTGRES_PASSWORD" \
  > /dev/null 2>&1

# Create nodered database with PostGIS if it doesn't exist
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tc \
  "SELECT 1 FROM pg_database WHERE datname = 'nodered'" \
  | grep -q 1 \
  || psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "CREATE DATABASE nodered;" > /dev/null 2>&1

psql -U "$POSTGRES_USER" -d nodered -c \
  "CREATE EXTENSION IF NOT EXISTS postgis;" > /dev/null 2>&1

wait $PG_PID

#!/bin/bash
# Set the DB password in CoreConfig before the setup script reads it.
# The shared bind-mount means init-config will also patch this, but
# tak-database starts first and needs the password immediately.

if [ -n "${TAK_DB_PASSWORD}" ]; then
  for f in /opt/tak/CoreConfig.xml /opt/tak/CoreConfig.example.xml; do
    [ -f "$f" ] && sed -i '/<connection /s|password="[^"]*"|password="'"${TAK_DB_PASSWORD}"'"|' "$f"
  done
fi

# ── Inject autovacuum tuning flags into pg_ctl invocations ───────────────
# configureInDocker.sh is a TAK vendor script that hardcodes pg_ctl flags.
# We sed-patch it to append our tuning flags before exec-ing it.
# Defaults are tuned for TAK Server's high-write CoT workload (DD-026).
# Override via .env; set to PG defaults (0.2, 200, 64MB) to disable tuning.

PG_AUTOVACUUM_SCALE_FACTOR="${PG_AUTOVACUUM_SCALE_FACTOR:-0.05}"
PG_AUTOVACUUM_COST_LIMIT="${PG_AUTOVACUUM_COST_LIMIT:-1000}"
PG_MAINTENANCE_WORK_MEM="${PG_MAINTENANCE_WORK_MEM:-256MB}"

PG_EXTRA=" -c autovacuum_vacuum_scale_factor=${PG_AUTOVACUUM_SCALE_FACTOR}"
PG_EXTRA="${PG_EXTRA} -c autovacuum_vacuum_cost_limit=${PG_AUTOVACUUM_COST_LIMIT}"
PG_EXTRA="${PG_EXTRA} -c maintenance_work_mem=${PG_MAINTENANCE_WORK_MEM}"

echo "[tak-database] Autovacuum tuning: scale_factor=${PG_AUTOVACUUM_SCALE_FACTOR} cost_limit=${PG_AUTOVACUUM_COST_LIMIT} work_mem=${PG_MAINTENANCE_WORK_MEM}"
sed -i "s|-c shared_buffers=2560MB'|-c shared_buffers=2560MB${PG_EXTRA}'|g" /opt/tak/db-utils/configureInDocker.sh

exec /opt/tak/db-utils/configureInDocker.sh

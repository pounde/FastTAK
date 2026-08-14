#!/bin/bash
# check-pgdata-persistent.sh — assert PostgreSQL's data directory is on a mount.
#
# Usage: check-pgdata-persistent.sh <data-dir> [<mounts-file>]
#
# A data directory that is not a mount point lives in the container's writable
# layer and is destroyed by `docker rm` — which is what `docker compose down`,
# `--force-recreate`, and any image change all do. FastTAK shipped in exactly
# that state until TAK 5.8: the volume was mounted at /var/lib/postgresql/data
# while TAK 5.6 ran initdb into /var/lib/postgresql/15/data.
#
# The mounts file is a parameter so this is testable without a container.

set -u

DATA_DIR="${1:-}"
MOUNTS_FILE="${2:-/proc/mounts}"

if [ -z "$DATA_DIR" ]; then
  echo "Usage: $0 <data-dir> [<mounts-file>]" >&2
  exit 2
fi

# Normalise away a trailing slash so "/data" and "/data/" compare equal.
case "$DATA_DIR" in
  */) DATA_DIR="${DATA_DIR%/}" ;;
esac

if [ ! -r "$MOUNTS_FILE" ]; then
  echo "ERROR: cannot read $MOUNTS_FILE — unable to verify that $DATA_DIR is persistent." >&2
  exit 1
fi

# Field 2 of /proc/mounts is the mount point. Compare against it exactly:
# a mount one directory above PGDATA does not persist PGDATA, which is
# precisely the bug this exists to catch.
if awk -v d="$DATA_DIR" '$2 == d { found = 1 } END { exit !found }' "$MOUNTS_FILE"; then
  exit 0
fi

cat >&2 <<EOF
ERROR: PostgreSQL's data directory is not on a mounted volume.

  data directory: $DATA_DIR
  mounts checked: $MOUNTS_FILE

Nothing is mounted at that path, so the database lives in the container's
writable layer and will be destroyed the next time this container is removed —
by \`docker compose down\`, by \`--force-recreate\`, or by any image change.

Check that the tak-db-data volume in docker-compose.yml is mounted at the
path this TAK Server release actually uses for PGDATA.
EOF
exit 1

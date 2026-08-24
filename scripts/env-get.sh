#!/bin/bash
# scripts/env-get.sh — print one value from a .env file, using lib-env.sh.
#
# Usage: scripts/env-get.sh <env-file> <KEY>
#
# Exists for the callers that cannot source lib-env.sh: a justfile recipe has no
# equivalent of the `.` path a script resolves from its own location, and
# reconfig.sh is POSIX sh while the library is written for bash. This is that
# library behind a one-line CLI, rather than a second reader.
#
# The second reader is the defect: the recipes read
# `grep '^KEY=' .env | cut -d= -f2`, which keeps the quotes Compose's dotenv
# parser strips. `DEPLOY_MODE="direct"` therefore selected the direct overlay in
# scripts/upgrade.sh (env_get) and the subdomain one in the justfile and
# start.sh — caddy brought up without the Monitor, Node-RED and MediaMTX port
# publishings, by an upgrade that reported success.
#
# An absent file or key prints the empty string and exits 0: every caller has a
# documented default for that, and a lookup that fails the recipe's `set -e`
# instead would be a worse answer than the default.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -ne 2 ]; then
  echo "usage: env-get.sh <env-file> <KEY>" >&2
  exit 2
fi

[ -r "$SCRIPT_DIR/lib-env.sh" ] || {
  echo "ERROR: $SCRIPT_DIR/lib-env.sh is missing; cannot read $1." >&2
  exit 1
}
# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/lib-env.sh"

env_get "$1" "$2"

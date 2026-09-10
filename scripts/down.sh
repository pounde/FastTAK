#!/bin/bash
# scripts/down.sh — Stop the stack.
# Usage: ./scripts/down.sh
#
# Volumes survive. --remove-orphans is what removes the capture sidecars
# (tak-mitm, init-capture) without needing a flag: the overlay is never in
# COMPOSE_FILE here, so they are project orphans.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

# shellcheck source=scripts/lib-stack.sh
. "$SCRIPT_DIR/lib-stack.sh"

if [ $# -gt 0 ]; then
  echo "down.sh takes no arguments; it stops the whole stack." >&2
  exit 2
fi

# shellcheck disable=SC2119  # no --capture here; the capture overlay is never in COMPOSE_FILE for down
stack_export_compose_file
exec docker compose --env-file "$(stack_env_file)" down --remove-orphans

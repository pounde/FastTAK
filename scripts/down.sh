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

usage() {
  cat <<'EOF'
Usage: ./scripts/down.sh

Stop the stack. Volumes survive; the capture sidecars are removed.

  -h, --help    show this help
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) echo "down.sh takes no arguments; it stops the whole stack." >&2; usage >&2; exit 2 ;;
esac

# shellcheck disable=SC2119  # no --capture here; the capture overlay is never in COMPOSE_FILE for down
stack_export_compose_file
exec docker compose --env-file "$(stack_env_file)" down --remove-orphans

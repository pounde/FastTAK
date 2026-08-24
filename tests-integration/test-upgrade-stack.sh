#!/usr/bin/env bash
# test-upgrade-stack.sh — Isolated upgrade rehearsal: setup, run, tear down.
#
# Mirrors test-stack.sh but runs only tests marked `destructive` (the
# upgrade rehearsal in test_upgrade_rehearsal.py). scripts/upgrade.sh stops
# the stack, deletes Docker volumes, and restores from backup by design —
# every other integration test is read-only against the shared stack
# `just test-up` creates and expects to survive the whole run. Running the
# rehearsal here, in its own stack, keeps a rehearsal failure from
# demolishing that shared stack and cascading into unrelated failures.
#
# This stack uses the same /tmp/fastak-test-* naming as the shared stack
# (via test-setup.sh/test-down.sh unchanged) rather than a second prefix:
# usage is strictly sequential, never concurrent (see docker-compose.yml's
# resource allotments — two stacks are not viable on a dev machine at once),
# and by the time this script's setup runs, any prior stack this recipe
# itself started has already been torn down by its own EXIT trap below. If
# you run this while a `just test-up` stack is intentionally still up for
# manual iteration, test-setup.sh's own stale-stack cleanup will tear that
# down too — the same as running `just test-up` a second time today. Give
# the manual stack its own moment, or expect this to replace it.
#
# Usage: ./tests-integration/test-upgrade-stack.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Setup ────────────────────────────────────────────────────────────
PROJECT=$("${SCRIPT_DIR}/test-setup.sh" | tail -n 1)

# test-setup.sh exports HOST_ENV_FILE/TAK_HOST_PATH/BACKUP_DIR in its own
# subshell — those don't propagate up to us. Re-derive them from the state
# file, same as test-stack.sh, so any docker compose invocation the
# rehearsal makes gets the same variable substitution the original `up` saw.
STATE_FILE="/tmp/${PROJECT}/.test-state"
if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
    export TAK_HOST_PATH
    export BACKUP_DIR="${TEST_DIR}/backups"
    export HOST_ENV_FILE="${ENV_FILE}"
fi

# ── Cleanup on exit ──────────────────────────────────────────────────
# shellcheck disable=SC2317
cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    "${SCRIPT_DIR}/test-down.sh" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── Run the rehearsal ────────────────────────────────────────────────
# -m destructive (not the filename) so a future destructive test file is
# picked up automatically as long as it carries the same marker.
uv run pytest tests-integration/ -v -m destructive

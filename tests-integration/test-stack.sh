#!/usr/bin/env bash
# test-stack.sh — Full integration test: setup, run assertions, tear down.
#
# Composes test-setup.sh + test-run.sh + test-down.sh into a single
# CI-friendly script. For iterative development, use them separately:
#   just test-up     # stand up stack
#   just test-run    # run assertions (re-run after code changes)
#   just test-down   # tear down
#
# Usage: ./tests-integration/test-stack.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Setup ────────────────────────────────────────────────────────────
PROJECT=$("${SCRIPT_DIR}/test-setup.sh" | tail -n 1)

# test-setup.sh exports HOST_ENV_FILE/TAK_HOST_PATH/BACKUP_DIR in its own
# subshell — those don't propagate up to us. Re-derive them from the
# state file so any `docker compose` invocation pytest makes (e.g. the
# mediamtx recording test's --force-recreate) gets the same variable
# substitution the original `up` saw.
STATE_FILE="/tmp/${PROJECT}/.test-state"
if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
    export TAK_HOST_PATH
    export BACKUP_DIR="${TEST_DIR}/backups"
    export HOST_ENV_FILE="${ENV_FILE}"
fi

# ── Cleanup on exit ──────────────────────────────────────────────────
# Tear down ALL fastak-test-* stacks, not just $PROJECT, because the
# backup-restore test tears the captured project down mid-run and brings
# up a fresh one — that fresh one would otherwise leak.
# shellcheck disable=SC2317
cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    "${SCRIPT_DIR}/test-down.sh" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── Run assertions ───────────────────────────────────────────────────
# Don't pin FASTAK_TEST_PROJECT — the backup-restore test tears the
# original stack down and brings up a fresh one. A pinned project name
# goes stale at that point and conftest's stack_info fixture skips every
# test downstream that needs docker compose access. The glob fallback in
# conftest picks the newest /tmp/fastak-test-* dir, which is always the
# currently active stack.
#
# -m "not destructive" excludes the upgrade rehearsal: it stops the stack
# and deletes volumes, which would demolish the shared stack every other
# test here depends on. It runs separately, in its own stack, via
# test-upgrade-stack.sh (see `just test-upgrade` / `just test-integration`).
uv run pytest tests-integration/ -v -m "not destructive"

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
PROJECT=$("${SCRIPT_DIR}/test-setup.sh")

# ── Cleanup on exit ──────────────────────────────────────────────────
# shellcheck disable=SC2317
cleanup() {
    echo ""
    echo "=== Cleaning up ==="
    "${SCRIPT_DIR}/test-down.sh" "${PROJECT}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── Run assertions ───────────────────────────────────────────────────
"${SCRIPT_DIR}/test-run.sh" "${PROJECT}"

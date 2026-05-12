#!/usr/bin/env bash
# test-down.sh — Tear down test stacks.
#
# With no args: tears down ALL fastak-test-* stacks.
# With a project name: tears down only that stack.
#
# Usage:
#   ./tests-integration/test-down.sh              # all stacks
#   ./tests-integration/test-down.sh <project>    # specific stack

set -euo pipefail

teardown_stack() {
    local state="$1"
    local dir
    dir=$(dirname "$state")
    local name
    name=$(basename "$dir")
    echo "=== Tearing down: ${name} ==="

    # Subshell so the sourced state file can't leak PROJECT/REPO_DIR/ENV_FILE
    # into successive teardowns. Tear down by project name only — the
    # original compose files may no longer exist (e.g. stack was created
    # from a since-deleted worktree).
    (
        project_name="${name}"
        # shellcheck disable=SC1090
        if source "$state" 2>/dev/null; then
            project_name="${PROJECT:-${name}}"
        fi
        docker compose -p "${project_name}" down -v --remove-orphans 2>/dev/null || true
    )
    rm -rf "$dir"
}

if [ -n "${1:-}" ]; then
    # Specific project
    STATE="/tmp/${1}/.test-state"
    if [ -f "$STATE" ]; then
        teardown_stack "$STATE"
    else
        # No state file — try direct teardown by project name
        echo "=== Tearing down: ${1} (no state file) ==="
        docker compose -p "${1}" down -v --remove-orphans 2>/dev/null || true
        rm -rf "/tmp/${1}"
    fi
else
    # All stacks — also clean up dirs without state files
    found=false
    for dir in /tmp/fastak-test-*/; do
        [ -d "$dir" ] || continue
        found=true
        if [ -f "${dir}.test-state" ]; then
            teardown_stack "${dir}.test-state"
        else
            name=$(basename "$dir")
            echo "=== Tearing down: ${name} (no state file) ==="
            docker compose -p "${name}" down -v --remove-orphans 2>/dev/null || true
            rm -rf "$dir"
        fi
    done
    if [ "$found" = false ]; then
        echo "No running test stacks found."
    fi
fi

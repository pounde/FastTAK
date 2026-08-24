set shell := ["bash", "-euo", "pipefail", "-c"]

# List all available recipes
help:
    @just --list

# Run fast tests (unit + shellcheck + go) — no Docker needed
test:
    #!/bin/bash
    set -euo pipefail
    find . -name '*.sh' -not -path './tak/*' -not -path './.venv/*' | xargs shellcheck
    # ldap-proxy's tests encode its authorization rules (who may call /tokens,
    # who may search). CI installs Go so they always run there; locally they are
    # skipped loudly rather than failing a machine that only builds in-container.
    if command -v go >/dev/null 2>&1; then
        (cd ldap-proxy && go test ./...)
    else
        echo "  ⚠ go not found — SKIPPING ldap-proxy authorization tests"
    fi
    uv run pytest tests/ -v

# Run full suite: unit tests, the shared-stack suite, then the isolated destructive upgrade rehearsal
test-integration: test
    #!/usr/bin/env bash
    set -uo pipefail
    echo "=== [1/2] Integration suite (shared stack) ==="
    phase1=0
    ./tests-integration/test-stack.sh || phase1=$?
    echo ""
    echo "=== [2/2] Upgrade rehearsal (isolated stack) ==="
    phase2=0
    ./tests-integration/test-upgrade-stack.sh || phase2=$?
    echo ""
    if [ "$phase1" -eq 0 ]; then
        echo "[1/2] Integration suite (shared stack): PASSED"
    else
        echo "[1/2] Integration suite (shared stack): FAILED (exit ${phase1})"
    fi
    if [ "$phase2" -eq 0 ]; then
        echo "[2/2] Upgrade rehearsal (isolated stack): PASSED"
    else
        echo "[2/2] Upgrade rehearsal (isolated stack): FAILED (exit ${phase2})"
    fi
    if [ "$phase1" -ne 0 ] || [ "$phase2" -ne 0 ]; then
        exit 1
    fi

# Run the destructive upgrade rehearsal alone, against its own isolated stack
test-upgrade:
    ./tests-integration/test-upgrade-stack.sh

# Stand up an isolated test stack (detached — stays running until test-down)
test-up:
    ./tests-integration/test-setup.sh

# Stand up test stack in foreground (containers die when process is killed)
# Use with background agents: containers auto-cleanup when session ends
test-up-fg:
    ./tests-integration/test-setup.sh --foreground

# Run test assertions against the running test stack (excludes the destructive upgrade rehearsal — see `just test-upgrade`)
test-run:
    uv run pytest tests-integration/ -v -m "not destructive"

# Tear down the test stack
test-down:
    ./tests-integration/test-down.sh

# Run ruff linter
lint:
    uv run ruff check .

# Run ruff formatter
fmt:
    uv run ruff format .

# Install pre-commit hooks (commit + push)
setup-dev:
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push

# Start the stack (reads DEPLOY_MODE from .env to select compose files)
# Pass service names to rebuild+force-recreate specific services: `just up monitor`
# Pass --capture to run the mitmproxy capture sidecar: `just up --capture`
up *services:
    #!/bin/bash
    set -euo pipefail
    DEPLOY_MODE=$(grep '^DEPLOY_MODE=' .env 2>/dev/null | cut -d= -f2 || true)
    DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"
    # Split --capture out of the positional args (rest are service names).
    capture=false
    svcs=()
    for arg in {{services}}; do
      if [ "$arg" = "--capture" ]; then capture=true; else svcs+=("$arg"); fi
    done
    # Build COMPOSE_FILE only when non-default files are needed, so plain
    # subdomain `just up` still auto-loads docker-compose.override.yml.
    files=""
    if [ "$DEPLOY_MODE" = "direct" ]; then
      files="docker-compose.yml:docker-compose.direct.yml"
    fi
    if [ "$capture" = true ]; then
      files="${files:-docker-compose.yml}:docker-compose.capture.yml"
      mkdir -p ./captures ./capture/mitm
    fi
    if [ -n "$files" ]; then
      # An explicit COMPOSE_FILE disables compose's override auto-load;
      # re-append docker-compose.override.yml last so it still wins.
      if [ -f docker-compose.override.yml ]; then
        files="$files:docker-compose.override.yml"
      fi
      export COMPOSE_FILE="$files"
    fi
    # Surface FASTTAK_VERSION / FASTTAK_COMMIT to the monitor image build so
    # backups produced from this stack are labeled with the right version
    # (rather than the "dev" / "unknown" fallback baked into Dockerfile.monitor).
    # Mirror the derivation in start.sh — keep them in sync if either changes.
    if [ -f pyproject.toml ]; then
      FASTTAK_VERSION="$(awk -F'"' '/^version *=/{print $2; exit}' pyproject.toml 2>/dev/null || true)"
    fi
    if [ -z "${FASTTAK_VERSION:-}" ] && command -v git >/dev/null 2>&1; then
      FASTTAK_VERSION="$(git describe --tags --always 2>/dev/null || echo dev)"
    fi
    FASTTAK_VERSION="${FASTTAK_VERSION:-dev}"
    if command -v git >/dev/null 2>&1; then
      FASTTAK_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    else
      FASTTAK_COMMIT="unknown"
    fi
    export FASTTAK_VERSION FASTTAK_COMMIT
    # Branch on the array rather than expanding it: on macOS bash 3.2,
    # "${svcs[@]}" on an empty array trips `set -u` (unbound variable).
    # --remove-orphans clears stale services when toggling --capture on/off.
    if [ ${#svcs[@]} -gt 0 ]; then
      docker compose up -d --build --remove-orphans --force-recreate "${svcs[@]}"
    else
      docker compose up -d --build --remove-orphans
    fi

# Stop the stack (including the capture overlay, if it was up).
down *services:
    #!/bin/bash
    set -euo pipefail
    DEPLOY_MODE=$(grep '^DEPLOY_MODE=' .env 2>/dev/null | cut -d= -f2 || true)
    DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"
    if [ "$DEPLOY_MODE" = "direct" ]; then
      export COMPOSE_FILE="docker-compose.yml:docker-compose.direct.yml"
      if [ -f docker-compose.override.yml ]; then
        export COMPOSE_FILE="$COMPOSE_FILE:docker-compose.override.yml"
      fi
    fi
    # --remove-orphans removes the capture-overlay containers (tak-mitm,
    # init-capture) even though the overlay is not in COMPOSE_FILE — they are
    # project orphans. No --capture flag needed; extra args are ignored.
    docker compose down --remove-orphans

# Take a backup. Output lands in $BACKUP_DIR (default ./backups).
backup:
    docker compose exec -T monitor python -m app.backup run

# List backups currently on disk.
backups:
    docker compose exec -T monitor python -m app.backup list

# Manually prune backups (keeps newest N, default $BACKUP_RETENTION_KEEP).
backup-prune keep="":
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "{{keep}}" ]; then
        docker compose exec -T monitor python -m app.backup prune --keep {{keep}}
    else
        docker compose exec -T monitor python -m app.backup prune
    fi

# Pass --skip-cot to discard the CoT history instead of migrating it.
# Upgrade an existing deployment (database majors, then restart the stack).
upgrade *args:
    ./scripts/upgrade.sh {{args}}

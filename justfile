set shell := ["bash", "-euo", "pipefail", "-c"]

# List all available recipes
help:
    @just --list

# Run fast tests (unit + shellcheck) — no Docker needed
test:
    find . -name '*.sh' -not -path './tak/*' -not -path './.venv/*' | xargs shellcheck
    uv run pytest tests/ -v

# Run full test suite: unit tests first, then stand up stack and validate
test-integration: test
    ./tests-integration/test-stack.sh

# Stand up an isolated test stack (detached — stays running until test-down)
test-up:
    ./tests-integration/test-setup.sh

# Stand up test stack in foreground (containers die when process is killed)
# Use with background agents: containers auto-cleanup when session ends
test-up-fg:
    ./tests-integration/test-setup.sh --foreground

# Run test assertions against the running test stack
test-run:
    uv run pytest tests-integration/ -v

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
# Pass service names to rebuild and force-recreate specific services: `just up monitor`
up *services:
    #!/bin/bash
    set -euo pipefail
    DEPLOY_MODE=$(grep '^DEPLOY_MODE=' .env 2>/dev/null | cut -d= -f2 || true)
    DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"
    if [ "$DEPLOY_MODE" = "direct" ]; then
      export COMPOSE_FILE="docker-compose.yml:docker-compose.direct.yml"
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
    docker compose up -d --build {{ if services != "" { "--force-recreate" } else { "" } }} {{ services }}

# Stop the stack
down:
    #!/bin/bash
    set -euo pipefail
    DEPLOY_MODE=$(grep '^DEPLOY_MODE=' .env 2>/dev/null | cut -d= -f2 || true)
    DEPLOY_MODE="${DEPLOY_MODE:-subdomain}"
    if [ "$DEPLOY_MODE" = "direct" ]; then
      export COMPOSE_FILE="docker-compose.yml:docker-compose.direct.yml"
    fi
    docker compose down

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

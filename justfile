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

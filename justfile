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

# Run full suite: unit tests, then the integration suite against a built stack
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
up *args:
    ./start.sh {{args}}

# Stop the stack (including the capture overlay, if it was up).
down:
    ./scripts/down.sh

# Backups, through the monitor's own CLI. The stack must be running.
#   just backup run [--actor NAME]    take a backup; NAME is recorded in the audit log
#   just backup list                  list backups on disk
#   just backup prune [--keep N]      apply retention (default: $BACKUP_RETENTION_KEEP)
backup *args:
    docker compose exec -T monitor python -m app.backup {{args}}

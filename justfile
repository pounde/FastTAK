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

# Run ruff linter
lint:
    uv run ruff check .

# Run ruff formatter
fmt:
    uv run ruff format .

# Install pre-commit hooks (commit + push)
setup-dev:
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push

# Start production stack (services only reachable through Caddy + Authentik)
# Pass service names to rebuild and force-recreate specific services: `just up monitor`
# Without arguments, starts the full stack. With arguments, adds --force-recreate
# to ensure containers pick up code changes even when Docker's layer cache hits.
up *services:
    docker compose up -d --build {{ if services != "" { "--force-recreate" } else { "" } }} {{ services }}

# Stop the production stack
down:
    docker compose down

# Start stack for local development (direct-access ports enabled)
# Pass service names to rebuild and force-recreate specific services: `just dev-up monitor`
# Without arguments, starts the full stack. With arguments, adds --force-recreate
# to ensure containers pick up code changes even when Docker's layer cache hits.
dev-up *services:
    COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml docker compose up -d --build {{ if services != "" { "--force-recreate" } else { "" } }} {{ services }}

# Stop the dev stack
dev-down:
    COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml docker compose down

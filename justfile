set shell := ["bash", "-euo", "pipefail", "-c"]

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

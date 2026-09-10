#!/bin/bash
# scripts/test.sh — The fast suite: shellcheck, ldap-proxy's Go tests, pytest.
# Usage: ./scripts/test.sh
#
# No Docker needed. This is what pre-commit and CI run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

usage() {
  cat <<'EOF'
Usage: ./scripts/test.sh

Run the fast suite: shellcheck over every script, ldap-proxy's Go tests
(skipped loudly if go is not installed), then pytest tests/.

  -h, --help    show this help
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) usage >&2; exit 2 ;;
esac

# shellcheck disable=SC2038  # repo has no filenames with spaces/newlines
find . -name '*.sh' -not -path './tak/*' -not -path './.venv/*' | xargs shellcheck -x
# ldap-proxy's tests encode its authorization rules (who may call /tokens,
# who may search). CI installs Go so they always run there; locally they are
# skipped loudly rather than failing a machine that only builds in-container.
if command -v go >/dev/null 2>&1; then
  (cd ldap-proxy && go test ./...)
else
  echo "  ⚠ go not found — SKIPPING ldap-proxy authorization tests"
fi
uv run pytest tests/ -v

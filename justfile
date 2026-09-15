set shell := ["bash", "-euo", "pipefail", "-c"]

# Bare `just` lists every recipe, in file order.
_default:
    @just --list --unsorted

[doc("Set up FastTAK from a tak.gov release ZIP: extracts tak/, builds the images,
creates or updates .env. Run once, and again for a new TAK Server release.
  just setup <zip>            into this directory
  just setup -d <dir> <zip>   into another directory (the test harness uses this)")]
setup *args:
    ./setup.sh {{args}}

[doc("Start the stack: preflight .env, build, up, wait for tak-server, verify.
  just up                       whole stack, with the post-start checks
  just up <service>...          rebuild and recreate only those (checks skipped)
  just up --capture             include the mitmproxy capture sidecar
  just up --checks|--no-checks  override the checks default
  just up --no-wait             do not wait for tak-server (skips the checks unless --checks is given)
  just up --verbose             print every check, not only the failures")]
up *args:
    ./start.sh {{args}}

# Stop the stack. Volumes survive; the capture sidecars are removed.
down:
    ./scripts/down.sh

[doc("Compare .env with .env.example: keys this release adds that your .env does
not have, and keys this release no longer reads. Advisory only.")]
check:
    ./scripts/check-env.sh --report

[doc("Backups, through the monitor's own CLI. The stack must be running.
  just backup run [--actor NAME]    take a backup; NAME is recorded in the audit log
  just backup list                  list backups on disk
  just backup prune [--keep N]      apply retention (default: $BACKUP_RETENTION_KEEP)")]
backup *args:
    docker compose exec -T monitor python -m app.backup {{args}}

[doc("The fast suite: shellcheck, ldap-proxy's Go tests, pytest tests/. No Docker.
This is what pre-commit and CI run.")]
test:
    ./scripts/test.sh

[doc("The isolated integration stack. Each stack is its own compose project with
ports offset by +10000, so it runs beside a development stack.
  just test-stack up [--foreground] [--no-up]   stand it up (detached)
  just test-stack run                           assertions against it
  just test-stack down [<project>]              tear down (all, or one)
  just test-stack cycle                         fast suite, then up → run → down")]
test-stack *args:
    ./tests-integration/test-stack.sh {{args}}

# Run ruff linter
lint:
    uv run ruff check .

# Run ruff formatter
fmt:
    uv run ruff format .

# Install pre-commit hooks (commit + push)
setup-dev:
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push

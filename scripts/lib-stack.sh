# shellcheck shell=bash

# scripts/lib-stack.sh — compose-file resolution and image labelling for the
# stack entry points. Sourced, never executed.
#
# start.sh, scripts/down.sh and the justfile's recipes need the same two answers: which
# compose files apply to this deployment, and what version to stamp on the
# monitor image. Each used to derive them separately and they drifted — `down`
# lost the capture branch `up` had. This is the one implementation.

# shellcheck source=scripts/lib-env.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib-env.sh"

# stack_env_file
#
# The .env this invocation operates on: $FASTAK_ENV_FILE when set, otherwise
# .env in the current directory. Callers cd to the deployment root first.
stack_env_file() {
  printf '%s' "${FASTAK_ENV_FILE:-.env}"
}

# stack_export_compose_file [--capture]
#
# Exports COMPOSE_FILE when non-default files are needed and leaves it unset
# otherwise. An explicit COMPOSE_FILE disables Compose's auto-load of override
# files, so plain subdomain stays unset to keep that behaviour — and whenever
# it is set, every override file Compose would have auto-loaded is re-appended
# last so it still wins.
stack_export_compose_file() {
  local capture=false mode files="" f
  [ "${1:-}" = "--capture" ] && capture=true
  mode=$(env_get "$(stack_env_file)" DEPLOY_MODE)
  mode="${mode:-subdomain}"
  if [ "$mode" = "direct" ]; then
    files="docker-compose.yml:docker-compose.direct.yml"
  fi
  if $capture; then
    files="${files:-docker-compose.yml}:docker-compose.capture.yml"
  fi
  [ -z "$files" ] && return 0
  for f in docker-compose.override.yml compose.override.yml compose.override.yaml; do
    [ -f "$f" ] && files="$files:$f"
  done
  export COMPOSE_FILE="$files"
}

# stack_export_version
#
# Exports FASTTAK_VERSION and FASTTAK_COMMIT for the monitor image build, so a
# backup produced by this stack is labelled with the version that made it
# rather than the "dev"/"unknown" baked into Dockerfile.monitor.
stack_export_version() {
  local version="" commit="unknown"
  if [ -f pyproject.toml ]; then
    version="$(awk -F'"' '/^version *=/{print $2; exit}' pyproject.toml 2>/dev/null || true)"
  fi
  if [ -z "$version" ] && command -v git >/dev/null 2>&1; then
    version="$(git describe --tags --always 2>/dev/null || echo dev)"
  fi
  if command -v git >/dev/null 2>&1; then
    commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  fi
  export FASTTAK_VERSION="${version:-dev}" FASTTAK_COMMIT="$commit"
}

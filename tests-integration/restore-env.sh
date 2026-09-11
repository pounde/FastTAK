#!/usr/bin/env bash
# restore-env.sh — The .env step of a restore: take the archive's secrets,
# keep this host's TAK_VERSION.
#
# The archive's .env must win for the secrets: its database passwords match
# the role hashes in the dumps, and the containers have to initialise with
# them. TAK_VERSION is different — it names the images setup.sh built on
# this host, not the data, so an archive taken on an older release must not
# roll the host back to it (#99).
#
# An archive from a release below the supported floor is refused before
# anything else is touched: those images do not exist here, FastTAK has no
# path to carry that data forward, and check-env.sh would refuse to start.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/lib-env.sh
. "$SCRIPT_DIR/../scripts/lib-env.sh"
# shellcheck source=scripts/lib-tak-version.sh
. "$SCRIPT_DIR/../scripts/lib-tak-version.sh"

usage() {
  cat <<'USAGE'
Usage: tests-integration/restore-env.sh <archive-env> <host-env>

Replace <host-env> with <archive-env>, keeping the host's TAK_VERSION.
Refuses an archive from a TAK Server release below the supported floor.

  -h, --help    show this help
USAGE
}

case "${1:-}" in -h|--help) usage; exit 0 ;; esac
if [ $# -ne 2 ]; then
  usage >&2
  exit 2
fi
ARCHIVE_ENV="$1"
HOST_ENV="$2"

host_version=$(env_get "$HOST_ENV" TAK_VERSION)
archive_version=$(env_get "$ARCHIVE_ENV" TAK_VERSION)

if [ -n "$archive_version" ] && ! tak_version_meets_floor "$archive_version" "$TAK_VERSION_FLOOR"; then
  cat >&2 <<MSG
ERROR: this archive was taken on TAK Server $archive_version, below the
supported floor of $TAK_VERSION_FLOOR. FastTAK has no path to carry that
data across a release boundary, and this host's images are not that release.

Restore it on the FastTAK release that produced it: check out the tag named
by fasttak_version in the archive's MANIFEST.json and follow that release's
restore procedure. Nothing on this host has been changed.
MSG
  exit 1
fi

cp "$ARCHIVE_ENV" "$HOST_ENV"

if [ -z "$host_version" ]; then
  echo "[restore-env] no TAK_VERSION on this host; keeping the archive's (${archive_version:-unset})"
  exit 0
fi

if [ "$archive_version" = "$host_version" ]; then
  exit 0
fi

if env_has "$HOST_ENV" TAK_VERSION; then
  sed -i.bak "s/^TAK_VERSION=.*/TAK_VERSION=${host_version}/" "$HOST_ENV"
  rm -f "${HOST_ENV}.bak"
else
  printf 'TAK_VERSION=%s\n' "$host_version" >> "$HOST_ENV"
fi
echo "[restore-env] kept this host's TAK_VERSION=${host_version} (archive had ${archive_version:-none})"

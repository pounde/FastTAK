# scripts/lib-tak-version.sh — TAK Server version parsing and the supported
# floor. Sourced, never executed.
#
# setup.sh enforces the floor against a release bundle's tak/version.txt;
# check-env.sh enforces it against TAK_VERSION in .env. Both source this file,
# which carries the floor itself as well as the comparison, so they cannot
# disagree about what "5.8 or later" means or about which release is the floor.
#
# shellcheck shell=bash

# The oldest TAK Server release FastTAK supports. Earlier releases place the
# PostgreSQL data directory outside the volume FastTAK mounts, so the database
# does not survive a container recreate. See DD-051.
# shellcheck disable=SC2034  # read by setup.sh and check-env.sh, which source this
TAK_VERSION_FLOOR="5.8"

# tak_version_major_minor <version-string>
#
# "5.8-RELEASE-65" -> "5.8". Prints nothing and returns 1 when the string is not
# <digits>.<digits>[-anything], so a missing or malformed version.txt becomes a
# caller-visible failure rather than a silent "0".
tak_version_major_minor() {
  local v="$1" mm major minor
  mm="${v%%-*}"
  major="${mm%%.*}"
  minor="${mm#*.}"

  # No dot at all: "${mm#*.}" returns mm unchanged, so major == minor.
  [ "$mm" = "$major" ] && return 1
  [ -z "$major" ] && return 1
  [ -z "$minor" ] && return 1

  case "$major" in *[!0-9]*) return 1 ;; esac
  case "$minor" in *[!0-9]*) return 1 ;; esac

  printf '%s.%s' "$major" "$minor"
}

# tak_version_meets_floor <version-string> <floor-string>
#
# 0 when version >= floor. Major and minor are compared as integers, so 5.10
# correctly sorts above 5.8 — a string comparison would get that backwards.
tak_version_meets_floor() {
  local v f v_major v_minor f_major f_minor
  v="$(tak_version_major_minor "$1")" || return 1
  f="$(tak_version_major_minor "$2")" || return 1

  v_major="${v%%.*}"; v_minor="${v#*.}"
  f_major="${f%%.*}"; f_minor="${f#*.}"

  [ "$v_major" -gt "$f_major" ] && return 0
  [ "$v_major" -lt "$f_major" ] && return 1
  [ "$v_minor" -ge "$f_minor" ]
}

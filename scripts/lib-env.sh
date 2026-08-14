# scripts/lib-env.sh — shared readers for the .env file. Sourced, never executed.
#
# check-env.sh validates values and ensure-secrets.sh fills missing ones, so the
# two must agree on what "set" means. A key that one treats as empty and the
# other as populated is how a required secret goes missing.
#
# shellcheck shell=bash

# env_get <file> <key>
#
# Reads a key's value, matching Docker Compose dotenv semantics: optional
# 'export' prefix, optional leading whitespace, last-wins on duplicates,
# surrounding quotes stripped, `=` in values preserved, inline comments after
# quoted values trimmed, trailing whitespace trimmed for unquoted values.
# Prints the empty string when the key is absent or has no value.
env_get() {
  local file="$1" key="$2" line val
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null | tail -n 1)
  [ -z "$line" ] && { printf '%s' ''; return; }
  # Strip everything up to and including the first `=` (key + optional prefix)
  val="${line#*=}"
  case "$val" in
    \"*)
      # Double-quoted: take up to the closing double quote
      val="${val#\"}"
      val="${val%%\"*}"
      ;;
    \'*)
      # Single-quoted: take up to the closing single quote
      val="${val#\'}"
      val="${val%%\'*}"
      ;;
    *)
      # Unquoted: strip trailing whitespace
      val="${val%"${val##*[![:space:]]}"}"
      ;;
  esac
  printf '%s' "$val"
}

# env_has <file> <key>
#
# True when the key appears as an assignment at all, whatever its value. A
# commented-out `#KEY=` does not match — `#` is not whitespace — so a commented
# key reads as absent and gets appended rather than uncommented in place.
env_has() {
  local file="$1" key="$2"
  grep -qE "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null
}

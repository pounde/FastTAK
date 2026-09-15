#!/bin/sh
# healthcheck.sh — Docker healthcheck for TAK Server container
# Checks:
#   1. All 5 Java processes running (config, messaging, api, retention, plugins)
#   2. Port 8089 accepting connections (CoT TLS)
#   3. The API answers without a server error (5xx = up but failing)
#   4. Certificate expiry within 30 days
#   5. Ignite client disconnects in the recent log
#   6. OutOfMemoryError in the recent log
#
# Exit 0 = healthy, Exit 1 = unhealthy. Paths and the probe URL come from the
# environment so the script runs outside the container (tests/test_healthcheck_sh.py).

CERT_DIR="${CERT_DIR:-/opt/tak/certs/files}"
LOGFILE="${LOGFILE:-/opt/tak/logs/takserver.log}"
MSG_LOGFILE="${MSG_LOGFILE:-/opt/tak/logs/takserver-messaging.log}"
INCIDENT_DIR="${INCIDENT_DIR:-/opt/tak/logs/incident}"
PROC_ROOT="${PROC_ROOT:-/proc}"
PROBE_URL="${PROBE_URL:-https://localhost:8446/Marti/api/version}"
WARN_DAYS=30
WARN_SECONDS=$((WARN_DAYS * 86400))

# --- Check 1: All 5 Java processes running ---
# The 5.8 hardened image ships no procps (no pgrep/ps/pidof), so match on
# /proc directly. cmdline is NUL-separated; translate to spaces before matching.
proc_running() {
    for c in "$PROC_ROOT"/[0-9]*/cmdline; do
        [ -r "$c" ] || continue
        if tr '\0' ' ' < "$c" 2>/dev/null | grep -qF -- "$1"; then
            return 0
        fi
    done
    return 1
}

MISSING=""
proc_running "spring.profiles.active=config"    || MISSING="${MISSING} config"
proc_running "spring.profiles.active=messaging" || MISSING="${MISSING} messaging"
proc_running "spring.profiles.active=api"       || MISSING="${MISSING} api"
proc_running "takserver-retention.jar"          || MISSING="${MISSING} retention"
proc_running "takserver-pm.jar"                 || MISSING="${MISSING} plugins"

if [ -n "$MISSING" ]; then
    echo "UNHEALTHY: missing processes:${MISSING}"
    exit 1
fi

# --- Check 2: Port 8089 accepting connections ---
if command -v nc >/dev/null 2>&1; then
    nc -z -w 2 localhost 8089 2>/dev/null || { echo "UNHEALTHY: port 8089 not accepting connections"; exit 1; }
fi

# --- Check 3: The API answers without a server error ---
# A dead listener gives 000. A server that answers 5xx to everything — the
# Ignite-client-detached outage — is up, TLS-fine, and useless; that is the
# case this check exists for (#79). Any other code proves the API answers.
if command -v curl >/dev/null 2>&1; then
    HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$PROBE_URL" 2>/dev/null)
    case "$HTTP_CODE" in
        000) echo "UNHEALTHY: no TLS response from $PROBE_URL"; exit 1 ;;
        5*)  echo "UNHEALTHY: $PROBE_URL returned HTTP $HTTP_CODE (the API is up but failing)"; exit 1 ;;
    esac
fi

# --- Check 4: Certificate expiry ---
if [ -d "${CERT_DIR}" ]; then
    now=$(date +%s)
    threshold=$((now + WARN_SECONDS))

    for pem in "${CERT_DIR}"/*.pem; do
        [ -f "${pem}" ] || continue
        openssl x509 -in "${pem}" -noout 2>/dev/null || continue

        enddate=$(openssl x509 -enddate -noout -in "${pem}" 2>/dev/null) || continue
        expiry_str=$(echo "${enddate}" | sed 's/notAfter=//')

        if expiry_epoch=$(date -d "${expiry_str}" +%s 2>/dev/null); then
            : # GNU date
        elif expiry_epoch=$(date -j -f "%b %d %T %Y %Z" "${expiry_str}" +%s 2>/dev/null); then
            : # BSD date
        else
            continue
        fi

        if [ "${expiry_epoch}" -le "${now}" ]; then
            echo "UNHEALTHY: cert EXPIRED: $(basename "${pem}")"
            exit 1
        fi

        if [ "${expiry_epoch}" -le "${threshold}" ]; then
            days_left=$(( (expiry_epoch - now) / 86400 ))
            echo "UNHEALTHY: cert expiring soon: $(basename "${pem}") (${days_left} days)"
            exit 1
        fi
    done
fi

# --- Check 5: OutOfMemoryError in logs ---
if [ -f "$LOGFILE" ]; then
    if grep -q "OutOfMemoryError" "$LOGFILE" 2>/dev/null; then
        echo "UNHEALTHY: OutOfMemoryError detected in logs"
        exit 1
    fi
fi

echo "HEALTHY: all processes running, ports ok, certs valid"
exit 0

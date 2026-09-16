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
KEEP_INCIDENTS=5

# capture <check>: write the log tails to INCIDENT_DIR, named by time and the
# check that tripped, keeping the newest KEEP_INCIDENTS. Failure to write is
# reported on stderr and never changes the verdict.
capture() {
    if ! mkdir -p "$INCIDENT_DIR" 2>/dev/null; then
        echo "healthcheck: cannot write $INCIDENT_DIR; no incident capture" >&2
        return
    fi
    # Stamp first so names sort chronologically; the PID keeps two trips in
    # the same second apart.
    _out="$INCIDENT_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$$-$1.log"
    {
        echo "== $1 tripped at $(date -u +%Y-%m-%dT%H:%M:%SZ) =="
        echo "== tail -n 2000 $LOGFILE =="
        [ -f "$LOGFILE" ] && tail -n 2000 "$LOGFILE"
        echo "== tail -n 500 $MSG_LOGFILE =="
        [ -f "$MSG_LOGFILE" ] && tail -n 500 "$MSG_LOGFILE"
        :
    } 2>/dev/null > "$_out" || echo "healthcheck: could not write $_out" >&2
    # shellcheck disable=SC2012  # filenames are our own generated timestamp-pid-check.log, never adversarial
    ls -1 "$INCIDENT_DIR"/*.log 2>/dev/null | sort -r | tail -n +$((KEEP_INCIDENTS + 1)) \
        | while read -r _old; do rm -f "$_old"; done
}

# trip <check> <message>: report UNHEALTHY and exit 1. The first trip of an
# incident captures the evidence rotation would otherwise eat (#79); the
# marker suppresses further captures until a healthy pass clears it.
trip() {
    if [ ! -f "$INCIDENT_DIR/.tripped" ]; then
        capture "$1"
        [ -d "$INCIDENT_DIR" ] && : > "$INCIDENT_DIR/.tripped"
    fi
    echo "UNHEALTHY: $2"
    exit 1
}

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
    trip processes "missing processes:${MISSING}"
fi

# --- Check 2: Port 8089 accepting connections ---
if command -v nc >/dev/null 2>&1; then
    nc -z -w 2 localhost 8089 2>/dev/null || trip port-8089 "port 8089 not accepting connections"
fi

# --- Check 3: The API answers without a server error ---
# A dead listener gives 000. A server that answers 5xx to everything — the
# Ignite-client-detached outage — is up, TLS-fine, and useless; that is the
# case this check exists for (#79). Any other code proves the API answers.
if command -v curl >/dev/null 2>&1; then
    HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 "$PROBE_URL" 2>/dev/null)
    CURL_RC=$?
    case "$HTTP_CODE" in
        ""|000)
            if [ "$CURL_RC" -eq 28 ]; then
                trip api-probe "$PROBE_URL did not answer within 5s"
            else
                trip api-probe "no TLS response from $PROBE_URL"
            fi
            ;;
        5*)  trip api-probe "$PROBE_URL returned HTTP $HTTP_CODE (the API is up but failing)" ;;
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
            trip cert-expired "cert EXPIRED: $(basename "${pem}")"
        fi

        if [ "${expiry_epoch}" -le "${threshold}" ]; then
            days_left=$(( (expiry_epoch - now) / 86400 ))
            trip cert-expiring "cert expiring soon: $(basename "${pem}") (${days_left} days)"
        fi
    done
fi

SCAN_LINES=500

# --- Check 5: Ignite client disconnects in the recent log ---
# The detached-client outage logs these while every other check passes.
# Bounded to the last SCAN_LINES lines so a past incident cannot pin the
# container unhealthy until rotation (#79).
if [ -f "$LOGFILE" ]; then
    IGNITE=$(tail -n "$SCAN_LINES" "$LOGFILE" \
        | grep -oE 'IgniteClientDisconnected|ClusterTopologyException|Failed to connect to node' \
        | head -n 1)
    if [ -n "$IGNITE" ]; then
        trip ignite "$IGNITE in the last $SCAN_LINES lines of takserver.log"
    fi
fi

# --- Check 6: OutOfMemoryError in the recent log ---
if [ -f "$LOGFILE" ]; then
    if tail -n "$SCAN_LINES" "$LOGFILE" | grep -q "OutOfMemoryError" 2>/dev/null; then
        trip oom "OutOfMemoryError in the last $SCAN_LINES lines of takserver.log"
    fi
fi

rm -f "$INCIDENT_DIR/.tripped" 2>/dev/null
echo "HEALTHY: all processes running, ports ok, certs valid"
exit 0

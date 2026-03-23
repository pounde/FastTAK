#!/bin/sh
# healthcheck.sh — Docker healthcheck for TAK Server container
# Checks:
#   1. All 5 Java processes running (config, messaging, api, retention, plugins)
#   2. Port 8089 accepting connections (CoT TLS)
#   3. Port 8446 TLS handshake works (web admin)
#   4. Certificate expiry within 30 days
#   5. OutOfMemoryError in logs
#
# Exit 0 = healthy, Exit 1 = unhealthy

CERT_DIR="/opt/tak/certs/files"
WARN_DAYS=30
WARN_SECONDS=$((WARN_DAYS * 86400))
LOGFILE="/opt/tak/logs/takserver.log"

# --- Check 1: All 5 Java processes running ---
MISSING=""
pgrep -f "spring.profiles.active=config" > /dev/null 2>&1    || MISSING="${MISSING} config"
pgrep -f "spring.profiles.active=messaging" > /dev/null 2>&1  || MISSING="${MISSING} messaging"
pgrep -f "spring.profiles.active=api" > /dev/null 2>&1        || MISSING="${MISSING} api"
pgrep -f "takserver-retention.jar" > /dev/null 2>&1            || MISSING="${MISSING} retention"
pgrep -f "takserver-pm.jar" > /dev/null 2>&1                   || MISSING="${MISSING} plugins"

if [ -n "$MISSING" ]; then
    echo "UNHEALTHY: missing processes:${MISSING}"
    exit 1
fi

# --- Check 2: Port 8089 accepting connections ---
if command -v nc >/dev/null 2>&1; then
    nc -z -w 2 localhost 8089 2>/dev/null || { echo "UNHEALTHY: port 8089 not accepting connections"; exit 1; }
fi

# --- Check 3: Port 8446 TLS responding ---
if command -v curl >/dev/null 2>&1; then
    HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 5 https://localhost:8446/ 2>/dev/null)
    if [ "$HTTP_CODE" = "000" ]; then
        echo "UNHEALTHY: port 8446 TLS handshake failed"
        exit 1
    fi
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

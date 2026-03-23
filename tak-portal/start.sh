#!/bin/sh
# Copy config from init-identity's output into the portal's data dir.
# Log warnings if files are missing so operators can diagnose.

mkdir -p /usr/src/app/data/certs

if [ -f /opt/tak/portal/settings.json ]; then
    cp -f /opt/tak/portal/settings.json /usr/src/app/data/
    echo "[tak-portal] settings.json loaded"
else
    echo "[tak-portal] WARNING: settings.json not found — did init-identity run?"
fi

if ls /opt/tak/portal/certs/* >/dev/null 2>&1; then
    cp -f /opt/tak/portal/certs/* /usr/src/app/data/certs/
    echo "[tak-portal] certs loaded"
else
    echo "[tak-portal] WARNING: no certs in /opt/tak/portal/certs/ — TAK API calls may fail"
fi

# Patch QR enrollment to use FQDN instead of internal TAK_URL hostname.
# TAK_URL points to tak-server:8443 (internal) for API calls, but the QR
# enrollment URL needs the external FQDN so TAK clients can reach the server.
if [ -n "$FQDN" ] && [ -f /usr/src/app/services/qr.service.js ]; then
    if grep -q "return \"${FQDN}\"" /usr/src/app/services/qr.service.js 2>/dev/null; then
        echo "[tak-portal] QR enrollment host already set to ${FQDN}"
    elif grep -q 'function getTakHost()' /usr/src/app/services/qr.service.js; then
        sed -i "s|function getTakHost() {|function getTakHost() { return \"${FQDN}\";|" \
            /usr/src/app/services/qr.service.js
        echo "[tak-portal] QR enrollment host set to ${FQDN}"
    else
        echo "[tak-portal] WARNING: getTakHost() not found — QR codes may use internal hostname"
    fi
fi

exec npm start

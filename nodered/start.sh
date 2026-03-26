#!/bin/sh
# Copy starter flows on first boot only.
# After first boot, users modify flows in the editor — their changes
# are saved to the /data volume and persist across restarts.

if [ ! -f /data/.fastak-initialized ]; then
  cp /opt/fastak/flows.json /data/flows.json
  cd /data && npm install --save node-red-contrib-postgresql 2>&1 | tail -1
  touch /data/.fastak-initialized
  echo "[nodered] Installed starter flows with FastTAK connections"
fi

# Extract PEM cert/key from .p12 on every startup (stays in sync with cert rotation).
# Node-RED's tls-config cert/key/ca fields are file paths.
# The .p12 format isn't supported — Node.js TLS needs PEM files.
P12_PASSWORD="${P12_PASSWORD:-atakatak}"

if ! command -v openssl >/dev/null 2>&1; then
  echo "[nodered] WARNING: openssl not found — cannot extract PEM from .p12"
elif [ -f /opt/tak/certs/svc_nodered.p12 ]; then
  if openssl pkcs12 -in /opt/tak/certs/svc_nodered.p12 -nokeys -clcerts \
       -passin "pass:${P12_PASSWORD}" -out /data/svc_nodered.cert.pem 2>/dev/null && \
     openssl pkcs12 -in /opt/tak/certs/svc_nodered.p12 -nocerts -nodes \
       -passin "pass:${P12_PASSWORD}" -out /data/svc_nodered.key.pem 2>/dev/null; then
    chmod 600 /data/svc_nodered.key.pem
    # Set TLS servername to match the TAK Server certificate's SAN
    if [ -n "${FQDN}" ] && [ -f /data/flows.json ]; then
      node -e "
const fs = require('fs');
const flows = JSON.parse(fs.readFileSync('/data/flows.json', 'utf8'));
const tls = flows.find(n => n.id === 'fastak-tls');
if (tls) { tls.servername = process.env.FQDN; fs.writeFileSync('/data/flows.json', JSON.stringify(flows, null, 4)); }
"
    fi
    echo "[nodered] Extracted PEM cert/key from svc_nodered.p12"
  else
    echo "[nodered] WARNING: Failed to extract PEM from svc_nodered.p12 — TLS config will not work"
  fi
fi

exec ./entrypoint.sh

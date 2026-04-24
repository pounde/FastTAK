#!/bin/sh
# First-boot init for the nodered container.
#
# Cert/key PEMs for the `fastak-tls` and library-flow TLS config nodes are
# written directly to /data/certs/ by the monitor when a data-mode service
# account is created (see monitor/app/api/service_accounts/cert_gen.py,
# write_nodered_pems). No runtime extraction needed here — the bind mount
# makes them visible as soon as the POST to create the account returns.

# Copy starter flows on first boot only.
# After first boot, users modify flows in the editor — their changes
# are saved to the /data volume and persist across restarts.
if [ ! -f /data/.fastak-initialized ]; then
  cp /opt/fastak/flows.json /data/flows.json
  cd /data && npm install --save node-red-contrib-postgresql 2>&1 | tail -1
  touch /data/.fastak-initialized
  echo "[nodered] Installed starter flows with FastTAK connections"
fi

# Set TLS servername on every tls-config node that has an empty one.
# The TAK Server cert's SAN is SERVER_ADDRESS (from .env). Without this,
# Node.js falls back to using the tcp-out node's host ("tak-server", the
# Docker hostname), which doesn't match the cert SAN → handshake fails
# verification. Users can override servername per-node; this only fills
# in empties.
if [ -n "${SERVER_ADDRESS}" ] && [ -f /data/flows.json ]; then
  node -e "
const fs = require('fs');
const flows = JSON.parse(fs.readFileSync('/data/flows.json', 'utf8'));
let patched = 0;
for (const n of flows) {
  if (n.type === 'tls-config' && !n.servername) {
    n.servername = process.env.SERVER_ADDRESS;
    patched++;
  }
}
if (patched) {
  fs.writeFileSync('/data/flows.json', JSON.stringify(flows, null, 4));
  console.log('[nodered] Set servername on ' + patched + ' tls-config node(s) to ' + process.env.SERVER_ADDRESS);
}
" 2>/dev/null || echo "[nodered] WARNING: Failed to patch tls-config servername"
fi

exec ./entrypoint.sh

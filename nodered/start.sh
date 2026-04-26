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

# Sanity-check the shipped library bind mount. If the host directory is
# missing or unreadable, Compose silently mounts an empty directory and
# Node-RED's library scanner shows an empty fasttak folder with no error
# in any log. Surface it here so the failure mode is diagnosable.
if [ -d /data/lib/flows/fasttak ] && [ -z "$(ls -A /data/lib/flows/fasttak 2>/dev/null)" ]; then
  echo "[nodered] WARNING: /data/lib/flows/fasttak is empty — host bind mount './nodered/flows-library' may be missing or unreadable. Library flows will not appear under Import → Local."
fi

if [ -z "${SERVER_ADDRESS}" ]; then
  echo "[nodered] WARNING: SERVER_ADDRESS is unset — TLS handshakes will fail because library tls-config nodes resolve servername from this env var. Set SERVER_ADDRESS in .env."
fi

exec ./entrypoint.sh

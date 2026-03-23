#!/bin/sh
# Copy starter flows on first boot only.
# After first boot, users modify flows in the editor — their changes
# are saved to the /data volume and persist across restarts.

if [ ! -f /data/.fastak-initialized ]; then
  cp /opt/fastak/flows.json /data/flows.json
  cd /data && npm install --save node-red-contrib-postgresql node-red-contrib-tak 2>&1 | tail -1
  touch /data/.fastak-initialized
  echo "[nodered] Installed starter flows with FastTAK connections"
fi

exec ./entrypoint.sh

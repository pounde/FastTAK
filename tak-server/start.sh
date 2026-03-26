#!/bin/bash
# FastTAK tak-server startup wrapper.
# Waits for certificates to be available before starting TAK Server.
# On cold boot with fresh cert generation, the bind-mount may have a brief
# propagation delay. Without this wait, the API process fails to load the
# keystore and dies silently.

CERT_FILES="/opt/tak/certs/files"

echo "[tak-server] Waiting for certificates..."
WAITED=0
while [ ! -f "${CERT_FILES}/takserver.jks" ] || [ ! -f "${CERT_FILES}/truststore-root.jks" ]; do
  sleep 2
  WAITED=$((WAITED + 2))
  if [ $WAITED -ge 120 ]; then
    echo "[tak-server] ERROR: Certificates not found after 120s" >&2
    exit 1
  fi
done
echo "[tak-server] Certificates ready (waited ${WAITED}s)"

# Ensure TAKIgniteConfig.xml exists — prevents FileAlreadyExistsException
# race between config and api processes on cold boot
if [ ! -f /opt/tak/TAKIgniteConfig.xml ] && [ -f /opt/tak/TAKIgniteConfig.example.xml ]; then
  cp /opt/tak/TAKIgniteConfig.example.xml /opt/tak/TAKIgniteConfig.xml
  echo "[tak-server] Created TAKIgniteConfig.xml"
fi

# Register API service cert after TAK Server is ready (background, idempotent)
/opt/tak/register-api-cert.sh &

# Hand off to the official TAK Server startup
exec /opt/tak/configureInDocker.sh init

#!/bin/sh
# register-api-cert.sh — Register the FastTAK API service cert with TAK Server.
# Runs as a background process during TAK Server startup.
# Waits for TAK Server to be fully initialized, then runs certmod.
# Idempotent — safe to run on every boot.

CERT_FILE="/opt/tak/certs/files/svc_fasttakapi.pem"
LOG_FILE="/opt/tak/logs/takserver-messaging.log"
READY_PATTERN="com.bbn.marti.nio.server.NioServer - Server started"
MAX_WAIT=300  # 5 minutes
WAITED=0

if [ ! -f "${CERT_FILE}" ]; then
  echo "[register-api-cert] WARNING: ${CERT_FILE} not found — skipping"
  exit 0
fi

echo "[register-api-cert] Waiting for TAK Server to start..."
while ! grep -q "${READY_PATTERN}" "${LOG_FILE}" 2>/dev/null; do
  sleep 5
  WAITED=$((WAITED + 5))
  if [ ${WAITED} -ge ${MAX_WAIT} ]; then
    echo "[register-api-cert] ERROR: TAK Server not ready after ${MAX_WAIT}s" >&2
    exit 1
  fi
done

echo "[register-api-cert] TAK Server ready — registering svc_fasttakapi cert"
cd /opt/tak || exit 1
# Retry certmod until it succeeds — same pattern as TAK Server's enable_admin.sh.
# The database may not be fully ready even after NioServer logs "Server started".
RETRIES=0
MAX_RETRIES=12  # 60 seconds
until java -jar utils/UserManager.jar certmod -A "${CERT_FILE}" 2>&1; do
  RETRIES=$((RETRIES + 1))
  if [ ${RETRIES} -ge ${MAX_RETRIES} ]; then
    echo "[register-api-cert] ERROR: certmod failed after ${MAX_RETRIES} retries" >&2
    exit 1
  fi
  sleep 5
done
echo "[register-api-cert] Done"

#!/bin/bash
# Materialize the certs the mitmproxy capture sidecar needs, from TAK's own
# cert store:
#   server-bundle.pem  takserver cert+key — mitm presents this DOWNSTREAM to clients
#   mitm-client.pem    mitm-proxy cert+key — mitm presents this UPSTREAM to TAK (mTLS)
#   ca.pem             TAK CA (reference / upstream trust anchor)
# The mitm-proxy cert is issued via TAK's own makeCert.sh so TAK Server trusts
# it like any other user. Idempotent: only (re)issues the client cert if absent.
set -euo pipefail

CERT_DIR=/opt/tak/certs
CERT_FILES="${CERT_DIR}/files"
OUT=/certs
MITM_NAME=mitm-proxy

echo "[init-capture] waiting for TAK CA + server keystore..."
for _ in $(seq 1 60); do
  if [ -f "${CERT_FILES}/ca.pem" ] && [ -f "${CERT_FILES}/takserver.jks" ]; then
    break
  fi
  sleep 2
done
[ -f "${CERT_FILES}/ca.pem" ] || { echo "[init-capture] ca.pem never appeared" >&2; exit 1; }
[ -f "${CERT_FILES}/takserver.jks" ] || { echo "[init-capture] takserver.jks never appeared" >&2; exit 1; }

# Issue the mitm client cert via TAK's own flow (idempotent).
# makeCert.sh -> cert-metadata.sh requires these to be set; mirror the
# defaults init-config uses so the cert subject matches the rest of the deployment.
if [ ! -f "${CERT_FILES}/${MITM_NAME}.pem" ]; then
  echo "[init-capture] issuing ${MITM_NAME} client cert via makeCert.sh..."
  export STATE="${STATE:-XX}"
  export CITY="${CITY:-Default}"
  export ORGANIZATIONAL_UNIT="${ORGANIZATIONAL_UNIT:-FastTAK}"
  ( cd "${CERT_DIR}" && bash ./makeCert.sh client "${MITM_NAME}" )
fi

mkdir -p "${OUT}"
rm -f "${OUT}/ready"

KEYPASS="${PASS:-${CAPASS:-atakatak}}"
STOREPASS="${CAPASS:-atakatak}"

# --- server bundle (cert mitm presents DOWNSTREAM) --------------------------
# Extract the exact cert+key TAK presents on 8443/8089 straight from its <tls>
# keystore (takserver.jks). The loose takserver.pem/.key files can be out of
# sync after the SERVER_ADDRESS server-cert regeneration (pem updated, key not),
# so the keystore is the only reliable matching pair. mitmproxy needs an
# unencrypted key, so route JKS -> PKCS12 (keytool) -> PEMs (openssl).
keytool -importkeystore -noprompt \
  -srckeystore "${CERT_FILES}/takserver.jks" -srcstoretype JKS \
  -srcstorepass "${STOREPASS}" \
  -destkeystore /tmp/tak.p12 -deststoretype PKCS12 -deststorepass "${STOREPASS}"
openssl pkcs12 -in /tmp/tak.p12 -passin "pass:${STOREPASS}" -clcerts -nokeys -out /tmp/tak-cert.pem
openssl pkcs12 -in /tmp/tak.p12 -passin "pass:${STOREPASS}" -nocerts -nodes  -out /tmp/tak-key.pem
cat /tmp/tak-cert.pem /tmp/tak-key.pem > "${OUT}/server-bundle.pem"

# --- client bundle (cert mitm presents UPSTREAM for mTLS) -------------------
# mitm-proxy.pem/.key are a fresh matching pair from makeCert.sh; the key is
# stored encrypted (PASS, default atakatak), so decrypt before concatenating.
openssl pkey -in "${CERT_FILES}/${MITM_NAME}.key" -passin "pass:${KEYPASS}" -out /tmp/mitm.key.dec
cat "${CERT_FILES}/${MITM_NAME}.pem" /tmp/mitm.key.dec > "${OUT}/mitm-client.pem"

cp "${CERT_FILES}/ca.pem" "${OUT}/ca.pem"
rm -f /tmp/tak.p12 /tmp/tak-cert.pem /tmp/tak-key.pem /tmp/mitm.key.dec

# 644 (not 600): the non-root mitmproxy container user (UID ~1000) must read
# these. capture/ is gitignored + local-dev-only, so world-readable is fine.
chmod 644 "${OUT}/server-bundle.pem" "${OUT}/mitm-client.pem" "${OUT}/ca.pem"

touch "${OUT}/ready"
echo "[init-capture] capture certs ready in ${OUT}"

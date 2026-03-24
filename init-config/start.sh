#!/bin/sh
# FastTAK init — patches CoreConfig.xml and generates certs.
# Runs once before tak-server starts. All config in one pass.
set -e

TAK_DIR="/opt/tak"
CONFIG="${TAK_DIR}/CoreConfig.xml"
TEMPLATE="${TAK_DIR}/CoreConfig.example.xml"
CERT_DIR="${TAK_DIR}/certs"
CERT_FILES="${CERT_DIR}/files"

# ── Validate inputs ─────────────────────────────────────────────────────────

for var in FQDN TAK_DB_PASSWORD; do
  eval val=\$$var
  if [ -z "$val" ]; then
    echo "[init] ERROR: $var is not set. Check your .env file." >&2
    exit 1
  fi
done

if [ ! -d "${TAK_DIR}" ]; then
  echo "[init] ERROR: ${TAK_DIR} not found. Did you run setup.sh?" >&2
  exit 1
fi

if [ -n "${FQDN}" ]; then
  case "${FQDN}" in
    *[!a-zA-Z0-9._-]*) echo "[init] ERROR: FQDN contains invalid characters" >&2; exit 1 ;;
  esac
fi

if [ -n "${TAK_DB_PASSWORD}" ]; then
  case "${TAK_DB_PASSWORD}" in
    *[\"\'\&\<\>\|]*) echo "[init] ERROR: TAK_DB_PASSWORD contains unsafe characters (\", ', &, <, >, |)" >&2; exit 1 ;;
  esac
fi

if [ -n "${LDAP_BIND_PASSWORD}" ]; then
  case "${LDAP_BIND_PASSWORD}" in
    *[\"\'\&\<\>]*) echo "[init] ERROR: LDAP_BIND_PASSWORD contains XML-unsafe characters (\", ', &, <, >)" >&2; exit 1 ;;
  esac
fi

# ── 1. Create CoreConfig.xml from template if needed ─────────────────────────

if [ ! -f "${CONFIG}" ]; then
  if [ -f "${TEMPLATE}" ]; then
    echo "[init] Creating CoreConfig.xml from template"
    cp "${TEMPLATE}" "${CONFIG}"
  else
    echo "[init] ERROR: No CoreConfig template found at ${TEMPLATE}" >&2
    exit 1
  fi
fi

# ── 2. Patch database connection ────────────────────────────────────────────

sed -i 's|jdbc:postgresql://[^/]*/cot|jdbc:postgresql://tak-database:5432/cot|g' "${CONFIG}"

if [ -n "${TAK_DB_PASSWORD}" ]; then
  sed -i '/<connection /s|password="[^"]*"|password="'"${TAK_DB_PASSWORD}"'"|' "${CONFIG}"
  echo "[init] Database password set"
fi

# ── 3. Enable admin UI on 8446 ──────────────────────────────────────────────

if ! grep -q 'enableAdminUI="true"' "${CONFIG}" 2>/dev/null; then
  sed -i 's|_name="cert_https"|_name="cert_https" enableAdminUI="true" enableWebtak="true"|g' "${CONFIG}"
  echo "[init] Admin UI enabled on 8446"
fi

# ── 4. Generate certificates if none exist ───────────────────────────────────

if [ ! -f "${CERT_FILES}/ca.pem" ]; then
  echo "[init] No CA found — generating certificates..."
  export STATE="${STATE:-XX}"
  export CITY="${CITY:-Default}"
  export ORGANIZATIONAL_UNIT="${ORGANIZATIONAL_UNIT:-FastTAK}"
  cd "${CERT_DIR}"
  bash ./makeRootCa.sh --ca-name FastTAK-CA 2>&1 | tail -3
  bash ./makeCert.sh server takserver 2>&1 | tail -2
  bash ./makeCert.sh client admin 2>&1 | tail -2
  bash ./makeCert.sh client nodered 2>&1 | tail -2
  cd /

  echo "[init] Certificates generated"
fi

# ── 5. Create CA signing keystore ────────────────────────────────────────────

if [ ! -f "${CERT_FILES}/ca-signing.jks" ] && [ -f "${CERT_FILES}/ca.pem" ] && [ -f "${CERT_FILES}/ca-do-not-share.key" ]; then
  echo "[init] Creating CA signing keystore..."
  openssl pkcs12 -export \
    -in "${CERT_FILES}/ca.pem" \
    -inkey "${CERT_FILES}/ca-do-not-share.key" \
    -passin pass:atakatak \
    -out "${CERT_FILES}/ca-signing.p12" \
    -name ca \
    -passout pass:atakatak 2>/dev/null && \
  keytool -importkeystore \
    -srckeystore "${CERT_FILES}/ca-signing.p12" \
    -srcstoretype PKCS12 \
    -srcstorepass atakatak \
    -destkeystore "${CERT_FILES}/ca-signing.jks" \
    -deststoretype JKS \
    -deststorepass atakatak \
    -noprompt 2>/dev/null && \
  rm -f "${CERT_FILES}/ca-signing.p12" && \
  echo "[init] CA signing keystore created" || \
  echo "[init] WARNING: Failed to create CA signing keystore"
fi

# ── 6. Add certificateSigning block ──────────────────────────────────────────

if ! grep -q '<certificateSigning CA="TAKServer">' "${CONFIG}" 2>/dev/null && [ -f "${CERT_FILES}/ca-signing.jks" ]; then
  echo "[init] Adding certificateSigning block..."
  sed -i '/<\/Configuration>/i\
    <certificateSigning CA="TAKServer">\
        <certificateConfig>\
            <nameEntries>\
                <nameEntry name="O" value="FastTAK"/>\
                <nameEntry name="OU" value="TAK"/>\
            </nameEntries>\
        </certificateConfig>\
        <TAKServerCAConfig keystore="JKS" keystoreFile="certs/files/ca-signing.jks" keystorePass="atakatak" validityDays="365" signatureAlg="SHA256WithRSA"/>\
    </certificateSigning>' "${CONFIG}"
fi

# ── 7. Create FQDN server cert ──────────────────────────────────────────────
# Generate a server cert matching the FQDN and replace the default takserver
# cert. This avoids patching CoreConfig's keystoreFile reference (which TAK
# Server's config process may overwrite on startup).

if [ -n "${FQDN}" ] && [ "${FQDN}" != "localhost" ] && [ -f "${CERT_FILES}/ca.pem" ]; then
  if [ ! -f "${CERT_FILES}/${FQDN}.jks" ]; then
    echo "[init] Creating server cert for ${FQDN}..."
    export STATE="${STATE:-XX}"
    export CITY="${CITY:-Default}"
    export ORGANIZATIONAL_UNIT="${ORGANIZATIONAL_UNIT:-FastTAK}"
    cd "${CERT_DIR}" && bash ./makeCert.sh server "${FQDN}" 2>&1 | tail -2
    cd /
  fi

  if [ -f "${CERT_FILES}/${FQDN}.jks" ]; then
    echo "[init] Installing ${FQDN} cert as default server cert"
    cp -f "${CERT_FILES}/${FQDN}.jks" "${CERT_FILES}/takserver.jks"
    cp -f "${CERT_FILES}/${FQDN}.pem" "${CERT_FILES}/takserver.pem"
    [ -f "${CERT_FILES}/${FQDN}.p12" ] && cp -f "${CERT_FILES}/${FQDN}.p12" "${CERT_FILES}/takserver.p12"
    # Rename the alias inside the JKS to 'takserver' — TAK Server expects this alias
    keytool -changealias -keystore "${CERT_FILES}/takserver.jks" \
      -storepass atakatak -alias "${FQDN}" -destalias takserver 2>/dev/null || true
  fi
fi

# ── 8. Patch LDAP auth (if LDAP vars are set) ────────────────────────────────

if [ -n "${LDAP_BIND_PASSWORD}" ]; then
  LDAP_HOST="${LDAP_HOST:-authentik-ldap}"
  LDAP_BASE_DN="${LDAP_BASE_DN:-DC=takldap}"
  BASE_DN_LOWER=$(echo "${LDAP_BASE_DN}" | tr '[:upper:]' '[:lower:]')

  if ! grep -q 'adm_ldapservice' "${CONFIG}" 2>/dev/null; then
    echo "[init] Adding LDAP auth block..."

    AUTH_BLOCK='    <auth default="ldap" x509groups="true" x509addAnonymous="false" x509useGroupCache="true" x509useGroupCacheDefaultActive="true" x509checkRevocation="true">\
        <ldap url="ldap://'"${LDAP_HOST}"':3389" userstring="cn={username},ou=users,'"${BASE_DN_LOWER}"'" updateinterval="30" groupprefix="cn=tak_" groupNameExtractorRegex="cn=tak_(.*?)(?:,|$)" serviceAccountDN="cn=adm_ldapservice,ou=users,'"${BASE_DN_LOWER}"'" serviceAccountCredential="'"${LDAP_BIND_PASSWORD}"'" groupBaseRDN="ou=groups,'"${BASE_DN_LOWER}"'" userBaseRDN="ou=users,'"${BASE_DN_LOWER}"'" dnAttributeName="DN" nameAttr="CN" adminGroup="ROLE_ADMIN"/>\
        <File location="UserAuthenticationFile.xml"/>\
    </auth>'

    # shellcheck disable=SC1003  # intentional sed quoting for XML block replacement
    sed -i '/<auth/,/<\/auth>/c\'"${AUTH_BLOCK}" "${CONFIG}"
    echo "[init] LDAP auth configured"
  else
    # LDAP already configured — update password if changed
    CURRENT_PASS=$(grep -o 'serviceAccountCredential="[^"]*"' "${CONFIG}" | sed 's/.*="//;s/"//')
    if [ -n "$CURRENT_PASS" ] && [ "$CURRENT_PASS" != "${LDAP_BIND_PASSWORD}" ]; then
      sed -i 's|serviceAccountCredential="[^"]*"|serviceAccountCredential="'"${LDAP_BIND_PASSWORD}"'"|' "${CONFIG}"
      echo "[init] LDAP password updated"
    else
      echo "[init] LDAP already configured"
    fi
  fi
fi

# ── 9. Upgrade .p12 files to modern ciphers ───────────────────────────────────
# TAK's cert tools use legacy RC2-40 which modern OpenSSL 3.x rejects.
# Re-export all .p12 files with AES-256-CBC after all cert generation is done.

for p12 in "${CERT_FILES}"/*.p12; do
  [ -f "$p12" ] || continue
  # Check if already modern (openssl can read it without -legacy)
  if openssl pkcs12 -in "$p12" -passin pass:atakatak -nokeys -out /dev/null 2>/dev/null; then
    continue
  fi
  # Try extracting with -legacy flag and re-exporting with modern ciphers
  if openssl pkcs12 -in "$p12" -passin pass:atakatak -nokeys -out /tmp/cert.pem -legacy 2>/dev/null; then
    if openssl pkcs12 -in "$p12" -passin pass:atakatak -nocerts -nodes -out /tmp/key.pem -legacy 2>/dev/null && [ -s /tmp/key.pem ]; then
      # Has private key — full re-export
      openssl pkcs12 -export -in /tmp/cert.pem -inkey /tmp/key.pem -out "$p12" \
        -passout pass:atakatak -certpbe AES-256-CBC -keypbe AES-256-CBC -macalg SHA256 2>/dev/null
    else
      # Cert-only (e.g., truststore) — re-export without key
      openssl pkcs12 -export -in /tmp/cert.pem -nokeys -out "$p12" \
        -passout pass:atakatak -certpbe AES-256-CBC -macalg SHA256 2>/dev/null
    fi
    rm -f /tmp/cert.pem /tmp/key.pem
  fi
done

echo "[init] CoreConfig.xml ready"

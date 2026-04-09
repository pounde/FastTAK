#!/bin/sh
# wait-for-ldap.sh — Block until the LDAP outpost is actually serving queries.
#
# The ldap-proxy Docker healthcheck (ping endpoint on :9300) reports healthy
# before the LDAP protocol on :3389 is ready. If TAK Server starts in that gap,
# the FileAuthenticator captures cert-based connections with __ANON__ groups,
# creating a permanent mismatch with LDAP that causes t-x-g-c spam.
#
# This init container performs an actual LDAP bind + search with the service
# account and only exits 0 when it succeeds.
set -e

LDAP_HOST="${LDAP_HOST:-ldap-proxy}"
LDAP_PORT="${LDAP_PORT:-3389}"
LDAP_BASE_DN="${LDAP_BASE_DN:-DC=takldap}"
BASE_DN_LOWER=$(echo "${LDAP_BASE_DN}" | tr '[:upper:]' '[:lower:]')
BIND_DN="uid=adm_ldapservice,ou=people,${BASE_DN_LOWER}"
MAX_WAIT="${LDAP_READY_TIMEOUT:-120}"
INTERVAL=5
WAITED=0

echo "[init-ldap-ready] Waiting for LDAP at ${LDAP_HOST}:${LDAP_PORT}..."

while true; do
  if ldapsearch -x \
       -H "ldap://${LDAP_HOST}:${LDAP_PORT}" \
       -D "${BIND_DN}" \
       -w "${LDAP_BIND_PASSWORD}" \
       -b "ou=people,${BASE_DN_LOWER}" \
       -s base "(objectClass=*)" dn \
       >/dev/null 2>&1; then
    echo "[init-ldap-ready] LDAP is serving queries (waited ${WAITED}s)"
    exit 0
  fi

  WAITED=$((WAITED + INTERVAL))
  if [ "${WAITED}" -ge "${MAX_WAIT}" ]; then
    echo "[init-ldap-ready] ERROR: LDAP not ready after ${MAX_WAIT}s" >&2
    exit 1
  fi

  sleep "${INTERVAL}"
done

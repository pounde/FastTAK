ARG LLDAP_VERSION=v0.6.1
FROM lldap/lldap:${LLDAP_VERSION} AS lldap

FROM python:3.12-alpine

# Copy lldap_set_password for OPAQUE-based password setting
COPY --from=lldap /app/lldap_set_password /usr/local/bin/lldap_set_password

COPY bootstrap.py /app/bootstrap.py

ENTRYPOINT ["python", "-u", "/app/bootstrap.py"]

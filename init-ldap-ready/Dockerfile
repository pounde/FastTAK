FROM alpine:3.20

RUN apk add --no-cache openldap-clients

COPY wait-for-ldap.sh /wait-for-ldap.sh
RUN chmod +x /wait-for-ldap.sh

ENTRYPOINT ["/wait-for-ldap.sh"]

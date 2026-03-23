#!/bin/bash
# Set the DB password in CoreConfig before the setup script reads it.
# The shared bind-mount means init-config will also patch this, but
# tak-database starts first and needs the password immediately.

if [ -n "${TAK_DB_PASSWORD}" ]; then
  for f in /opt/tak/CoreConfig.xml /opt/tak/CoreConfig.example.xml; do
    [ -f "$f" ] && sed -i '/<connection /s|password="[^"]*"|password="'"${TAK_DB_PASSWORD}"'"|' "$f"
  done
fi

exec /opt/tak/db-utils/configureInDocker.sh

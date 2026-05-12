# Backup and Restore

FastTAK can produce an encrypted backup tarball that captures every piece of
state needed to rebuild a deployment on a fresh host: both Postgres
databases, all TAK certificates and keys, the Node-RED data volume, and
your `.env`. Restoring is a documented manual procedure — there is no
"one button restore" because every restore should be done with a human
reading every step.

## What gets backed up

- TAK Server database (`cot` on `tak-database`) — CoT history, mission data,
  data packages.
- LLDAP database (`lldap` on `app-db`) — users and groups.
- Node-RED database (`nodered` on `app-db`) — data your flows have stored in Postgres.
- FastTAK audit database (`fastak` on `app-db`) — the audit/health event store.
- TAK certificates and CA key (`tak/certs/`) — without these, every device
  must be re-enrolled.
- TAK Server config (`tak/CoreConfig.xml`, `tak/TAKIgniteConfig.xml`) —
  operator customization (federation, channels, auth tweaks, Ignite
  cluster settings). Without these, the restored stack reverts to
  defaults derived from the `*.example.xml` files.
- Node-RED data volume (`nodered-data`) — flow definitions, credentials,
  installed packages, monitor-written cert PEMs.
- `.env` — the entire stack's configuration, including secrets.

**Not** backed up:

- Caddy data — TLS certificates re-issue on first boot.
- The FastTAK source tree — already in version control.
- The extracted `tak/` directory minus `certs/` — re-extracted from the
  TAK Server release zip via `setup.sh`.

## First-time setup

1. Start the stack normally (`./start.sh` or `just up`).
2. Visit the **Backups** page in the monitor dashboard (you must be a
   member of the `monitor_admin` LDAP group — the bootstrap auto-creates
   this group and adds `webadmin` to it on first boot, so the default
   admin account works out of the box).
3. Click **Run backup now**. Wait for it to complete — depending on data
   size this may take anywhere from a few seconds to several minutes.
4. Click **Download backup key**. **This is critical.** The age private
   key is what makes your backups recoverable. Save it somewhere off this
   host — a password manager, a thumb drive, a separate machine.
5. Click **Download** on the new backup tarball. Save it off-host too.

After your first key download, the warning banner disappears.

You can re-download the key any time. There is no rotation — the same
identity encrypts every backup.

## Routine operations

**Manually:** `just backup` from the host runs a backup via the monitor
container.

**Scheduled:** add the operator's preferred cron entry on the host:

```cron
0 3 * * *   cd /path/to/FastTAK && just backup >> /var/log/fasttak-backup.log 2>&1
```

**From the dashboard:** the **Run backup now** button on the **Backups**
page kicks off a backup in the background. The status indicator polls
every 5 seconds while it's running.

**Retention** keeps the newest 14 backups by default. Override via
`BACKUP_RETENTION_KEEP` in `.env`. Stale `.partial` files older than 6
hours are also reaped — the default covers a multi-GB `cot` dump
without prematurely clobbering an in-flight backup. Operators with
larger DBs (or stricter cleanup) can override via
`BACKUP_PARTIAL_REAP_AGE_SECONDS` in `.env`.

**Manual prune** is available via `just backup-prune` (uses the
`BACKUP_RETENTION_KEEP` default) or `just backup-prune 5` (override to
keep the newest 5). Useful for one-off cleanup before a release; routine
ops should not need it.

**List backups** with `just backups` (lists what's on disk with sizes
and ages).

## Restoring to a fresh host

> Before you start, you need: the `.age-identity` file (your key), at
> least one backup tarball, and a host with Docker and Compose ready,
> with FastTAK already cloned.

The canonical procedure is also implemented as a shell script that the
integration test invokes — see `tests-integration/restore.sh`. The steps
below mirror that script.

1. **Extract the TAK Server release**

   Extract the matching TAK Server release so `tak/` and a fresh `.env`
   exist before any container starts:

   ```bash
   ./setup.sh <takserver-docker-X.X.zip>
   ```

2. **Verify and decrypt the backup**

   **2a. Verify the archive (optional but recommended).** Every successful
   backup writes a `<archive>.sha256` sidecar in `sha256sum -c` format. If
   you copied it off-host alongside the archive, verify it before
   decrypting:

   ```bash
   sha256sum -c fasttak-backup-...age.sha256
   ```

   **2b. Decrypt.**

   ```bash
   age -d -i fasttak-backup-key.txt fasttak-backup-...age | tar xz -C /tmp/restore
   ```

   This produces `/tmp/restore/MANIFEST.json`, `/tmp/restore/postgres/`,
   `/tmp/restore/tak-certs.tar`, `/tmp/restore/tak-config.tar`,
   `/tmp/restore/nodered-data.tar`, and `/tmp/restore/env`.

3. **Inspect the manifest**

   ```bash
   cat /tmp/restore/MANIFEST.json
   ```

   Confirm `postgres_versions` is compatible with what your fresh stack
   will run. A major-version downgrade (e.g. restoring a `cot` dump from
   PG16 onto a PG15 stack) will fail.

4. **Replace `.env`**

   ```bash
   cp /tmp/restore/env .env
   ```

5. **Restore TAK certificates and config**

   ```bash
   mkdir -p tak/certs
   tar -x -C tak/certs -f /tmp/restore/tak-certs.tar
   # CoreConfig.xml and any other operator-customized tak/ files
   tar -x -C tak -f /tmp/restore/tak-config.tar
   ```

6. **Restore the Node-RED volume**

   The compose-project labels matter — without them, the next
   `docker compose down -v` won't recognize this volume as part of the
   project and will leak it across runs. `<project>` is your compose
   project name (typically `fasttak`); confirm with `docker volume ls`
   if you've overridden it.

   ```bash
   PROJECT=fasttak
   docker volume create \
       --label "com.docker.compose.project=${PROJECT}" \
       --label "com.docker.compose.volume=nodered-data" \
       "${PROJECT}_nodered-data"
   docker run --rm -i -v "${PROJECT}_nodered-data:/data" alpine \
       sh -c 'cd /data && tar x' < /tmp/restore/nodered-data.tar
   ```

7. **Start the database services only**

   ```bash
   docker compose up -d tak-database app-db
   ```

   Wait for both to be healthy:

   ```bash
   docker compose ps tak-database app-db
   ```

8. **Restore the databases**

   > **Warning — this drops databases.** The commands below run
   > `DROP DATABASE … WITH (FORCE)`, which terminates any live
   > connections (LLDAP, Node-RED, the monitor) and removes the existing
   > database before restoring. **On a fresh restore host this is what
   > you want.** On a recovery against a partially-running stack, stop
   > dependent services first (`docker compose stop lldap nodered monitor`)
   > so you do not interrupt active sessions.

   The dumps are `--format=plain` (no `--clean`), so the target databases
   must exist and be empty before restore. We drop and recreate each one,
   then pipe the dump in. `psql -h localhost` is required because Unix
   socket auth in these images falls through to peer auth and rejects
   `martiuser`/`fastak`.

   ```bash
   # cot (TAK Server)
   docker compose exec -T tak-database \
       sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d postgres \
              -c "DROP DATABASE IF EXISTS cot WITH (FORCE);" \
              -c "CREATE DATABASE cot OWNER martiuser;"'
   docker compose exec -T tak-database \
       sh -c 'PGPASSWORD="$TAK_DB_PASSWORD" psql -h localhost -U martiuser -d cot' \
       < /tmp/restore/postgres/cot.sql

   # app-db (lldap, nodered, fastak)
   for db in lldap nodered fastak; do
       docker compose exec -T app-db \
           sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -h localhost -U fastak -d postgres \
                  -c \"DROP DATABASE IF EXISTS $db WITH (FORCE);\" \
                  -c \"CREATE DATABASE $db OWNER fastak;\""
       docker compose exec -T app-db \
           sh -c "PGPASSWORD=\"\$POSTGRES_PASSWORD\" psql -h localhost -U fastak -d $db" \
           < /tmp/restore/postgres/$db.sql
   done
   ```

   `WITH (FORCE)` kicks out live connections (e.g. LLDAP holds an open
   connection to `lldap`). Services reconnect after the `CREATE DATABASE`
   completes. For a quieter operation, stop dependent services
   (`docker compose stop lldap nodered monitor`) before this step and
   start them again after.

   For the exact procedure used by the integration test, see
   `tests-integration/restore.sh` in the repo.

9. **Start the rest of the stack**

   ```bash
   ./start.sh
   ```

10. **Post-restore checks**

   - Log in to the monitor dashboard with an LLDAP user from before the
     restore.
   - Confirm a known TAK client cert is still listed under Service
     Accounts.
   - Confirm a Node-RED flow you remember is still present at
     `https://nodered.<your-server>`.
   - Check `fastak_events` rows: `docker compose exec app-db psql -U fastak
     -d fastak -c 'SELECT COUNT(*) FROM fastak_events'`. Should match
     roughly what you had before the restore.

## Caveats

- **Backups co-located with data are not really backups.** The default
  `BACKUP_DIR` is on the same disk as the deployment. A single-disk
  failure loses both. Treat the dashboard "Download" button as your
  off-host sync.
- **Cross-database consistency is not guaranteed.** The two Postgres
  instances are dumped sequentially; a few seconds may pass between
  them. A user can be created in LLDAP between dumps and not yet have a
  matching cert. Take backups during low-activity periods.
- **Age key off-host requirement.** If the host dies and the key was only
  on it, the backups are decorative. Save the key somewhere durable and
  separate.
- **Postgres version skew.** A major-version downgrade between backup and
  restore will fail. The manifest records the producing version.
- **`.env` is a credential.** The archive contains every secret your
  stack uses. The age encryption is what keeps that safe at rest. Treat
  the unencrypted form on a restore host with care.
- **Concurrency lock is per-`BACKUP_DIR`, not per-stack.** If two
  FastTAK deployments on the same host share `BACKUP_DIR` (uncommon —
  typically each stack uses its own), the second `just backup` blocks
  until the first finishes. The default `BACKUP_DIR=./backups` is
  per-checkout, so this only matters if you explicitly point multiple
  stacks at one directory.

## Troubleshooting

- **"Permission denied" on `/api/backup/*`** — your user isn't in the
  backup admin group. The bootstrap creates `monitor_admin` (or whatever
  `BACKUP_ADMIN_GROUP` is set to in `.env`) and adds the `webadmin` user
  to it on first boot. Any additional admins must be added to the same
  group via LLDAP.
- **"a backup is already in progress"** — another `just backup` or
  dashboard click is mid-run. Wait for it to finish, then retry.
- **`pg_dump: server version mismatch`** — your monitor image's
  `pg_dump` is older than the database server. Rebuild the monitor image
  after updating `postgresql-client` to a matching or newer major in
  `monitor/Dockerfile`.
- **`/host/.env` missing during a backup** — the compose bind mount of
  `.env` isn't in place. Check `docker-compose.yml` for
  `./.env:/host/.env:ro` on the monitor service.
- **Backup files appear root-owned on the host** — the monitor container
  writes them under its container uid (root by default), so removing
  them from the host directly may require `sudo`. Prefer the dashboard
  **Delete** button or `just backup-prune`, which run inside the
  container.

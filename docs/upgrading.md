# Upgrading FastTAK

Most upgrades are a `git pull` and a restart. `./setup.sh` pulls the new
release and preserves your `.env`, your certificates and your `CoreConfig.xml`;
`./start.sh` brings the stack back up on the new images.

Crossing the **TAK Server 5.8 boundary** is the exception. It starts both
databases empty, and it is not reversible — read
[Upgrading to TAK Server 5.8](#upgrading-to-tak-server-58-fasttak-v029) in full
before you begin.

This page covers the parts that are **not** automatic: the ones that depend on
decisions you made, which no script can make for you.

## Before you upgrade

### 1. Check who can reach the Monitor (v0.28.1 and later)

As of v0.28.1 the Monitor is admin-only, end to end. Every JSON API and every
dashboard page requires membership in `ADMIN_GROUP` (default `monitor_admin`);
only `/api/ping` is open. See [Authentication](authentication.md#authorization-who-may-use-the-monitor).

`webadmin` is added to that group automatically by the identity bootstrap, so if
that is the account you log in with, you keep access.

**Any other admin account must be added to the group before you upgrade**, or it
will get a 403 on every page afterwards. Add users to `monitor_admin` from the
Monitor's Users page, or directly in LLDAP.

!!! warning "Renaming `ADMIN_GROUP`"
    If you set `ADMIN_GROUP` to a custom name, make sure the stack has been
    restarted at least once on v0.28.2 or later. Earlier versions bootstrapped
    only `BACKUP_ADMIN_GROUP`, so a renamed `ADMIN_GROUP` was never created and
    nobody was ever added to it — locking every account out of the Monitor with
    no way back in through the UI. Recovery is to add the group and membership
    in LLDAP directly, or to unset `ADMIN_GROUP` and restart.

### 2. Know what a version bump changes

Check the release notes for the versions you are skipping. FastTAK follows
semantic versioning, and `fix:` releases will not change behaviour you depend on
— but a service being *removed* from the stack is worth knowing about in
advance, because of the next item.

## Upgrading to TAK Server 5.8 (FastTAK v0.29+)

FastTAK v0.29 requires the **hardened** TAK Server bundle, version 5.8 or
later — `takserver-docker-hardened-5.8-RELEASE-65.zip` or newer from
[tak.gov](https://tak.gov/products/tak-server). `setup.sh` refuses anything
older, and `scripts/check-env.sh` refuses a `TAK_VERSION` in `.env` that is
below the floor or unparseable, because earlier releases put the PostgreSQL
data directory outside the volume FastTAK mounts and the database would not
survive a container recreate. See
[DD-051](decisions.md#dd-051-tak-server-58-hardened-bundle-is-the-supported-floor).

TAK 5.8 moves to PostgreSQL 18, and FastTAK moves `app-db` with it. Neither
existing volume can be started on the new images — an 18 server refuses a 15
data directory outright — so **this upgrade starts both databases empty**.
FastTAK does not migrate them for you. There is no `just upgrade`; the
automation that used to be here was removed, and why is recorded in
[DD-055](decisions.md#dd-055-no-automated-cross-major-database-upgrade-yet).

Read [What survives and what does not](#what-survives-and-what-does-not) before
you start.

### Procedure

```bash
# 1. Take a backup and confirm it is on disk. This is the only copy of the old
#    databases; nothing below restores it for you. BACKUP_DIR (default
#    ./backups) is a host directory, so step 3 does not touch it — but copy the
#    archive off the host anyway if the deployment matters.
just backup && just backups

# 2. Pull the new FastTAK release and rebuild the TAK images from the hardened
#    bundle. This preserves .env, tak/certs/, CoreConfig.xml and
#    UserAuthenticationFile.xml, and updates TAK_VERSION in .env.
git pull
./setup.sh takserver-docker-hardened-5.8-RELEASE-65.zip

# 3. Remove the old database volumes. THIS IS THE IRREVERSIBLE STEP.
docker compose down -v

# 4. Start the stack. Both databases initialise empty.
./start.sh
```

`setup.sh` prints this same sequence at the end of an upgrade run.

!!! warning "`-v` is the whole point of step 3"
    Without it the old volumes stay and `app-db` will not start at all: an 18
    server refuses a PostgreSQL 15 data directory, and the container restart-
    loops. Nothing is corrupted by the attempt — it just does not come up until
    the volume is gone.

### What survives and what does not

The dividing line is the host filesystem. Everything under `tak/` is a bind
mount and is untouched by `down -v`; everything in a named Docker volume is
removed.

**Survives** — host files under `tak/`, preserved by `setup.sh`:

| What | Where |
| ---- | ----- |
| The CA (`ca.pem`, `ca-do-not-share.key`) and every issued client cert | `tak/certs/` |
| `CoreConfig.xml` — your whole TAK Server configuration | `tak/CoreConfig.xml` |
| `UserAuthenticationFile.xml` | `tak/UserAuthenticationFile.xml` |
| `.env`, including every generated secret | repository root |
| Backup archives | `$BACKUP_DIR` (default `./backups`) |

Enrolled ATAK/WinTAK clients keep working: they authenticate with certificates
issued by a CA that did not change.

**Lost** — named volumes removed by `down -v`:

| What | Volume |
| ---- | ------ |
| The `cot` database — all CoT history and mission data | `tak-db-data` |
| The `lldap`, `nodered` and `fastak` databases — LDAP accounts and groups, Node-RED's own store, the Monitor's audit and health history | `app-db-data` |
| Node-RED flows and installed palette nodes | `nodered-data` |
| Caddy's Let's Encrypt certificates and ACME account | `caddy-data` |

!!! note "On a pre-5.8 deployment the `cot` database was never on that volume"
    That is the persistence defect
    [DD-051](decisions.md#dd-051-tak-server-58-hardened-bundle-is-the-supported-floor)
    describes: before 5.8, PGDATA sat inside the `tak-database` container's
    writable layer rather than on `tak-db-data`. Step 2 changes the image tag,
    so the CoT history ends with that container whether or not you pass `-v`.
    The `-v` is what clears `app-db-data`.

Two of the losses recover on their own:

- **`webadmin`.** `init-identity` recreates it from `TAK_WEBADMIN_PASSWORD` in
  `.env` on every boot and joins it to the gate groups, so admin access to the
  Monitor and to TAK Server comes back by itself. **Every other human account
  has to be recreated by hand** — they were in LLDAP, which is now empty.
- **Caddy's TLS certificates.** Caddy re-issues on the next start. In
  subdomain mode that means a fresh ACME order per hostname, which counts
  against [Let's Encrypt's rate limits](https://letsencrypt.org/docs/rate-limits/)
  — relevant if you have already been reissuing today.

Node-RED's flows and the pre-installed `node-red-contrib-postgresql` /
`node-red-contrib-tak` palette are re-provisioned from the image on first
boot, but any flow **you** built is in the backup archive and nowhere else.

### Restoring the app-db databases afterwards (optional)

If the LLDAP accounts, Node-RED flows or audit history are worth recovering,
`tests-integration/restore.sh` can put the `lldap`, `nodered` and `fastak`
databases back from the archive taken at step 1, and
[Backup and restore](backup-and-restore.md) documents the by-hand equivalent.
This is optional. Skip it if the data is expendable — that is the case this
procedure is written for.

!!! danger "A pre-v0.29 archive carries a below-floor `TAK_VERSION`"
    `restore.sh` replaces `.env` wholesale with the archive's copy, because
    the restored databases' role passwords have to match. An archive taken
    before v0.29 also carries `TAK_VERSION=5.6-RELEASE-6` in that copy — so
    the restore asks Compose for `takserver-database:5.6-RELEASE-6`, which
    does not exist on a 5.8 host, and every later `./start.sh` fails the
    version floor. Nothing reconciles it for you. See
    [issue #99](https://github.com/pounde/FastTAK/issues/99); until it is
    fixed, re-set `TAK_VERSION` in `.env` by hand after any such restore.

    Restoring only the `cot` database has the same problem and none of the
    benefit — the whole premise of this upgrade is that the CoT history does
    not carry across.

### Building the images needs network access

The hardened images install packages from the Rocky, EPEL, Adoptium and PGDG
repositories during the build. Earlier bundles built from `postgres:15.1` with
two extra packages, so a build that previously worked behind a restrictive
egress policy may not any more.

## What the scripts now handle for you

These used to be manual steps and are no longer:

| Rule | Where it is enforced |
| ---- | -------------------- |
| Remove containers for services deleted from the compose file | `--remove-orphans` in `start.sh`, `reconfig.sh`, and `just up` |
| Generate any newly required secrets on a fresh install | `setup.sh` |
| Create and populate the Monitor's gate groups | `init-identity` on every stack start |
| Refuse to start on a missing or unsafe `.env` value | `scripts/check-env.sh`, called from `start.sh` |

### Why `--remove-orphans` matters

`docker compose up` does **not** remove a container whose service has been
deleted from the compose file. It keeps running on its old configuration,
invisible to `docker compose ps`, surviving every subsequent upgrade.

This is not hypothetical. `tak-portal` was removed from the stack in
[DD-043](decisions.md) because it had become unauthenticated after Authentik was
replaced — and on at least one deployment the container kept running for months
afterwards, because nothing removed it.

If you upgraded before this was fixed, check once:

```bash
docker ps --format '{{.Names}}'
```

Anything not in `docker compose config --services` is an orphan. Remove it:

```bash
docker compose up -d --remove-orphans
```

## After you upgrade

Confirm the stack is healthy and that you can still get in:

```bash
docker compose ps            # every service up; none unexpected
curl -sf http://localhost:8080/api/ping   # from inside the monitor container
```

Then load the Monitor dashboard in a browser and confirm the pages render. A 403
on every page means the account you are logged in with is not in `ADMIN_GROUP` —
see step 1 above.

## Recovering from a Monitor lockout

If every page 403s and you have no admin account left in the group, you can fix
it from LLDAP without the Monitor. LLDAP's own web UI is not exposed by default,
so work through the container:

1. Confirm which group the Monitor is gating on — the value of `ADMIN_GROUP` in
   `.env`, or `monitor_admin` if unset.
2. Add your account to that group in LLDAP.
3. Restart the monitor so nothing is cached: `docker compose restart monitor`.

The group name is read per request, so renaming it in `.env` takes effect on the
next request after a monitor restart — no rebuild needed.

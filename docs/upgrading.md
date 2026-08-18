# Upgrading FastTAK

Most of an upgrade is automated. Run `./setup.sh` to pull the new release, then
`./start.sh` to bring the stack up — `setup.sh` preserves your `.env`, and
`start.sh` runs `scripts/check-env.sh` as a preflight before anything starts.

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

TAK 5.8 moves to PostgreSQL 18, and FastTAK moves `app-db` with it. Both
databases therefore need migrating.

### Procedure

```bash
# 1. Take a backup and confirm it exists. `just upgrade` also takes its own
#    backup before doing anything destructive and aborts if that fails, but
#    an explicit backup first costs nothing.
just backup && just backups

# 2. Pull the new FastTAK release and rebuild the TAK images.
git pull
./setup.sh takserver-docker-hardened-5.8-RELEASE-65.zip

# 3. Migrate the databases and restart the stack.
just upgrade
```

!!! warning "Do not restart the stack between `git pull` and `just upgrade`"
    `just upgrade` takes its backup through the *running* stack, and it
    requires `tak-database` and `monitor` to already be up when it starts.
    Once `git pull` updates `docker-compose.yml` to name `postgres:18-alpine`,
    restarting `app-db` fails outright against its still-PostgreSQL-15
    volume — an 18 server refuses a 15 data directory — and there is then no
    way to take the backup through a stack that will not start. If this
    happens, `git checkout <previous-tag>`, start the stack, then check back
    out and re-run `just upgrade`.

### Disk space

Migrating the CoT database dumps it and restores the dump alongside the
original, so it needs free space. `just upgrade` requires **1.5× the measured
`cot` database size** to be free (on the filesystem holding `BACKUP_DIR`)
before it starts, matching the check in tak.gov's own
`db-utils/upgrade-db.sh`, and aborts rather than filling the disk partway
through a restore.

This check does **not** cover `$TMPDIR` (where the decrypted, uncompressed
dump is extracted — often larger than the live database) or Docker's data
root (where the restored copy lands). Either can fill independently of the
check passing.

!!! warning "The stack is down for the whole migration"
    A multi-GB `cot` database can take a long time to dump and restore, and
    TAK Server is unavailable throughout. Check the reported size before
    starting and schedule accordingly. The timings are not yet benchmarked at
    realistic sizes — see [issue #98](https://github.com/pounde/FastTAK/issues/98).

### Discarding the CoT history

CoT history is **migrated by default**. If it is not worth carrying across for
this hop:

```bash
just upgrade --skip-cot
```

This recreates the `cot` database empty and skips the dump and restore
entirely, which is much faster. `--skip-cot` only affects CoT history —
**`app-db` (every LDAP account, Node-RED flow and audit record) is migrated
either way**, and the pre-upgrade backup is always taken regardless of the
flag.

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

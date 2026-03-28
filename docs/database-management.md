# FastTAK Database Management

## What Data Retention Is

By default, TAK Server keeps everything forever. Every position report, geochat message, and mission package that flows through your server accumulates in the database. On a quiet TAK network this isn't a problem. On a busy one — or a small VPS — the database will eventually fill your disk.

TAK Server ships a **retention service** that periodically deletes old data based on configurable thresholds. FastTAK exposes this through `.env` variables so you can enable it without editing XML.

The retention service manages five data types:

| Type              | What it is                                                                | Default      |
| ----------------- | ------------------------------------------------------------------------- | ------------ |
| `cot`             | CoT messages — position reports, sensor data, all cursor-on-target events | keep forever |
| `geochat`         | In-app chat messages                                                      | keep forever |
| `files`           | File attachments                                                          | keep forever |
| `missionpackages` | Mission packages                                                          | keep forever |
| `missions`        | Missions                                                                  | keep forever |

FastTAK exposes `.env` variables for `cot` and `geochat` because those are the two high-volume types that cause disk growth. Files, mission packages, and missions are intentionally created data that most users don't want to expire automatically — but you can configure them if needed (see [Power User Settings](#power-user-settings)).

---

## How Retention Affects Vacuuming

When the retention service deletes old rows, PostgreSQL doesn't immediately reclaim that disk space. Instead, deleted rows become **dead tuples** — they're marked invisible but still occupy space on disk. PostgreSQL's **autovacuum** daemon runs in the background to clean these up and make the space reusable.

This works fine for normal workloads. The problem is PostgreSQL's default autovacuum settings are tuned conservatively: it only triggers when 20% of a table's rows are dead, and it throttles itself heavily to avoid impacting query performance. On the CoT tables — which can have millions of rows and receive bulk deletes from retention — this means dead tuples can pile up faster than autovacuum cleans them. The table bloats, queries slow down, and disk usage stays high even though the data has been "deleted."

This isn't a FastTAK-specific problem. It's a well-known PostgreSQL tuning concern for high-write, bulk-delete workloads.

---

## How FastTAK Handles Vacuuming

FastTAK ships tuned autovacuum defaults in `tak-database/start.sh`, applied automatically at container startup. The defaults:

```bash
autovacuum_vacuum_scale_factor = 0.05    # trigger at 5% dead rows (PG default: 0.2)
autovacuum_vacuum_cost_limit = 1000      # 5x faster cleanup (PG default: 200)
maintenance_work_mem = 256MB             # more memory per vacuum worker (PG default: 64MB)
```

These three settings together mean autovacuum triggers earlier, runs faster, and has more memory to work with. **These are applied automatically** — you don't need to set anything to get them.

You can override these defaults via `.env` if needed (see [Power User Settings](#power-user-settings)). If you want stock PostgreSQL behavior, explicitly set the override variables to `0.2`, `200`, `64MB`.

A note on memory: `PG_MAINTENANCE_WORK_MEM` is only allocated during active vacuum operations. PostgreSQL runs up to 3 autovacuum workers simultaneously, so worst case is 3 × 256MB = 768MB during heavy cleanup. On a deployment with 2GB total RAM, you may want to lower this to 128MB — the scale factor and cost limit changes do most of the heavy lifting.

Tuned autovacuum handles routine dead tuple cleanup automatically and produces the same result as running VACUUM manually. **No manual VACUUM is needed.** VACUUM FULL is the one manual operation that matters: it rewrites tables to reclaim actual disk space back to the OS after a large deletion. See [Reclaiming disk space](#reclaiming-disk-space) for when to use it.

After changing autovacuum override variables, restart the database container:

```bash
docker compose up -d --force-recreate tak-database
```

---

## Quick Start

Add these to your `.env` and recreate the affected containers:

```bash
COT_RETENTION_DAYS=30
GEOCHAT_RETENTION_DAYS=90
# RETENTION_CRON defaults to daily at 3 AM — no need to set it
```

```bash
docker compose up -d --force-recreate init-config tak-server
```

That's it for most deployments. The rest of this doc covers tuning and troubleshooting.

---

## VACUUM FULL

Tuned autovacuum handles routine cleanup continuously — it reclaims dead tuple space for PostgreSQL to reuse, keeping query performance healthy. This is sufficient for ongoing steady-state operation.

VACUUM FULL is different: it rewrites the entire table, shrinking the database file on disk and returning space to the OS. That has a real use case and a real cost:

- **When it's useful:** After a large one-time deletion — for example, enabling retention for the first time on a database with months of accumulated data. The initial retention run may delete tens of millions of rows; autovacuum will eventually clean these up, but VACUUM FULL reclaims disk space immediately.
- **The cost:** VACUUM FULL holds an exclusive table lock for its duration. All TAK clients will disconnect and be unable to send or receive data. On a table with tens of millions of rows, this can take 5–15 minutes. Schedule it during a maintenance window.

---

## Power User Settings

### Autovacuum overrides

FastTAK applies tuned autovacuum defaults automatically. If you need to adjust them, set these in `.env` and restart `tak-database`:

```bash
PG_AUTOVACUUM_SCALE_FACTOR=0.05    # fraction of live rows that triggers autovacuum (default: 0.05)
PG_AUTOVACUUM_COST_LIMIT=1000      # I/O cost limit per autovacuum cycle (default: 1000)
PG_MAINTENANCE_WORK_MEM=256MB      # memory per vacuum worker (default: 256MB)
```

### Retention for files, mission packages, and missions

These types aren't exposed as `.env` variables because they're low-volume and most users don't want them to expire. To configure retention for them, edit `tak/conf/retention/retention-policy.yml` directly:

```yaml
dataRetentionMap:
  cot: 90 # days, or null to keep forever
  geochat: 30
  files: null # set a number here if you want file expiry
  missionpackages: null
  missions: null
```

**Note:** `init-config` only writes `retention-policy.yml` and `retention-service.yml` when retention variables (`COT_RETENTION_DAYS`, `GEOCHAT_RETENTION_DAYS`, or `RETENTION_CRON`) are set in `.env`. If none are set, the existing YAML files are preserved — you can edit them directly without init-config overwriting your changes.

### Custom retention schedule

The retention service runs on a Quartz cron schedule. FastTAK automatically sets it to daily at 3 AM (`0 0 3 * * ?`) when any retention days are configured. To run at a different time:

```bash
RETENTION_CRON=0 0 1 * * ?    # 1 AM daily
RETENTION_CRON=0 0 3 ? * SUN  # 3 AM every Sunday
```

The format is Quartz cron, which has a seconds field: `second minute hour day-of-month month day-of-week [year]`.

---

## API Reference

All health data is served from an in-memory cache that the scheduler populates on a configured interval. The live endpoints are available for debugging.

### Cached health endpoints

| Endpoint                      | Description                                                          |
| ----------------------------- | -------------------------------------------------------------------- |
| `GET /api/health`             | Full health cache — all modules with status and data                 |
| `GET /api/health?view=status` | Summary view — status and top-level values only, no per-table detail |

### Live diagnostic endpoints

| Endpoint                     | Description                                                               |
| ---------------------------- | ------------------------------------------------------------------------- |
| `GET /api/health/autovacuum` | Per-table dead tuple diagnostics, queried live from `pg_stat_user_tables` |

The `/api/health/autovacuum` response includes each table's dead tuple count, live tuple count, dead percentage, and when autovacuum last ran. The overall status reflects the worst table above the minimum dead tuple threshold (tables with fewer than 1000 dead tuples don't affect overall status — this prevents tiny config tables from triggering false alerts on a fresh stack).

---

## Troubleshooting

### Database is still growing

**Check that retention is actually enabled.** The retention service only runs when both a retention day value and a cron schedule are set. FastTAK auto-enables the cron when you set `COT_RETENTION_DAYS` or `GEOCHAT_RETENTION_DAYS`, but you need at least one of them.

**Check that init-config ran after your `.env` change.** `init-config` is a one-shot container — it runs once and exits. If you changed `.env` after it already ran, the config files haven't been updated:

```bash
docker compose up -d --force-recreate init-config tak-server
```

**Verify the config files look right.** After init-config exits:

```bash
cat tak/conf/retention/retention-policy.yml
cat tak/conf/retention/retention-service.yml
```

`retention-policy.yml` should show your day values. `retention-service.yml` should show a cron expression, not `"-"`.

**Wait for the first scheduled run.** If you just enabled retention, nothing has been deleted yet — the service runs on its cron schedule. Check logs for confirmation:

```bash
docker compose logs tak-server | grep -i retention
```

### Autovacuum warning

The dead tuple ratio on one or more tables is elevated. FastTAK ships tuned autovacuum defaults that should handle this automatically. If you're seeing this on a fresh deployment, the tuned settings may not be active — verify the `tak-database` container is running the current image:

```bash
docker compose up -d --force-recreate tak-database
```

The health API won't reflect the improvement immediately (it updates on its poll interval). Query the endpoint directly for current status:

```bash
curl -s http://localhost:8080/api/health/autovacuum | jq .
```

If the warning persists after autovacuum has had time to run, a large backlog of dead tuples may have accumulated (common when enabling retention for the first time). VACUUM FULL from the ops page will clear it immediately.

### Reclaiming disk space

Run **VACUUM FULL** from the FastTAK ops page. This rewrites the database tables and returns space to the OS.

The database is exclusively locked for the duration. All TAK clients will disconnect and be unable to send or receive data. On a table with tens of millions of rows, this can take 5–15 minutes. Schedule it during a maintenance window.

After VACUUM FULL completes, clients reconnect automatically and disk usage drops immediately.

---

## Mission Archiving

TAK Server has a separate mechanism for archiving inactive missions via `mission-archiving-config.yml`. This is **disabled by default** in FastTAK and is a separate concern from CoT retention — nothing you configure in `.env` for data retention affects it. If you need lifecycle management for long-running missions, refer to the TAK Server documentation. Direct editing of that file is required; FastTAK doesn't expose it through `.env`.

---

## References

- [PostgreSQL: Routine Vacuuming](https://www.postgresql.org/docs/current/routine-vacuuming.html) — official documentation on autovacuum behavior and configuration parameters
- [EDB: Autovacuum Tuning Basics](https://www.enterprisedb.com/blog/autovacuum-tuning-basics) — practical guide to tuning autovacuum for high-write workloads
- [EDB: VACUUM FULL](https://www.enterprisedb.com/blog/postgresql-vacuum-and-analyze-best-practice-tips) — when to use VACUUM FULL vs. standard VACUUM and the tradeoffs
- [OpenTAKServer: Deleting Old Data](https://opentakserver.io/docs/configuration/deleting_old_data/) — similar retention approach in a different TAK Server implementation

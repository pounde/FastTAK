# FastTAK Database Management

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

That's it for most deployments. The rest of this doc explains what's happening and how to tune it.

---

## What Data Retention Is

By default, TAK Server keeps everything forever. Every position report, geochat message, and mission package that flows through your server accumulates in the database. On a quiet TAK network this isn't a problem. On a busy one — or a small VPS — the database will eventually fill your disk.

TAK Server ships a **retention service** that periodically deletes old data based on configurable thresholds. FastTAK exposes this through `.env` variables so you can enable it without editing XML.

The retention service manages five data types:

| Type | What it is | Default |
|------|-----------|---------|
| `cot` | CoT messages — position reports, sensor data, all cursor-on-target events | keep forever |
| `geochat` | In-app chat messages | keep forever |
| `files` | File attachments | keep forever |
| `missionpackages` | Mission packages | keep forever |
| `missions` | Missions | keep forever |

FastTAK exposes `.env` variables for `cot` and `geochat` because those are the two high-volume types that cause disk growth. Files, mission packages, and missions are intentionally created data that most operators don't want to expire automatically — but you can configure them if needed (see [Power User Settings](#power-user-settings)).

---

## How Retention Affects Vacuuming

When the retention service deletes old rows, PostgreSQL doesn't immediately reclaim that disk space. Instead, deleted rows become **dead tuples** — they're marked invisible but still occupy space on disk. PostgreSQL's **autovacuum** daemon runs in the background to clean these up and make the space reusable.

This works fine for normal workloads. The problem is PostgreSQL's default autovacuum settings are tuned conservatively: it only triggers when 20% of a table's rows are dead, and it throttles itself heavily to avoid impacting query performance. On the CoT tables — which can have millions of rows and receive bulk deletes from retention — this means dead tuples can pile up faster than autovacuum cleans them. The table bloats, queries slow down, and disk usage stays high even though the data has been "deleted."

This isn't a FastTAK-specific problem. It's a well-known PostgreSQL tuning concern for high-write, bulk-delete workloads.

---

## How FastTAK Handles Vacuuming

FastTAK tunes autovacuum through optional `.env` variables that are injected into PostgreSQL at startup. The defaults FastTAK applies:

```bash
PG_AUTOVACUUM_SCALE_FACTOR=0.05    # trigger at 5% dead rows (PG default: 0.2)
PG_AUTOVACUUM_COST_LIMIT=1000      # 5x faster cleanup (PG default: 200)
PG_MAINTENANCE_WORK_MEM=256MB      # more memory per vacuum worker (PG default: 64MB)
```

These three settings together mean autovacuum triggers earlier, runs faster, and has more memory to work with. **These are applied automatically** — you don't need to set anything to get them. If you want stock PostgreSQL behavior, explicitly set them to `0.2`, `200`, `64MB`.

A note on memory: `PG_MAINTENANCE_WORK_MEM` is only allocated during active vacuum operations. PostgreSQL runs up to 3 autovacuum workers simultaneously, so worst case is 3 × 256MB = 768MB during heavy cleanup. On a deployment with 2GB total RAM, you may want to lower this to 128MB — the scale factor and cost limit changes do most of the heavy lifting.

**No manual VACUUM is needed.** With these settings, autovacuum handles dead tuple cleanup automatically. VACUUM FULL is the one manual operation that matters: it rewrites tables to reclaim actual disk space after a large one-time deletion. See [Troubleshooting](#troubleshooting) for when to use it.

After changing these variables, restart the database container:

```bash
docker compose up -d --force-recreate tak-database
```

---

## Database Health Card

The FastTAK dashboard shows a **Database** health card with two key numbers:

- **Size** — total database size on disk (`pg_database_size`)
- **Live data** — space occupied by your actual data: tables, indexes, TOAST, across all user tables (`pg_total_relation_size` summed)

The gap between Size and Live data is PostgreSQL system overhead (system catalogs, WAL, internal structures) plus any table bloat from dead tuples. On a healthy, recently vacuumed database, this gap is roughly 10–15 MB regardless of database size. When it's much larger — say, 15 GB on a 30 GB database — that extra space is reclaimable via VACUUM FULL.

### Autovacuum warning banner

When dead tuple ratio is elevated on any table, the dashboard shows a warning banner with a direct instruction: add the `PG_*` variables to `.env` and restart `tak-database`. The banner goes away once autovacuum catches up. If it doesn't clear after a restart, the dead tuple accumulation rate may be exceeding what autovacuum can keep up with — VACUUM FULL from the ops page will reset it.

---

## Why We Removed Standard VACUUM

The dashboard ops page previously had two vacuum buttons: **VACUUM** (standard) and **VACUUM FULL**. The standard VACUUM button was removed.

Standard VACUUM and autovacuum produce identical results — both reclaim dead tuple space for PostgreSQL to reuse without shrinking the database file. With FastTAK's tuned autovacuum settings (0.05 scale factor, 1000 cost limit), autovacuum does this automatically and continuously. A manual VACUUM button would do exactly what autovacuum already does, on demand, for no additional benefit.

VACUUM FULL is different: it rewrites the entire table, shrinking the database file on disk. That has a real use case (reclaiming space after a large deletion) and a real cost (exclusive table lock, all clients disconnect). It stays.

---

## Power User Settings

### All `.env` variables

```bash
# Retention
COT_RETENTION_DAYS=          # days to keep CoT messages (null = keep forever)
GEOCHAT_RETENTION_DAYS=      # days to keep geochat messages (null = keep forever)
RETENTION_CRON=              # Quartz cron expression — auto-set to "0 0 3 * * ?" when any
                             # retention days are configured; override here if needed
                             # (e.g., "0 0 1 * * ?" for 1 AM)

# Autovacuum tuning (FastTAK defaults applied automatically; override here if needed)
PG_AUTOVACUUM_SCALE_FACTOR=  # fraction of live rows that triggers autovacuum (default: 0.05)
PG_AUTOVACUUM_COST_LIMIT=    # I/O cost limit per autovacuum cycle (default: 1000)
PG_MAINTENANCE_WORK_MEM=     # memory per vacuum worker (default: 256MB)

# External database (advanced — leave unset for standard Docker Compose deployments)
# TAK_DB_URL=postgresql://martiuser:mypassword@db-host:5432/cot
```

All retention variables default to `null` (keep forever), matching stock TAK Server behavior.

### Health threshold overrides

The autovacuum warning thresholds are set in `monitor/config/thresholds.yml` and can be overridden without editing YAML via `FASTAK_MON_*` environment variables. The variable name is the YAML key path with dots replaced by underscores and prefixed with `FASTAK_MON_`:

```bash
FASTAK_MON_autovacuum__warning_threshold=0.08   # warn at 8% dead tuples (default: 0.05)
FASTAK_MON_autovacuum__critical_threshold=0.20  # critical at 20% (default: 0.15)
```

### Retention for files, mission packages, and missions

These types aren't exposed as `.env` variables because they're low-volume and most operators don't want them to expire. To configure retention for them, edit `tak/conf/retention/retention-policy.yml` directly:

```yaml
dataRetentionMap:
  cot: 90        # days, or null to keep forever
  geochat: 30
  files: null    # set a number here if you want file expiry
  missionpackages: null
  missions: null
```

**Important:** `init-config` overwrites `retention-policy.yml` on every startup using the `.env` variables as source of truth. If you edit this file directly, do it **after** init-config has exited (it's a one-shot container), and don't restart init-config afterward. Using `.env` for cot/geochat and direct file edits for the rest is the cleanest approach.

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

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Full health cache — all modules with status and data |
| `GET /api/health?view=status` | Summary view — status and top-level values only, no per-table detail |

### Live diagnostic endpoints

| Endpoint | Description |
|----------|-------------|
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

### Autovacuum warning on dashboard

The dead tuple ratio on one or more tables is elevated. Add the tuning variables to `.env` and restart the database:

```bash
# Add to .env
PG_AUTOVACUUM_SCALE_FACTOR=0.05
PG_AUTOVACUUM_COST_LIMIT=1000
PG_MAINTENANCE_WORK_MEM=256MB
```

```bash
docker compose up -d --force-recreate tak-database
```

The dashboard updates from cache — it won't reflect the improvement immediately. Query the endpoint directly for current status:

```bash
curl -s http://localhost:8080/api/health/autovacuum | jq .
```

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

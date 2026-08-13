# Troubleshooting

## No TAK client can connect, and every container reports healthy

**Symptoms**

- ATAK/iTAK/WinTAK clients cannot connect, or connect on port 8089 and then
  receive **no CoT at all**.
- TAK Server's REST API returns HTTP 500 on every request.
- Client-side logs show `BadCredentialsException: Exception performing TAK
  Server authentication`, wrapping
  `IgniteClientDisconnectedException: Client node disconnected`.
- `docker compose ps` shows everything **healthy**. Certificates are valid. TLS
  handshakes succeed.

**What is actually wrong**

TAK Server backs its authentication and group lookups with an Apache Ignite
cache shared between its five JVM processes. One process's Ignite client node
has detached from the cluster while the JVM itself stayed alive. Every auth
attempt then throws, surfacing as a 500.

The misleading part is `BadCredentialsException` — nobody's credentials are
wrong. The failure is in the shared auth path, upstream of any individual
client's certificate.

The silent-stream symptom has the same cause: TAK registers each client
subscription in that same Ignite cache
(`IgniteCacheHolder.cacheRemoteSubscription`). The socket connects, the
subscription never registers, so nothing routes.

**Confirm it**

```bash
docker exec fasttak-tak-server-1 sh -c \
  'grep -iE "IgniteClientDisconnected|ClusterTopologyException|Failed to connect to node" \
   /opt/tak/logs/takserver-*.log | tail -20'
```

**Fix**

Restart the stack. The Ignite cluster re-forms on startup.

```bash
docker compose restart tak-server
```

It does not recover on its own. An Ignite client reconnects with a **new** node
ID, so the messaging process can hold references to the old one indefinitely,
retrying against a dead peer.

**Why nothing warned you**

The container healthcheck verifies that processes are running, ports accept
connections, certificates are valid, and no `OutOfMemoryError` appears in the
log. All of those pass during this failure — the API is running, it is just
answering 500 to everything. Improving that detection is tracked in the issue
tracker, along with tuning Ignite's failure-detection timeouts, which default to
600 seconds even though every node here is on loopback.

**Collect this before restarting** if you can spare a minute — the error storm
rotates the useful lines away quickly:

```bash
docker exec fasttak-tak-server-1 sh -c \
  'grep "Topology snapshot" /opt/tak/logs/takserver-messaging.log | tail -30'
docker exec fasttak-tak-server-1 sh -c 'ls -la /opt/tak/logs/'
```

## Every Monitor page returns 403

The Monitor is admin-only as of v0.28.1. The account you are logged in with is
not a member of `ADMIN_GROUP` (default `monitor_admin`).

See [Upgrading](upgrading.md#recovering-from-a-monitor-lockout) for recovery,
and [Authentication](authentication.md#authorization-who-may-use-the-monitor)
for what the gate covers.

## A container is running that is not in the compose file

`docker compose up` does not remove containers whose service was deleted from
the compose file. Compare what is running against what is defined:

```bash
docker ps --format '{{.Names}}'
docker compose config --services
```

Remove orphans with `docker compose up -d --remove-orphans`. `start.sh`,
`reconfig.sh`, and `just up` now pass this automatically.

## The database keeps growing

TAK Server keeps all CoT forever by default — `cot_router` grows without bound
and nothing prunes it. Autovacuum tuning does **not** delete rows; it only
reclaims space from rows something else deleted, so a stack with zero deletions
will show zero dead tuples and reclaim nothing.

To enable pruning, set `COT_RETENTION_DAYS` (and optionally
`GEOCHAT_RETENTION_DAYS`) in `.env` and restart so `init-config` templates the
retention policy. See [Database Management](database-management.md).

!!! warning "First run after a long period"
    Enabling retention on a table with millions of accumulated rows deletes them
    all in one pass at the scheduled time. That creates a large dead-tuple load,
    and the space will not return to the OS without a `VACUUM FULL`, which takes
    an exclusive lock. Consider a shorter first window, or pruning in batches.

High-volume automated feeds are the usual reason for unexpected growth. An
ADS-B or weather flow publishing every few seconds inserts far more rows than a
handful of human users ever will, and those CoT payloads are large and highly
repetitive — they compress well on disk, so the network traffic to Postgres can
be far larger than the resulting table. Throttling or trimming what the flow
publishes reduces both.

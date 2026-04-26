# USGS Earthquakes → TAK

Polls the USGS earthquake GeoJSON feed and emits one CoT marker per
quake to TAK Server.

!!! note "Read the [Flows overview](index.md) first"

    Import the [GeoJSON to CoT subflow](subflows/geojson-to-cot.md) before
    importing this flow, and have a data-mode service account ready.

## What it emits

One CoT Point event per earthquake at or above the magnitude threshold:

- **Type** `a-u-G` (neutral ground marker) — change in the subflow
  instance if you prefer a different symbol.
- **Callsign** — USGS event title (e.g., `M 5.2 - 100km S of somewhere`).
- **Remarks** — magnitude, location name, depth (km), event time (ISO),
  USGS event URL.
- **UID** — `usgs-<usgs-event-id>` — stable so repeat polls update the
  same marker instead of creating duplicates.
- **Stale** — 15 minutes.

Poll interval defaults to 5 minutes.

## Install

### Before you start

- [Flows overview prerequisites](index.md#prerequisites) done.
- `GeoJSON to CoT` subflow imported.
- Service account created (e.g., `svc_usgs`) with the `tak_*` groups that
  should receive earthquake markers.

### 1. Import

Node-RED → **☰** → **Import** → **Local** tab → **fasttak** →
**usgs-earthquakes** → **Import**.

### 2. Configure TLS

**☰** → **Configuration nodes** → double-click **USGS Server TLS**. Check
"Use key or certificate from local file", replace `{svc_user}` in the
cert/key paths with your service account name, click Update.

### 3. Adjust filter (optional)

Edit the **USGS feed URL** change node to:

- Change the feed — `all_hour`, `2.5_day`, `significant_week`, etc. See
  [USGS feed catalog](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php).
- Raise or lower `MIN_MAGNITUDE` (default 2.5). The filter runs in the
  **Transform** function node, below the URL change.

Poll interval is the **Every 5 min** inject node's `repeat` value (in
seconds). USGS feeds update every minute; polling faster than that is
wasted traffic.

### 4. Deploy

Red **Deploy** button. First poll fires within 3 seconds of deploy.
Markers appear in TAK clients within another ~30 seconds (TAK Server
LDAP refresh + client sync).

## Verify

```bash
docker compose logs -f nodered | grep -iE 'usgs|http request'
```

Every poll should show an HTTP 200 from
`earthquake.usgs.gov/earthquakes/feed/v1.0/summary/*.geojson`. The
function node logs warnings if the payload shape changes.

## Files

- `nodered/flows-library/usgs-earthquakes.json`

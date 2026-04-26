# NOAA Weather Alerts → TAK

Polls the NOAA National Weather Service active-alerts API and emits one
CoT polygon per alert. Severe thunderstorms, tornado warnings, flash
flood warnings, etc., show on the TAK map as shaded areas with the
alert's full text in the marker inspector.

!!! note "Read the [Flows overview](index.md) first"

    Import the [GeoJSON to CoT subflow](subflows/geojson-to-cot.md) before
    importing this flow.

## What it emits

- One CoT drawing event (`u-d-f`) per active alert polygon:
    - **Vertices** — the alert's polygon ring, as `<link>` entries.
    - **Center marker** — polygon centroid.
    - **Callsign** — alert `event` (e.g., `Tornado Warning`).
    - **Remarks** — headline, area description, severity/certainty,
      sent/expires timestamps, truncated description and instruction
      text.
- **UID** — `noaa-<alert-id>` — stable across polls.
- **Stale** — 1 hour from emission. NWS alerts typically expire within
  an hour; set your own stale if you poll less often.

Poll interval defaults to 2 minutes. NWS alerts update in near-real-time,
so this is a reasonable cadence for operational use.

## Filter

Default URL:

```
https://api.weather.gov/alerts/active?status=actual&severity=Severe,Extreme
```

Without a severity filter the full-US feed can exceed a thousand active
alerts. Adjust the URL in the **NOAA feed URL** change node:

- **By state**: `?area=TX` (two-letter code, any US state/territory).
- **By point**: `?point=39.7,-104.99` (lat,lon — returns alerts
  affecting that location).
- **By zone**: `?zone=TXC141` (NWS forecast zone code).
- **Combine filters**: `?area=TX&severity=Severe,Extreme`.

Full query vocabulary:
[https://www.weather.gov/documentation/services-web-api](https://www.weather.gov/documentation/services-web-api).

## Install

### Before you start

- [Flows overview prerequisites](index.md#prerequisites) done.
- `GeoJSON to CoT` subflow imported.
- Service account (e.g., `svc_weather`) created.

### 1. Import

Node-RED → **☰** → **Import** → **Local** → **fasttak** →
**noaa-weather** → **Import**.

### 2. Configure TLS

**☰** → **Configuration nodes** → **NOAA Server TLS** → "Use key or
certificate from local file" → replace `{svc_user}` → Update.

### 3. Set your User-Agent

NOAA requires a unique User-Agent with contact info (see their [ToS](https://www.weather.gov/documentation/services-web-api#/default/alerts_active)).
Open the **Fetch feed** http-request node, scroll to **Headers**, and
change the `User-Agent` value from:

```
FastTAK/1.0 (contact@example.com)
```

to something that identifies your deployment:

```
FastTAK/1.0 (sar-ops@yourorg.example)
```

NOAA may rate-limit or block calls without a real contact email.

### 4. Tune filter and deploy

Edit the URL in **NOAA feed URL** for your AO, then Deploy.

## Alerts without polygons

Some NWS alerts reference a forecast zone rather than a specific polygon.
Those come back with `geometry: null` and are dropped by the **Transform**
node — they can't be plotted on a map. If you need them as area-based
markers, extend the transform to look up zone geometry from
`https://api.weather.gov/zones/forecast/<zone-id>` or emit a point marker
at the alert's centroid from `properties.parameters.SAME` codes.

## Verify

```bash
docker compose logs -f nodered | grep -iE 'noaa|weather|http request'
```

Watch for HTTP 200 responses from `api.weather.gov`. An HTTP 403 usually
means your User-Agent wasn't accepted — check the header.

## Files

- `nodered/flows-library/noaa-weather.json`

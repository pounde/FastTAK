# ADS-B Aircraft Tracking → TAK

Polls the [adsb.lol](https://api.adsb.lol/) community feed for aircraft
within a configurable point + radius and emits one CoT air-track per
aircraft. Tracks carry altitude, speed, and heading so they render
correctly on TAK clients as moving aircraft symbols.

!!! note "Read the [Flows overview](index.md) first"

    Import the [GeoJSON to CoT subflow](subflows/geojson-to-cot.md) before
    importing this flow.

## Why adsb.lol and not OpenSky?

OpenSky Network moved most queries behind OAuth2 in late 2025 and
aggressively rate-limits or silently drops anonymous queries from cloud
provider IP ranges (AWS, GCP, Azure). FastTAK deployments running on
Lightsail, EC2, or any VPS will see timeouts even on small bounding-box
queries.

[adsb.lol](https://api.adsb.lol/) is a community-run feed built on the
readsb / tar1090 ecosystem. Public, no authentication, no API key, no
ASN filtering. Coverage matches the global crowdsourced ADS-B receiver
network, which is excellent over populated areas.

## What it emits

- One CoT point event per aircraft, per poll cycle:
    - **Type** `a-n-A-C-F` (neutral / air / civilian / fixed-wing) —
      override in the subflow instance for friendly (`a-f-A-C-F`),
      unknown (`a-u-A`), or rotary (`a-n-A-C-H`) tracks.
    - **Callsign** — ATC callsign (e.g., `UAL1234`) if known, else
      registration (e.g., `N12345`), else ICAO 24-bit hex address.
    - **Remarks** — ICAO hex, registration, type code, altitude (ft),
      ground speed (kt), heading (°), squawk.
    - **`<point hae>`** — geometric altitude in meters (falls back to
      barometric altitude if geo-alt is unavailable).
    - **`<track>`** — course (degrees true) and ground speed (m/s).
- **UID** — `adsb-<icao24>` — stable across polls so the same aircraft
  updates rather than spawning duplicates.
- **Stale** — 60 seconds; aircraft that stop reporting disappear from
  the map within a minute.

## Install

### Before you start

- [Flows overview prerequisites](index.md#prerequisites) done.
- `GeoJSON to CoT` subflow imported.
- Service account (e.g., `svc_adsb`) created.

### 1. Import

Node-RED → **☰** → **Import** → **Local** → **fasttak** → **adsb** →
**Import**.

### 2. Configure TLS

**☰** → **Configuration nodes** → **ADS-B Server TLS** → "Use key or
certificate from local file" → replace `{svc_user}` → Update.

### 3. Set your AO

Edit the **AO** change node:

| Field | Meaning | Example (Denver) |
|---|---|---|
| `lat` | center latitude (decimal degrees) | `39.7` |
| `lon` | center longitude (decimal degrees) | `-104.99` |
| `radius` | search radius in nautical miles | `100` |

The values are interpolated into the **Fetch states** http-request URL
via `{{lat}}` / `{{lon}}` / `{{radius}}` mustache templates, so the URL
shown in the node is the literal template — that's expected.

### 4. Deploy

First poll fires within 5 seconds. Aircraft symbols appear in TAK
clients shortly after.

## Tuning poll interval

Default 15 seconds. adsb.lol doesn't publish a hard rate limit, but
single-flow / single-AO polling at 15s is well-mannered. If you run
multiple ADS-B flows for different AOs, stagger the inject nodes so they
don't all fire on the same second.

## Type mapping

adsb.lol doesn't distinguish military from civilian transponders. Default
is neutral civilian. Adjust `COT_TYPE` in the subflow instance if you:

- Want **friendly** display for your own fleet (e.g., known tail
  numbers): `a-f-A-C-F`. Pre-filter the GeoJSON in the **Aircraft →
  GeoJSON** node by registration / ICAO allowlist.
- Need **rotary-wing** symbols: `a-n-A-C-H`.

For type-per-aircraft (e.g., tag known military hex codes as `a-h-A-M-F`),
extend the **Aircraft → GeoJSON** function to look up `a.hex` against an
allowlist and set `properties.cotType` per feature, then read it in a
custom subflow variant.

## Verify

```bash
docker compose logs -f nodered | grep -iE 'adsb|http request'
```

A clean run shows successive HTTP 200 responses from
`api.adsb.lol`. The function node logs a warning if the response body
doesn't match the expected schema (`{ ac: [...] }`).

## Files

- `nodered/flows-library/adsb.json`

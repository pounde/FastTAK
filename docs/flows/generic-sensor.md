# Generic Sensor Feed → TAK (template)

A minimal, wired-up skeleton for polling an arbitrary REST API on an
interval and publishing the results as CoT. Rename the tab, edit four
nodes, deploy.

This is the starting point for CAD-dispatch feeds, river-gauge feeds,
lightning-strike feeds, vehicle telemetry, and any other REST-polled
data source not covered by a dedicated library flow.

!!! note "Read the [Flows overview](index.md) first"

    Import the [GeoJSON to CoT subflow](subflows/geojson-to-cot.md) before
    importing this flow.

## Shape

```
[ Every 60s ] → [ Feed URL ] → [ Fetch feed ] → [ Map to GeoJSON ] → [ GeoJSON → CoT ] → [ TAK Server ]
```

All five wires are already connected. You only edit nodes marked
**EDIT ME** and the subflow instance config.

## Install

### Before you start

- [Flows overview prerequisites](index.md#prerequisites) done.
- `GeoJSON to CoT` subflow imported.
- Service account created.

### 1. Import

Node-RED → **☰** → **Import** → **Local** → **fasttak** →
**generic-sensor** → **Import**.

### 2. Configure TLS

**☰** → **Configuration nodes** → **Generic Sensor Server TLS** → "Use
key or certificate from local file" → replace `{svc_user}` → Update.

### 3. Fill in the four EDIT ME nodes

#### a. `Every 60s — EDIT ME` (inject)

Set `repeat` to your desired poll interval in seconds. Respect upstream
rate limits.

#### b. `Feed URL — EDIT ME` (change)

Set `msg.url` to your REST endpoint. If you need query parameters,
headers, or auth, add them to the **Fetch feed** http-request node or
build them in an intermediate change/function node.

#### c. `Map to GeoJSON — EDIT ME` (function)

This is where most of your work happens. Transform `msg.payload` (your
API's response body) into a GeoJSON FeatureCollection. The required
shape:

```javascript
msg.payload = {
    type: 'FeatureCollection',
    features: [
        {
            type: 'Feature',
            id: stableIdForEntity,
            geometry: { type: 'Point', coordinates: [lon, lat] },
            properties: {
                callsign: '...',
                remarks: '...'
                // Optional: altitude, speed, course for air/sea tracks
            }
        }
    ]
};
return msg;
```

Tip: use `node.warn(JSON.stringify(msg.payload, null, 2))` during
development to inspect the shape of your API response.

#### d. `GeoJSON → CoT` (subflow instance)

Double-click and set:

- `COT_TYPE` — which TAK marker symbol to use. See
  [docs/reference/cot-types.md](../reference/cot-types.md) for common
  types, or the [full catalog](../reference/cot/CoTtypes.xml).
- `UID_PREFIX` — unique per flow (so IDs don't collide with other
  flows).
- `STALE_SECONDS` — must be greater than your poll interval.

## Example: CAD dispatch feed

Given an API that returns:

```json
{
  "incidents": [
    { "id": "INC-123", "type": "Structure Fire", "lat": 39.7, "lon": -104.99, "units": "E5,T2", "address": "1234 Main St" }
  ]
}
```

Mapping function:

```javascript
const raw = msg.payload;
const features = (raw.incidents || []).map(inc => ({
    type: 'Feature',
    id: inc.id,
    geometry: { type: 'Point', coordinates: [inc.lon, inc.lat] },
    properties: {
        callsign: inc.type,
        remarks: `${inc.type} @ ${inc.address} | Units: ${inc.units}`
    }
}));
msg.payload = { type: 'FeatureCollection', features };
return msg;
```

Subflow config: `COT_TYPE=a-f-G-E`, `UID_PREFIX=cad`, `STALE_SECONDS=600`.

## Files

- `nodered/flows-library/generic-sensor.json`

# GeoJSON to CoT (subflow)

Converts a GeoJSON `Feature` or `FeatureCollection` into TAK CoT XML. One
message in → one message per feature out. Shipped as a Node-RED subflow
so you can drop it into any flow that produces GeoJSON.

Used as a building block by every pipeline flow in the library (USGS,
NOAA, ADS-B, Generic Sensor).

!!! note "Import this subflow **before** importing any pipeline flow that uses it"

    Pipeline flows reference the subflow by id (`subflow:geojson-to-cot`).
    If you import a pipeline without the subflow present, the pipeline
    loads with an unresolved reference and won't deploy.

## Input

`msg.payload` is any of:

- A single GeoJSON `Feature`
- A GeoJSON `FeatureCollection`
- A plain array of `Feature` objects

Supported `geometry.type`: `Point`, `Polygon`, `LineString`. Other geometry
types are dropped silently.

## Output

One `msg` per feature on the single output wire:

- `msg.payload` — CoT XML string, UTF-8, no trailing newline.
- `msg.topic` — stable UID for the feature (for logging or routing).

Downstream nodes (`tcp out`, `mqtt out`, `file out`) receive each feature
as its own message.

## Environment variables

| Name | Default | Purpose |
|---|---|---|
| `COT_TYPE` | `a-u-G` | CoT event type for Point features. Polygon/LineString features auto-switch to `u-d-f` (TAK freehand drawing) unless `COT_TYPE` already starts with `u-d`. |
| `UID_PREFIX` | `geojson` | Prefix for generated UIDs. Full UID is `<prefix>-<id>`, where `<id>` falls back to `feature.id`, `feature.properties.id`, or the feature's index. |
| `STALE_SECONDS` | `300` | How long each CoT event is valid. Set > poll interval so markers don't flicker. |
| `CALLSIGN_PROP` | `name` | `feature.properties.<this>` becomes the CoT `callsign` (TAK map label). |
| `REMARKS_PROP` | `description` | `feature.properties.<this>` becomes the CoT `<remarks>` (inspector text). |
| `ALTITUDE_PROP` | (empty) | Optional. If set, `feature.properties.<this>` becomes `point[@hae]`. If empty, uses `geometry.coordinates[2]` when present, else 0. |
| `SPEED_PROP` | (empty) | Optional. If set, adds a `<track>` element with this property's value as speed (meters/second). |
| `COURSE_PROP` | (empty) | Optional. If set, adds `course` to the `<track>` element (degrees true from north). |

## CoT output shape

### Point features

```xml
<event version="2.0" uid="<prefix>-<id>" type="<COT_TYPE>" how="m-g" time="..." start="..." stale="...">
  <point lat="..." lon="..." hae="..." ce="9999999.0" le="9999999.0"/>
  <detail>
    <contact callsign="..."/>
    <track course="..." speed="..."/>   <!-- only if SPEED_PROP or COURSE_PROP set -->
    <remarks>...</remarks>
  </detail>
</event>
```

### Polygon / LineString features

Outer ring vertices (Polygon) or line points (LineString) become `<link>`
entries. Point position is the centroid. Interior rings (holes) are
ignored — TAK CoT drawings don't represent them.

```xml
<event version="2.0" uid="<prefix>-<id>" type="u-d-f" how="m-g" time="..." start="..." stale="...">
  <point lat="<centroid-lat>" lon="<centroid-lon>" hae="0" ce="9999999.0" le="9999999.0"/>
  <detail>
    <link point="lat,lon,hae"/>   <!-- one per vertex -->
    <link point="lat,lon,hae"/>
    ...
    <strokeColor value="-65536"/>
    <strokeWeight value="2.0"/>
    <fillColor value="1073741824"/>
    <labels_on value="true"/>
    <contact callsign="..."/>
    <remarks>...</remarks>
  </detail>
</event>
```

To override the default stroke/fill colors, drop a `change` node after the
subflow and edit `msg.payload`.

## Typical wiring

```
[ http request ] → [ function: map to GeoJSON ] → [ GeoJSON → CoT ] → [ tcp out (TAK) ]
```

Every pipeline flow in the library follows this shape. See the
[Generic Sensor Feed](../generic-sensor.md) for the smallest reference
implementation.

## Limitations

- **Polygon holes** are ignored. TAK CoT drawings only model an outer
  ring.
- **MultiPoint**, **MultiPolygon**, **MultiLineString** and
  **GeometryCollection** are dropped. Pre-process your GeoJSON to flatten
  them if you need per-part CoT output.
- The subflow is stateless — it doesn't track which UIDs were emitted
  previously. If your source stops providing an entity, no CoT
  `stale`/delete event is sent; the marker expires via its `STALE_SECONDS`
  window.

## Files

- `nodered/flows-library/subflows/geojson-to-cot.json` — the subflow
  (importable from Import → Local → fasttak → subflows).

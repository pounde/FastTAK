# Node-RED

FastTAK includes [Node-RED](https://nodered.org/) with two pre-installed packages:

- **node-red-contrib-postgresql** — query the PostGIS database
- **node-red-contrib-tak** — (optional) TAK-specific helper nodes

A **PostGIS config node** ("FastTAK PostGIS") is pre-configured and available to all flows —
select it in any postgresql node without entering credentials.

To **send CoT messages to TAK Server**, use a TCP output node with TLS:

| Setting | Value |
|---------|-------|
| Host | `tak-server` (env var `TAK_HOST`) |
| Port | `8089` (env var `TAK_PORT`) |
| TLS | Enabled — add a TLS config node |
| Client cert (pfx) | `/opt/tak/certs/nodered.p12` |
| Passphrase | `atakatak` |
| CA cert | `/opt/tak/certs/ca.pem` |

!!! note
    A `nodered` LDAP user is automatically created in the `tak_ROLE_ADMIN` group,
    so CoT messages sent from Node-RED flows reach all connected TAK clients.

---

## Tutorial: USGS Earthquakes on the TAK Map

This flow polls the USGS earthquake feed every five minutes and pushes each event
to TAK Server as a map marker. It uses four nodes: Inject → HTTP Request → Function → TCP Out.

### Step 1 — Inject (timer)

Drag an **inject** node onto the canvas.

- **Repeat** — interval, every `5` minutes
- Check **Inject once after 0 seconds** so it fires on deploy

### Step 2 — HTTP Request

Drag an **http request** node and wire it to the inject node.

- **Method** — GET
- **URL** — `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson`
- **Return** — a parsed JSON object

### Step 3 — Function (build CoT XML)

Drag a **function** node and wire it to the HTTP request node. Paste the following:

```js
const features = msg.payload.features || [];
const messages = [];

for (const f of features) {
    const [lon, lat, depthKm] = f.geometry.coordinates;
    const props = f.properties;
    const mag = props.mag;
    const place = props.place || "Unknown";
    const id = f.id;

    // Timestamps — event time as start, stale after 1 hour
    const time = new Date(props.time).toISOString();
    const now = new Date().toISOString();
    const stale = new Date(Date.now() + 3600000).toISOString();

    const cot = `<?xml version="1.0" encoding="UTF-8"?>
<event version="2.0"
       uid="usgs-${id}"
       type="b-m-p-s-m"
       how="m-g"
       time="${now}"
       start="${time}"
       stale="${stale}">
  <point lat="${lat}" lon="${lon}" hae="${-depthKm * 1000}" ce="9999999" le="9999999"/>
  <detail>
    <remarks>M${mag} — ${place} (depth ${depthKm} km)</remarks>
    <contact callsign="EQ-M${mag}"/>
  </detail>
</event>`;

    messages.push({ payload: cot });
}

return [messages];
```

**What this does:** iterates over each earthquake feature, builds a CoT event XML with
the quake's coordinates, magnitude, and description, then outputs all messages at once.

- `uid` — prefixed with `usgs-` so TAK treats each earthquake as a distinct track
- `type` — `b-m-p-s-m` (seismic event)
- `how` — `m-g` (machine-generated)
- `hae` — height above ellipsoid (negative = below ground, converted from km to meters)
- `stale` — 1 hour from now; markers disappear after the feed refreshes

### Step 4 — TCP Out (TAK Server)

Drag a **tcp out** node and wire it to the function node.

- **Type** — Connect to
- **Host** — `tak-server`
- **Port** — `8089`
- Click the pencil icon next to **TLS** to add a TLS config:
    - **Certificate (pfx)** — upload or path: `/opt/tak/certs/nodered.p12`
    - **Passphrase** — `atakatak`
    - **CA Certificate** — upload or path: `/opt/tak/certs/ca.pem`
    - Uncheck **Verify server certificate** if using self-signed certs
- **Close connection after each message** — leave unchecked (persistent connection)

### Deploy

Click **Deploy**. Within a few seconds the inject node fires, the flow fetches the latest
earthquakes, and markers appear on connected TAK clients (ATAK, WinTAK, iTAK).

---

## Import This Flow

Copy the JSON below, then in Node-RED go to **Menu → Import → Clipboard** and paste:

```json
[
    {
        "id": "usgs-tab",
        "type": "tab",
        "label": "USGS Earthquakes",
        "disabled": false,
        "info": "Polls USGS earthquake feed and pushes events to TAK Server as CoT markers."
    },
    {
        "id": "usgs-inject",
        "type": "inject",
        "z": "usgs-tab",
        "name": "Every 5 min",
        "props": [],
        "repeat": "300",
        "crontab": "",
        "once": true,
        "onceDelay": "0",
        "topic": "",
        "x": 130,
        "y": 100,
        "wires": [["usgs-http"]]
    },
    {
        "id": "usgs-http",
        "type": "http request",
        "z": "usgs-tab",
        "name": "USGS Feed",
        "method": "GET",
        "ret": "obj",
        "paytoqs": "ignore",
        "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
        "tls": "",
        "persist": false,
        "proxy": "",
        "insecureHTTPParser": false,
        "authType": "",
        "senderr": false,
        "headers": [],
        "x": 300,
        "y": 100,
        "wires": [["usgs-function"]]
    },
    {
        "id": "usgs-function",
        "type": "function",
        "z": "usgs-tab",
        "name": "Build CoT XML",
        "func": "const features = msg.payload.features || [];\nconst messages = [];\n\nfor (const f of features) {\n    const [lon, lat, depthKm] = f.geometry.coordinates;\n    const props = f.properties;\n    const mag = props.mag;\n    const place = props.place || \"Unknown\";\n    const id = f.id;\n\n    const time = new Date(props.time).toISOString();\n    const now = new Date().toISOString();\n    const stale = new Date(Date.now() + 3600000).toISOString();\n\n    const cot = `<?xml version=\"1.0\" encoding=\"UTF-8\"?>\\n<event version=\"2.0\"\\n       uid=\"usgs-${id}\"\\n       type=\"b-m-p-s-m\"\\n       how=\"m-g\"\\n       time=\"${now}\"\\n       start=\"${time}\"\\n       stale=\"${stale}\">\\n  <point lat=\"${lat}\" lon=\"${lon}\" hae=\"${-depthKm * 1000}\" ce=\"9999999\" le=\"9999999\"/>\\n  <detail>\\n    <remarks>M${mag} — ${place} (depth ${depthKm} km)</remarks>\\n    <contact callsign=\"EQ-M${mag}\"/>\\n  </detail>\\n</event>`;\n\n    messages.push({ payload: cot });\n}\n\nreturn [messages];",
        "outputs": 1,
        "timeout": "",
        "noerr": 0,
        "initialize": "",
        "finalize": "",
        "libs": [],
        "x": 480,
        "y": 100,
        "wires": [["usgs-tcp"]]
    },
    {
        "id": "usgs-tcp",
        "type": "tcp out",
        "z": "usgs-tab",
        "name": "TAK Server",
        "host": "tak-server",
        "port": "8089",
        "beserver": "client",
        "base64": false,
        "end": false,
        "tls": "usgs-tls",
        "close": false,
        "x": 660,
        "y": 100,
        "wires": []
    },
    {
        "id": "usgs-tls",
        "type": "tls-config",
        "name": "TAK Server TLS",
        "cert": "",
        "key": "",
        "ca": "/opt/tak/certs/ca.pem",
        "certname": "/opt/tak/certs/nodered.p12",
        "keyname": "",
        "caname": "ca.pem",
        "servername": "",
        "verifyservercert": false,
        "alpnprotocol": "",
        "passphrase": "atakatak"
    }
]
```

After importing, click **Deploy**. You may need to open the TCP Out node and re-select
the TLS config if the certificate paths don't resolve automatically.

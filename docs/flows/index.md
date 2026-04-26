# Flows Library

Reusable Node-RED flows that ship with FastTAK. Each flow is a single importable JSON file under `nodered/flows-library/` with a matching docs page here.

## Available flows

| Flow | Source | Geometry | CoT type |
|---|---|---|---|
| [DroneSense](dronesense.md) | DroneSense REST API | Point (drones, operators, phones with video) | `a-f-A-M-H-Q`, `a-f-G-E-S`, `a-f-G-U-C` |
| [USGS Earthquakes](usgs-earthquakes.md) | `earthquake.usgs.gov` GeoJSON | Point | `a-u-G` |
| [NOAA Weather Alerts](noaa-weather.md) | `api.weather.gov` alerts | Polygon | `u-d-f` (drawing) |
| [ADS-B Aircraft](adsb.md) | adsb.lol community feed | Point with altitude/speed/course | `a-n-A-C-F` |
| [Generic Sensor Feed](generic-sensor.md) | any REST API (template) | Point (customizable) | user-defined |

## Available subflows

| Subflow | Purpose |
|---|---|
| [GeoJSON to CoT](subflows/geojson-to-cot.md) | Convert a GeoJSON Feature/FeatureCollection into CoT XML. Used as a building block by every pipeline flow above. |

The patterns below apply to every flow and subflow in the library.

---

## Model: per-flow TLS config node

Every library flow ships with its **own** TLS config node, named for that flow (e.g., DroneSense ships with `DroneSense Server TLS` / id `ds-tls`). Cert and key fields are empty on import — you pick which service account the flow should use when you configure it.

Two flows → two TLS config nodes → you choose whether they share a cert or not:

- **Different certs** (isolated — each flow's CoT is scoped to that account's groups): open each flow's TLS node separately and point at different `/data/certs/svc_<name>.*` PEMs.
- **Same cert** (shared — both flows publish as the same account): paste the same `/data/certs/svc_<name>.*` paths into both flows' TLS nodes.

The decision is always yours and always configured inside the flow itself, not in a global setting.

> **Why not share a single TLS config across all library flows?**
> Because that would force every flow to use the same service account, and the common case for separate flows is separate group scopes (e.g., DroneSense publishes to `tak_UAS_Team`, a future earthquakes flow publishes to `tak_SAR`). Per-flow TLS keeps the default isolated; sharing stays a deliberate act.

---

## Prerequisites

### Create data-mode service accounts in the dashboard

Open the FastTAK dashboard → **Service Accounts** → **Create Service Account**. For each flow you want to deploy:

- **Username**: `svc_<whatever>` (the `svc_` prefix is required; pick something meaningful like `svc_dronesense`, `svc_sar_ops`, or a generic `svc_nodered` if you just want one account for everything).
- **Mode**: `data`.
- **Groups**: every `tak_*` group this account should publish into. Only clients in at least one of these groups will see CoT the flow emits.

On creation, the monitor automatically writes an unencrypted PEM cert/key pair into `/data/certs/` inside the nodered container. **No restart required** — the PEMs appear immediately via the bind mount.

### Verify PEMs are visible in nodered

```bash
docker compose exec nodered ls /data/certs
```

You should see `svc_<name>.cert.pem` and `svc_<name>.key.pem` for every data-mode account you've created. If not, check the monitor logs:

```bash
docker compose logs monitor | grep -iE 'Node-RED PEM'
```

---

## Config nodes every flow uses

| Config Node | Purpose | Ships where |
|---|---|---|
| **FastTAK App DB** | PostgreSQL connection to the shared app database. | Base `nodered/flows.json` — shared. |
| **FastTAK Server TLS** | Ad-hoc shared TLS config for flows you build yourself. Ships empty; configure once or clone per flow. | Base `nodered/flows.json` — shared. |
| **Library flow TLS** (e.g., `DroneSense Server TLS`) | Per-flow TLS config. Ships empty; each library flow has its own. | Inside the library flow's JSON — flow-scoped. |

---

## Import a flow — walkthrough

Using **DroneSense** as the example, and the sample service account name
`svc_nodered`. Substitute your own account name where shown.

### 1. Open Node-RED

In your browser, go to Node-RED (same host as the FastTAK dashboard; path
depends on your deploy mode — typically `/nodered`).

### 2. Import the flow JSON

Library flows are available directly in the editor — no file upload:

1. Click the **☰** (hamburger) menu at the top right.
2. Click **Import**.
3. Switch to the **Local** tab.
4. Expand the **fasttak** folder. Every library flow (dronesense,
   usgs-earthquakes, noaa-weather, adsb, generic-sensor) appears here,
   plus a **subflows** subfolder with reusable components.
5. Click the flow you want to import. Its nodes fill the preview pane.
6. Click **Import**. A new tab appears in Node-RED with the flow laid out.

> **Tip:** Pipeline flows (USGS, NOAA, ADS-B, Generic Sensor) use the
> `GeoJSON to CoT` subflow. Import it first from **subflows →
> geojson-to-cot**, otherwise the pipeline will load with an unresolved
> subflow reference.

### 3. Configure the TLS node

Every library flow includes its **own** TLS config node (named for the flow,
e.g., `DroneSense Server TLS`).

1. **☰** → **Configuration nodes** → double-click your flow's TLS node.
2. Check **Use key or certificate from local file**.
3. In **Client Certificate** and **Client Key**, replace `{svc_user}` with
   your service account's username (e.g., `/data/certs/svc_nodered.cert.pem`).
   Leave **CA Certificate** at `/opt/tak/certs/ca.pem`.
4. Leave **Server Name** at `${SERVER_ADDRESS}` — Node-RED resolves
   this from your `.env` on every flow load. The TLS layer asserts your
   TAK Server's FQDN as the cert identity while the underlying TCP
   connection still goes to `tak-server` on the Docker network.
5. Click **Update**.

### 4. Fill in flow-specific config

Each library flow has a **change** node (often named "Missions", "Config",
or similar) where you paste in API keys, mission names, etc. The flow's
own docs page shows exactly what to put where.

### 5. Deploy

Click the red **Deploy** button at the top right. The flow starts running
immediately.

---

## Verify CoT is reaching TAK clients

Three layers of check:

1. **Node-RED debug tab** — add a `debug` node on the `tcp out` input; inspect outgoing CoT XML.
2. **TAK Server logs**:
   ```bash
   docker compose logs --tail=50 tak-server | grep -iE 'subscription|svc_'
   ```
   Confirm the service account opened a TLS subscription.
3. **TAK clients** — open WinTAK/ATAK, confirm expected markers appear on the map.

If a TAK client in the right group still doesn't see events, check the service account's group assignment matches the TAK channel the client is on. Common mistake: service account in `tak_GroupA`, client in `tak_GroupB` — no overlap, no visibility.

---

## Changing group assignments later

Groups are pure LDAP state. Use **Service Accounts → <account> → Edit** in the dashboard to add/remove groups. TAK Server picks up group changes on its next LDAP refresh (30s). No container restart, no Node-RED redeploy — the cert hasn't changed, only the channels it's allowed to publish into.

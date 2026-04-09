# FastTAK Quickstart Walkthrough

End-to-end: set up FastTAK, create a user, enroll a device, stream video.

## Prerequisites

- Docker Engine + Docker Compose v2 (v2.20+) installed
- Official TAK Server release ZIP from [tak.gov](https://tak.gov)

## Step 1: Run setup

```bash
cd /path/to/FastTAK
./setup.sh takserver-docker-5.6-RELEASE-6.zip
```

This extracts the TAK Server release into `./tak/`, builds the Docker images, and creates `.env` from the template.

## Step 2: Configure

```bash
vim .env   # setup.sh already created this from .env.example
```

Set at minimum:

- `FQDN` — your domain name (used for SSL, QR enrollment, server cert)

All other values (secrets, database passwords) are generated automatically by `setup.sh`. Review `TAK_WEBADMIN_PASSWORD` if you want to change the default admin password.

## Step 3: Start the stack

```bash
./start.sh
```

Or manually: `docker compose up -d --build`

### What happens on startup

1. **tak-database** and **app-db** start and become healthy
   - `app-db` syncs the database password, creates the `nodered` database, and enables PostGIS
2. **init-config** runs:
   - Patches CoreConfig.xml (DB connection, admin UI, LDAP auth)
   - Generates certificates (CA, server, admin, nodered client certs)
   - Creates CA signing keystore for QR enrollment
   - Generates a server cert matching your FQDN
   - Adds the `<certificateSigning>` block
   - Upgrades all `.p12` certs to modern AES-256-CBC ciphers
   - Exits
3. **LLDAP** starts (lightweight LDAP server backed by app-db)
4. **init-identity** runs after LLDAP is healthy:
   - Bootstraps LDAP users, groups, and custom attribute schemas via GraphQL
   - Creates `webadmin` user and `tak_ROLE_ADMIN` group
   - Generates TAK Portal settings.json
   - Exits
5. **tak-server** starts (~4 minutes to become healthy) — all config is already applied, no restart needed
6. **ldap-proxy**, **tak-portal**, **caddy**, **mediamtx**, and **nodered** start

## Step 4: Verify

```bash
docker compose ps                    # all services running, init containers exited
./certs.sh ca-info               # shows Root CA and Intermediate CA
./certs.sh list                  # shows cert files including FQDN server cert
ls ./tak/certs/files/                # certs are directly accessible on the host
```

## Step 5: Log in as admin

Open `https://<your-fqdn>:8446` in your browser.

Log in with: `webadmin` / value of `TAK_WEBADMIN_PASSWORD` from `.env`

## Step 6: Create a user and enroll their device

TAK Portal is available at `http://localhost:3000`.

### Create the user

1. In TAK Portal, create an **Agency** (e.g., your organization name)
2. Create a **Group** with the `tak_` prefix (e.g., `tak_team1`) — see "Understanding groups" below
3. Go to **Users** → **Create**
4. Fill in: username, name, assign to the agency and group
5. Click **Create**

### Enroll their device

1. In TAK Portal's user list, click the **QR** button next to the user
2. A QR code appears with a 15-minute enrollment token
3. User scans the QR code with ATAK, iTAK, or TAK Aware
4. The TAK client connects to TAK Server on port 8446, authenticates, and receives its certificate
5. The client auto-configures and connects — no manual setup needed

### Alternative: manual cert (CLI)

```bash
./certs.sh create-client alice
./certs.sh download alice.p12
```

Transfer `alice.p12` to the device (email, shared drive — not AirDrop, which iOS treats as a profile install). Then in the TAK client:

- Settings → Network → TAK Servers → Add
- Host: your FQDN
- Port: 8089, Protocol: SSL
- Import `alice.p12`, password: `atakatak`

## Step 7: Understanding groups

Only groups prefixed with `tak_` are visible to TAK Server. The prefix is stripped to form the channel name:

| LLDAP Group      | TAK Channel      | Purpose                                    |
| ---------------- | ---------------- | ------------------------------------------ |
| `tak_ROLE_ADMIN` | _(admin access)_ | Grants admin privileges on TAK Server      |
| `tak_team1`      | `team1`          | Users see this channel in their TAK client |
| `tak_fires`      | `fires`          | Users see this channel in their TAK client |

Groups without the `tak_` prefix are invisible to TAK Server.

Create groups in TAK Portal under the **Groups** page.

**LDAP cache delay:** When you create a user or change groups, TAK Server takes up to 30 seconds to refresh. The user can connect immediately but may see "No channels found" — disconnect and reconnect after 30 seconds.

## Step 8: Stream video

### Send a stream

**From OBS or an encoder:**

- Server: `rtmp://<server-ip>:1935/live`
- Stream key: any name (e.g., `drone1`)

**From an RTSP source (camera, drone):**

- Push to: `rtsp://<server-ip>:8554/live/<stream-name>`

### View a stream

- **HLS:** `http://<server-ip>:8888/live/<stream-name>`
- **Via Caddy:** `https://stream.<FQDN>/live/<stream-name>`
- **In TAK:** share the RTSP URL as a CoT video feed

## Clean teardown

```bash
# Remove database volumes. Certs and config in ./tak/ are preserved.
docker compose down -v

# Complete wipe including certs and config:
docker compose down -v && rm -rf tak/ .env
```

## Troubleshooting

**TAK Server not healthy after 5 minutes?**

```bash
docker compose logs tak-server | tail -30
```

**Identity bootstrap failed?**

```bash
docker compose logs init-identity
```

**QR enrollment says "credentials not accepted"?**

- Did you run `./setup.sh`? The TAK Server release must be extracted before starting
- Enrollment tokens expire after 15 minutes — generate a fresh QR

**Device shows "connecting" but never connects?**

- Verify the FQDN server cert was created: `./certs.sh list | grep <your-fqdn>`
- Verify port 8089 is reachable from the device

**No channels in TAK client?**

- LDAP cache delay — wait 30 seconds, disconnect, reconnect
- Verify user has groups with `tak_` prefix in TAK Portal

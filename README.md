# FastTAK

TAK ecosystem infrastructure — deployed with `docker compose up`.

## What Is This?

A Docker Compose stack for deploying and managing the TAK ecosystem:

- **TAK Server** — Official tak.gov Docker images (messaging, CA, web admin)
- **Caddy** — Automatic Let's Encrypt TLS and reverse proxy
- **MediaMTX** — RTSP/RTMP/HLS video streaming
- **Authentik** — SSO, LDAP authentication, user management
- **TAK Portal** — User management, certificate enrollment via QR
- **Node-RED** — Flow-based automation engine with pre-configured PostGIS and TAK Server connections

For browser-based TAK access, see [CloudTAK](https://github.com/dfpc-coe/CloudTAK) (separate stack, connects to FastTAK's TAK Server).

## Prerequisites

1. **Docker Engine** and **Docker Compose v2** (v2.20+) installed
2. **Official TAK Server release** ZIP from [tak.gov](https://tak.gov)
3. **DNS** (recommended) — your FQDN and subdomains should resolve to the host's public IP. Required for Caddy TLS, subdomain routing, and QR enrollment. Without DNS, TAK clients can still connect directly via IP address on ports 8089/8443/8446, but Caddy and QR enrollment won't work. Set `FQDN=localhost` in `.env` for local development.

## Quick Start

```bash
# One-time setup (builds images, extracts tak/, generates secrets)
./setup.sh takserver-docker-5.6-RELEASE-6.zip
vim .env                # set FQDN to your domain

# Start
./start.sh
```

`setup.sh` extracts the TAK Server release, builds Docker images, creates `.env` with generated secrets. You only run it once (or again to upgrade).

`start.sh` brings up the stack, waits for healthy, and shows connection info.

For the full end-to-end walkthrough (user enrollment, video streaming), see [docs/quickstart-walkthrough.md](docs/quickstart-walkthrough.md).

### Without shell scripts (Windows or manual setup)

If you can't run bash scripts, the manual workflow is:

```bash
# 1. Extract the tak.gov ZIP and copy tak/ into this directory
unzip takserver-docker-5.6-RELEASE-6.zip
cp -r takserver-docker-5.6-RELEASE-6/tak ./tak

# 2. Build Docker images (from the extracted release directory)
docker build -t takserver-database:5.6-RELEASE-6 -f takserver-docker-5.6-RELEASE-6/docker/Dockerfile.takserver-db takserver-docker-5.6-RELEASE-6
docker build -t takserver:5.6-RELEASE-6 -f takserver-docker-5.6-RELEASE-6/docker/Dockerfile.takserver takserver-docker-5.6-RELEASE-6

# 3. Create .env and generate secrets
cp .env.example .env
# Edit .env: set FQDN, then generate each empty secret with the command
# shown in the comment above it (e.g., openssl rand -hex 16).
# All empty fields must be filled — the stack will not start without them.

# 4. Start
docker compose up -d --build
```

### Changing configuration

When you change `.env` (FQDN, passwords, versions), apply the changes with:

```bash
docker compose down          # keeps database data
docker compose up -d --build
```

Running compose `down` then `up` re-runs the init containers that apply configuration from `.env` to the services.

To change passwords, edit the values directly in `.env` and run `docker compose down` then `docker compose up -d --build`. Database passwords and Authentik credentials are automatically synced on startup.

Use `down -v` only for a full reset (destroys database data). Certs and config in `./tak/` are always preserved.

## Services

All services start together — TAK Server, Caddy, MediaMTX, Authentik (SSO/LDAP), TAK Portal, Node-RED. Init containers handle configuration before TAK Server starts — no restart needed.

## Configuration

All configuration lives in a single `.env` file. See `.env.example` for the full reference.

### Required variables

| Variable | Description |
| -------- | ----------- |
| `FQDN` | Domain name for SSL certs, subdomain routing, and QR enrollment |
| `TAK_WEBADMIN_PASSWORD` | Password for the `webadmin` account (default: `FastTAK-Admin-1!`) |
| `AUTHENTIK_ADMIN_PASSWORD` | Authentik admin UI password for `akadmin` (default: `FastTAK-Admin-1!`) |

Change both default passwords for production. Additional optional variables (SMTP relay, LDAP base DN, admin email) are documented in `.env.example`.

### Version pins

| Variable             | Default    | Description                                |
| -------------------- | ---------- | ------------------------------------------ |
| `TAK_VERSION`        | `5.6`      | TAK Server Docker image tag                |
| `AUTHENTIK_VERSION`  | `2026.2.1` | Authentik server, worker, and LDAP outpost |
| `MEDIAMTX_VERSION`   | `1.15.5`   | MediaMTX video streaming                   |
| `NODERED_VERSION`    | `4.1`      | Node-RED                                   |
| `TAK_PORTAL_VERSION` | `1.2.53`   | TAK Portal (git tag)                       |

### Subdomains

Each service gets a configurable subdomain (defaults shown):

| Variable              | Default     | Routes to            |
| --------------------- | ----------- | -------------------- |
| `TAKSERVER_SUBDOMAIN` | `takserver` | TAK Server web admin |
| `MEDIAMTX_SUBDOMAIN`  | `stream`    | MediaMTX streaming   |
| `AUTHENTIK_SUBDOMAIN` | `auth`      | Authentik SSO        |
| `TAKPORTAL_SUBDOMAIN` | `portal`    | TAK Portal           |
| `NODERED_SUBDOMAIN`   | `nodered`   | Node-RED             |

### Internal secrets

Generated by `setup.sh` — users don't interact with these directly. If not using `setup.sh`, generate with the noted commands.

| Variable               | Description                                                                       |
| ---------------------- | --------------------------------------------------------------------------------- |
| `TAK_DB_PASSWORD`      | TAK database password (`openssl rand -hex 16`)                                    |
| `APP_DB_PASSWORD`      | App database password — shared by Authentik and Node-RED (`openssl rand -hex 16`) |
| `AUTHENTIK_SECRET_KEY` | Authentik internal signing key (`openssl rand -hex 32`)                           |
| `AUTHENTIK_API_TOKEN`  | Authentik bootstrap API token (`openssl rand -hex 32`)                            |
| `LDAP_BIND_PASSWORD`   | LDAP service account password (`openssl rand -hex 16`)                            |

## Ports

| Port    | Service    | Protocol                                       |
| ------- | ---------- | ---------------------------------------------- |
| 80, 443 | Caddy      | HTTP/HTTPS (Let's Encrypt)                     |
| 8089    | TAK Server | CoT over TLS (ATAK/iTAK/TAK Aware)             |
| 8443    | TAK Server | Client-cert HTTPS (mutual TLS)                 |
| 8446    | TAK Server | Password-auth HTTPS (web admin, QR enrollment) |
| 8554    | MediaMTX   | RTSP — video ingress from cameras, drones      |
| 1935    | MediaMTX   | RTMP — video ingress from OBS, encoders        |
| 8888    | MediaMTX   | HLS — browser video playback                   |
| 3000    | TAK Portal | Direct access (bypasses Caddy auth)            |
| 1880    | Node-RED   | Direct access (bypasses Caddy auth)            |

Ports 8089, 8443, and 8446 are direct TAK client connections — they bypass Caddy because TAK clients use mutual TLS with client certificates.

### Production hardening

Ports 3000 (TAK Portal) and 1880 (Node-RED) are exposed directly for development convenience. In production, remove these port mappings from `docker-compose.yml` and access the services only through Caddy, which enforces Authentik SSO:

```yaml
# Remove these lines from docker-compose.yml for production:
#   tak-portal → ports: ["3000:3000"]
#   nodered    → ports: ["1880:1880"]
```

## Certificate Management

Use `certs.sh` for certificate operations:

```bash
./certs.sh list                          # List all certs
./certs.sh create-client alice           # Create client cert
./certs.sh download alice.p12            # Download .p12 to host
./certs.sh create-server my.domain.com   # Create server cert for FQDN
./certs.sh ca-info                       # Show CA details + expiry
./certs.sh create-user webadmin 'Pass!'  # Create TAK admin user
./certs.sh revoke alice                  # Revoke a certificate
```

Run `./certs.sh help` for the full reference.

Certificate files are also directly accessible on the host at `./tak/certs/files/`.

For detailed information about how TAK certificates work, see [docs/certificates.md](docs/certificates.md).

## User Management

TAK Portal (`http://localhost:3000`) is the primary interface for managing users, groups, and certificate enrollment.

### Enrollment flow

1. Create a user in TAK Portal (Users → Create)
2. Assign groups — groups prefixed with `tak_` become TAK channels
3. Click the QR button next to the user
4. User scans the QR code with ATAK/iTAK/TAK Aware
5. The TAK client enrolls directly with TAK Server and receives its certificate

For details on authentication flows and LDAP, see [docs/authentication.md](docs/authentication.md).

### Authentik admin (advanced)

For advanced Authentik configuration (custom flows, branding, SMTP), access the admin UI at `https://auth.<FQDN>` in production. For local development, Authentik's port isn't exposed directly — run a temporary container that forwards port 9000 from your host into the Docker network:

```bash
docker run --rm -d --name ak-forward \
  --network fasttak_default \
  -p 9000:9000 \
  alpine/socat tcp-listen:9000,fork,reuseaddr tcp-connect:authentik-server:9000
# Access at http://localhost:9000 — login: akadmin / AUTHENTIK_ADMIN_PASSWORD
# Clean up: docker rm -f ak-forward
```

## Node-RED

Node-RED is available at `http://localhost:1880` (or `https://nodered.<FQDN>` via Caddy in production).

On first boot, FastTAK pre-installs `node-red-contrib-postgresql` and `node-red-contrib-tak`, and configures a PostGIS database connection. A `nodered` LDAP user is automatically created in the `tak_ROLE_ADMIN` group so CoT messages from Node-RED flows reach all TAK clients.

## Updating

```bash
# Update .env with new version pins, then:
docker compose pull
docker compose up -d

# For TAK Server updates: run setup.sh with the new release ZIP,
# update TAK_VERSION in .env, then docker compose up -d
```

## Testing

Run a full greenfield integration test (setup → start → verify → teardown):

```bash
./start.sh --test takserver-docker-5.6-RELEASE-6.zip
```

This builds from scratch, starts the full stack, runs automated checks, and tears everything down. Requires the tak.gov release ZIP.

## Stopping and Teardown

```bash
# Stop services (preserves databases and ./tak/ config)
docker compose down

# Full reset (destroys database data, keeps ./tak/ certs and config)
docker compose down -v

# Complete wipe (including certs and config)
docker compose down -v && rm -rf tak/ .env
```

`./tak/` is a bind-mount — certs, CoreConfig.xml, and logs always survive `down`. Only named volumes (PostgreSQL data) are removed with `-v`.

## Resource Limits

FastTAK does not enforce resource limits — your hardware varies. Recommended starting points:

| Service            | Recommended Memory | Notes                                          |
| ------------------ | ------------------ | ---------------------------------------------- |
| `tak-server`       | 4-8 GB             | JVM heap; scales with connected clients        |
| `tak-database`     | 1-2 GB             | PostgreSQL shared_buffers                      |
| `app-db`           | 1 GB               | PostGIS — shared by Authentik and Node-RED     |
| `authentik-server` | 1-2 GB             | Python/Django                                  |
| `authentik-worker` | 1 GB               | Background tasks                               |
| `nodered`          | 512 MB             | Depends on installed nodes and flow complexity |
| `mediamtx`         | 512 MB             | Scales with concurrent streams                 |
| `caddy`            | 256 MB             | Reverse proxy                                  |
| `redis`            | 256 MB             | In-memory cache                                |

To set limits, add `deploy.resources.limits` to a service in `docker-compose.yml`:

```yaml
tak-server:
  # ... existing config ...
  deploy:
    resources:
      limits:
        memory: 4G
```

## Troubleshooting

**Services not starting?**

```bash
docker compose ps                           # check status
docker compose logs -f                      # follow all logs
docker compose logs tak-server --tail 50    # specific service
```

**TAK Server not healthy after 5 minutes?**

```bash
docker compose logs tak-server | tail -30
```

**Identity bootstrap failed?**

```bash
docker compose logs init-identity
```

**QR enrollment not working?**

- Ensure TAK Server is healthy: `docker compose ps tak-server`
- Check TAK Portal logs: `docker compose logs tak-portal`
- Verify the FQDN resolves to the host from the client device
- Enrollment tokens expire after 15 minutes — generate a fresh QR

**Certificate issues?**

```bash
./certs.sh ca-info    # check CA cert expiry
```

Caddy auto-manages Let's Encrypt certs. TAK Server internal CA cert expiry is monitored by the healthcheck — the container becomes `unhealthy` when any cert is within 30 days of expiring.

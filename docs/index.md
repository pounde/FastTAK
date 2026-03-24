# FastTAK

A containerized TAK Server deployment stack using Docker Compose.

FastTAK bundles TAK Server with identity management (Authentik), a reverse proxy (Caddy),
video streaming (MediaMTX), automation (Node-RED), and a health monitoring dashboard —
all configured and running with a single `docker compose up`.

## Quick Links

- [Getting Started](quickstart-walkthrough.md) — end-to-end setup walkthrough
- [Authentication](authentication.md) — how SSO, LDAP, and TAK Portal work together
- [Certificates](certificates.md) — cert management and enrollment

## Requirements

- Docker Engine + Docker Compose v2 (v2.20+)
- Official TAK Server release ZIP from [tak.gov](https://tak.gov)

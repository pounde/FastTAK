# Contributing to FastTAK

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager
- [just](https://github.com/casey/just) — command runner
- [Docker](https://docs.docker.com/get-docker/) with Compose v2.24+
- TAK Server Docker zip from [tak.gov](https://tak.gov)

## Setup

```bash
# Extract TAK Server files and build images
./setup.sh <takserver-docker-X.X.zip>

# Configure environment (setup.sh copies .env.example to .env)
vim .env

# Install pre-commit hooks (runs unit tests + shellcheck on commit)
just setup-dev
```

## Development

```bash
just dev-up      # Start with direct-access ports (3000, 1880, 8180)
just dev-down    # Stop
```

The dev stack exposes TAK Portal, Node-RED, and Monitor directly on the host
for convenience. These ports bypass Caddy + LDAP authentication.

## Testing

```bash
just test              # Unit tests + shellcheck (no Docker needed)
just test-integration  # Full stack test (can run alongside dev stack)
```

Integration tests use a +10000 port offset to avoid conflicts with a running
development stack. The integration test creates an isolated Docker project environment
and utilizes an isolated `tak/` directory.

## Production

```bash
just up     # Start production stack
just down   # Stop
```

Production exposes only protocol endpoints: TAK Server (8089, 8446),
Caddy (80, 443), and MediaMTX (8554, 1935, 8888). TAK Portal, Node-RED,
and Monitor are only reachable through Caddy + LDAP authentication.

## All Commands

Run `just help` to see all available recipes.

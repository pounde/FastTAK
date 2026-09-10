# Contributing to FastTAK

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager
- [just](https://github.com/casey/just) — command runner
- [Docker](https://docs.docker.com/get-docker/) with Compose v2.24+
- TAK Server Docker zip from [tak.gov](https://tak.gov)

## Setup

```bash
# Extract TAK Server files and build images
just setup <takserver-docker-X.X.zip>

# Configure environment (setup.sh copies .env.example to .env)
vim .env

# Install pre-commit hooks (runs unit tests + shellcheck on commit)
just setup-dev
```

## Development

```bash
just up            # whole stack, with the post-start checks
just up monitor    # rebuild one service after a code change
just down
```

`just` with no arguments lists every recipe with what it does.

## Testing

```bash
just test               # unit tests + shellcheck + go (no Docker needed)
just test-stack cycle   # full integration cycle against an isolated stack
```

Integration tests use a +10000 port offset to avoid conflicts with a running
development stack. The integration test creates an isolated Docker project environment
and utilizes an isolated `tak/` directory.

For iterative work against the integration stack:

```bash
just test-stack up      # stand it up (prints the project name)
just test-stack run     # run the assertions; repeat after changes
just test-stack down    # tear it down when finished
```

## Production

```bash
just up     # Start production stack
just down   # Stop
```

Production exposes only protocol endpoints: TAK Server (8089, 8446),
Caddy (80, 443), and MediaMTX (8554, 1935, 8888). Node-RED and Monitor
are only reachable through Caddy + LDAP authentication.

## All Commands

Run `just` to see all available recipes.

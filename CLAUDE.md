# CLAUDE.md

## Design Decisions

Before proposing architectural changes, adding dependencies, or making design tradeoffs, consult `docs/decisions.md`. It contains the project's design decision log with rationale and alternatives considered.

## Testing

### Unit Tests
Run with `just test` (fast, no Docker needed). All unit tests are in `tests/`.

### Integration Tests
Integration tests run against a real Docker stack. There are two modes:

**Full cycle (CI):**
```bash
just test-stack cycle   # fast suite, then builds the stack, runs tests, tears down
```

**Iterative development:**
```bash
just test-stack up                # stand up isolated test stack (detached)
just test-stack up --foreground   # foreground — dies with the process; use in background agents
just test-stack run               # run assertions against the running stack
just test-stack down              # tear down all test stacks
```

After code changes, rebuild just the monitor in the test stack:
1. Find the project name in the `test-stack up` output or `/tmp/fastak-test-*/.test-state`
2. `docker compose -p <project> -f docker-compose.yml -f docker-compose.test.yml build monitor`
3. `docker compose -p <project> -f docker-compose.yml -f docker-compose.test.yml up -d --force-recreate monitor`
4. `just test-stack run`

**IMPORTANT:** Always tear down test stacks when done. Run `just test-stack down` before ending your session.

## TAK Server Version

FastTAK requires the **hardened** TAK Server bundle, 5.8 or later. `setup.sh`
refuses older bundles — earlier releases place PGDATA outside the mounted
volume. See `docs/decisions.md` DD-051.

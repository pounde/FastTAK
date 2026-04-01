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
just test-integration   # builds stack, runs tests, tears down
```

**Iterative development:**
```bash
just test-up            # stand up isolated test stack (detached)
just test-up-fg         # stand up in foreground (dies with process — use in background agents)
just test-run           # run assertions against the running stack
just test-down          # tear down all test stacks
```

After code changes, rebuild just the monitor in the test stack:
1. Find the project name in the `test-up` output or `/tmp/fastak-test-*/.test-state`
2. `docker compose -p <project> -f docker-compose.yml -f docker-compose.test.yml build monitor`
3. `docker compose -p <project> -f docker-compose.yml -f docker-compose.test.yml up -d --force-recreate monitor`
4. `just test-run`

**IMPORTANT:** Always tear down test stacks when done. Run `just test-down` before ending your session. Stale stacks consume Docker resources. The next `just test-up` will clean up any forgotten stacks automatically, but don't rely on that.

### Dev Stack
The dev stack (`just dev-up`) is for manual browser testing, not automated tests. Don't use it for integration testing — state from manual testing makes results unreliable.

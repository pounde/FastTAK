# FastTAK Design Decisions

Significant architectural and design decisions, with reasoning. Newest first.

---

## DD-022: Health Monitoring Architecture Refactor

**Date:** 2026-03-28
**Status:** Decided

**Decision:** Separate health data collection, evaluation, caching, and alerting into distinct layers. Health modules return raw data only (no status). An evaluator applies configurable thresholds from `monitor/config/thresholds.yml` (overridable via `FASTAK_MON_*` env vars) to produce status. The scheduler polls health modules on configured intervals, runs results through the evaluator, and writes to an in-memory cache. `GET /api/health` returns the full cache. Individual health endpoints (`/api/health/database`, etc.) remain live for debugging. Four status levels: ok, note, warning, critical.

**Why:** The previous architecture had health modules computing status with hardcoded thresholds, the scheduler re-querying the same data for alerting, and the dashboard querying a third time. Responsibilities were tangled, thresholds were unchangeable without code edits, and there was no way to distinguish informational changes (update available) from operational concerns (disk full). The refactor gives each layer a single clear responsibility and makes the entire monitoring policy configurable.

**Alert design:** The evaluator determines whether a status warrants alerting (via `should_alert` based on `alert_min_level` per service). The scheduler passes this to the alert engine, which handles deduplication and cooldown only — it does not filter by severity. Recovery transitions (elevated state back to ok) are logged but do not send email/SMS alerts. Alert cooldown is configurable globally via `alert_cooldown` in `thresholds.yml`.

---

## DD-021: Live Data Size Uses Heap-Only Measurement

**Date:** 2026-03-28
**Status:** Decided

**Decision:** The database health endpoint reports "live data" using `pg_total_relation_size(relid)` summed across all user tables. This includes table data, indexes, and TOAST — everything that belongs to the user's data. The gap between `pg_database_size` and this sum is PostgreSQL system overhead (system catalogs, WAL, internal structures) — roughly 10-15 MB regardless of database size.

**Why:** When the gap between total size and live data is large, it indicates table bloat from dead tuples that VACUUM FULL would reclaim. On a 30 GB database with 15 GB live data, the user knows half is reclaimable bloat. On a 30 GB database with 29.8 GB live data, VACUUM FULL would be pointless. This gives users the information to make that call without FastTAK imposing a threshold.

**Alternatives considered:**

- `pg_relation_size(relid, 'main')` (heap only) — excludes indexes and TOAST from "live data," making the gap include both reclaimable bloat and legitimate index space. Less accurate representation of what the user's data actually occupies.
- `pgstattuple` extension — gives exact dead tuple byte counts but requires installing an extension that TAK Server doesn't ship.
- Showing a computed "reclaimable" field — removed because the approximation includes system overhead, and labeling it "reclaimable" implies precision we don't have. Showing two raw values and letting users interpret the gap is more honest.

---

## DD-020: Global Autovacuum Tuning via Startup Flags

**Date:** 2026-03-27
**Status:** Decided

**Decision:** PostgreSQL autovacuum is tuned via global `-c` startup flags injected by `tak-database/start.sh`, not per-table `ALTER TABLE SET` statements. Three configurable `.env` variables with FastTAK defaults: `PG_AUTOVACUUM_SCALE_FACTOR` (default: 0.05), `PG_AUTOVACUUM_COST_LIMIT` (default: 1000), `PG_MAINTENANCE_WORK_MEM` (default: 256MB). These defaults are applied automatically — users who want stock PostgreSQL behavior must explicitly set them to `0.2`, `200`, `64MB`. Autovacuum health status uses a minimum dead tuple threshold (`AUTOVACUUM_MIN_DEAD_TUPLES`, default 1000) — tables below this count are shown in the UI but don't affect the overall status. This prevents tiny config tables with a handful of dead rows from triggering false alerts on a fresh stack.

**Why:** Per-table tuning (`ALTER TABLE cot_router SET (autovacuum_vacuum_scale_factor = 0.05)`) is more precise but couples us to TAK Server's schema — table names could change across versions. The CoT tables are the dominant workload, so global settings effectively tune for them. Startup flags are the simplest mechanism to wire through `.env` and require no SQL execution or schema knowledge.

---

## DD-019: Monitor Becomes the API Service

**Date:** 2026-03-27
**Status:** Decided (supersedes [DD-005](#dd-005-monitor-is-read-only-docker-socket-is-read-only))

**Decision:** The monitor service expands from a read-only health dashboard to the FastTAK management API. It gains the Authentik API token for user management and communicates with TAK Server via mTLS on port 8443 (using the `svc_fasttakapi` cert) for cert operations. [DD-005](#dd-005-monitor-is-read-only-docker-socket-is-read-only)'s read-only constraint is superseded.

**Why:** [DD-012](#dd-012-api-is-the-management-layer-dashboard-is-a-consumer) established that the API is the single management layer. The monitor already has the FastAPI infrastructure, Docker client, scheduler, and config patterns. Splitting management into a separate service would duplicate this infrastructure for no benefit. The existing cert operations endpoints (`/api/ops/certs/*`) already broke [DD-005](#dd-005-monitor-is-read-only-docker-socket-is-read-only)'s read-only constraint — this formalizes the expansion.

**Portability:** User management and cert operations use HTTP to Authentik and TAK Server, not `docker exec`. The Docker client remains optional — used only for container health monitoring, which degrades gracefully without it. This preserves [DD-012](#dd-012-api-is-the-management-layer-dashboard-is-a-consumer)'s portability goal.

**Security:** The Authentik API token and TAK Server mTLS cert grant admin access to their respective services. These are scoped to the management API's purpose and protected by the reverse proxy authentication layer.

---

## DD-018: User Cert Enrichment is Opt-In

**Date:** 2026-03-27
**Status:** Decided

**Decision:** `GET /api/users` returns Authentik user data only by default. TAK Server cert status is included only when the caller passes `?include=certs`. Per-user detail (`GET /api/users/{id}`) always includes cert data.

**Why:** Cert status requires an mTLS HTTP call to TAK Server's `certadmin` API per user. For list endpoints this is an N+1 problem that scales poorly. Most list consumers (dashboards, dropdowns, search) don't need cert data. Making it opt-in keeps the default fast and lets consumers that need cert data request it explicitly.

---

## DD-017: User TTL via Revocation Scheduler

**Date:** 2026-03-27
**Status:** Decided

**Decision:** Temporary users (e.g., external collaborators) are given an expiry timestamp stored as `fastak_expires` in Authentik's user `attributes` JSON. A FastTAK scheduled task (configurable interval via `user_expiry_check_interval`, default 60s) checks for expired users and enforces expiry by deactivating the Authentik account and revoking all TAK Server certs via the TAK Server `certadmin` REST API.

**Why:** TAK Server's enrollment cert validity (`validityDays` in CoreConfig.xml) is a global setting — no per-user control. Intercepting the enrollment flow to generate custom-TTL certs would require replicating TAK Server's undocumented data package format. Cert revocation is immediate (TAK Server rejects revoked certs on next connection), version-proof, and coordinates both Authentik deactivation and cert revocation as a single operation.

**Alternatives considered:**

- Per-user cert validity during enrollment — not supported by TAK Server
- Hijacking enrollment to generate certs with custom validity — fragile, requires reverse-engineering TAK Server's data package format
- Authentik's built-in `goauthentik.io/user/expires` — deletes the user instead of deactivating, losing audit trail
- Authentik Expression Policy — doesn't affect cert-based (mTLS) access, only password auth on port 8446

---

## DD-016: Service Account Naming Convention

**Date:** 2026-03-25
**Status:** Decided

**Decision:** All service accounts (machine-to-machine credentials) use the prefix `svc_` (e.g., `svc_fasttakapi`, `svc_nodered`). Human admin accounts use no prefix. The `USERS_HIDDEN_PREFIXES` in TAK Portal settings hides `svc_` accounts from the user management UI.

**Why:** The original names (`admin`, `nodered`) are too generic and could collide with real usernames. A consistent prefix makes service accounts immediately identifiable, easy to filter in the UI, and easy to exclude from user-facing operations. `svc_` was chosen over `service_` (shorter), `admin_` (confusing — implies human admin), and `sa_` (less readable).

**Applies to:**

- `svc_fasttakapi` — FastTAK API machine-to-machine cert (replaces `admin`)
- `svc_nodered` — Node-RED service account (replaces `nodered`)
- `adm_ldapservice` — kept as-is (Authentik LDAP convention, already hidden by `adm_` prefix)

---

## DD-015: Passwordless Users by Default

**Date:** 2026-03-25
**Status:** Decided

**Decision:** User creation does not set a password. Passwords for users requiring username/password authentication (e.g., for web-based clients like WebTAK or the admin UI) are set explicitly via `POST /api/users/{id}/password`. Device-only users never have an account password.

**Why:** The enrollment flow uses Authentik app passwords (15-minute TTL tokens), not the user's account password. A user with no password cannot authenticate via LDAP (verified by test — Authentik returns `INVALID_CREDENTIALS`). This is the most secure default: no credential exists to be compromised. Users who need browser access can have a password set explicitly.

**Verified:** Authentik's `InbuiltBackend` calls Django's `check_password()`, which returns `False` for users with no usable password. The `TokenBackend` (app passwords) works independently of the account password.

---

## DD-014: Enrollment Token TTL is Configurable

**Date:** 2026-03-25
**Status:** Decided

**Decision:** The enrollment app password TTL defaults to 15 minutes but is configurable via environment variable (`ENROLLMENT_TTL_MINUTES`). Any value with different use cases should be a config option.

**Why:** 15 minutes is reasonable for in-person QR code scanning, but other use cases (emailing enrollment links, remote deployments, batch provisioning) may need longer windows. Making it configurable avoids hardcoding an assumption about deployment context.

---

## DD-013: ROLE_ADMIN is Both a Permission Role and a Channel

**Date:** 2026-03-25
**Status:** Documented (not a decision — clarification of existing TAK Server behavior)

**Context:** TAK Server's LDAP config has `adminGroup="ROLE_ADMIN"`. Membership in `tak_ROLE_ADMIN` grants admin API access. TAK Server does not distinguish between permission groups and communication channels — all groups are both. `ROLE_ADMIN` appears as a channel in TAK Aware alongside user-created channels like `Field Team Alpha`. This is TAK Server's design, not something FastTAK controls.

---

## DD-012: API is the Management Layer, Dashboard is a Consumer

**Date:** 2026-03-25
**Status:** Decided

**Decision:** The FastTAK API is the single management interface for the stack. All management operations (user CRUD, plugin management, cert operations) go through the API. The dashboard and any future admin consoles are consumers of the API, not orchestrators.

**Why:** Decouples the management logic from any specific UI. Enables swapping dashboards, building CLI tools, or integrating with external systems without reimplementing orchestration. Makes the API portable — it can be used outside the Docker Compose stack by pointing it at any TAK Server + identity provider.

**Implications:**

- All external service URLs are environment variables, never hardcoded Docker DNS names
- Docker client is optional — API starts without it, container health endpoints degrade gracefully
- See [DD-019](#dd-019-monitor-becomes-the-api-service) for how the monitor service was formalized as the API service

---

## DD-011: Docs Must Cover All Features

**Date:** 2026-03-24
**Status:** Decided

**Decision:** Documentation (mkdocs) must include user guides for all features. Users should be able to deploy and operate FastTAK entirely from the documentation.

---

## DD-010: Release Path Filtering is Exclude-Only

**Date:** 2026-03-24
**Status:** Decided

**Decision:** Semantic release triggers on any `feat:`/`fix:` commit unless the commit _only_ touches `tests/`, `docs/`, or `.github/`. New directories and services trigger releases by default.

**Why:** An include-list risks missing new services or folders. Exclude-only logic means everything triggers a release unless explicitly excluded, which is safer as the project grows.

---

## DD-009: CI Runs Unit Tests Only

**Date:** 2026-03-24
**Status:** Decided (constraint-driven)

**Decision:** GitHub Actions CI runs unit tests (`just test`) and shellcheck only. Full integration tests (`just test-integration`) stay manual/local. A placeholder exists for a future integration job. Unit tests run on pre-commit; developers can bypass with `--no-verify` for WIP commits.

**Why:** TAK Server images are built from a proprietary tak.gov zip that isn't available in CI. No public TAK Server images exist.

---

## DD-008: Tilde Pins at Patch Level for Python Dependencies

**Date:** 2026-03-24
**Status:** Decided

**Decision:** All Python dependencies use compatible release pins at patch level (`~=X.Y.0`), e.g., `docker~=7.1.0`, `fastapi~=0.135.0`.

**Why:** Pre-1.0 packages treat minor bumps as breaking changes. Patch-level is consistent across all deps — no need to reason about which packages follow semver strictly.

---

## DD-007: Modern Ciphers for Cert Generation

**Date:** 2026-03-23
**Status:** Decided

**Decision:** Certificate generation uses ECDSA P-384 and AES-256-CBC instead of legacy RSA-2048/3DES.

**Why:** Modern cipher suites provide stronger security. Legacy software compatibility is not a design constraint.

---

## DD-006: Node-RED TLS Uses PEM Files with servername Override

**Date:** 2026-03-23
**Status:** Decided

**Decision:** `start.sh` extracts PEM files from `.p12` during init. The `tls-config` node uses `cert`/`key`/`ca` file paths (not inline PEM, not `.p12` direct). `verifyservercert = true` with `servername` set to `FQDN`.

**Why:** Node-RED's `certname`/`keyname` are for editor-uploaded files. Inline PEM is treated as a file path. The `servername` override is needed because Node-RED connects via Docker hostname (`tak-server`) but the cert is issued for the external FQDN. Traffic stays on the Docker bridge — `servername` only affects cert validation.

---

## DD-005: Monitor is Read-Only, Docker Socket is Read-Only

**Date:** 2026-03-23
**Status:** Superseded by [DD-019](#dd-019-monitor-becomes-the-api-service)

**Decision:** The monitor observes but does not control containers. Docker socket is mounted `:ro`. Service management (restart/stop/start), LDAP operations, and the Authentik API token are all excluded from the monitor.

**Why:** Reduces security surface. Containers should restart via Docker's own restart policies if they fail health checks. Management operations go through the API, not the monitoring layer.

**Superseded:** The monitor service was formalized as the FastTAK management API in [DD-019](#dd-019-monitor-becomes-the-api-service). It now holds the Authentik API token and communicates with TAK Server via mTLS for cert and user operations.

---

## DD-004: Two Init Containers with Different Lifecycles

**Date:** 2026-03-23
**Status:** Decided

**Decision:** Two separate one-shot init containers: `init-core` (needs DB healthy) and `init-identity` (needs both Authentik AND TAK Server healthy). Not combined into one.

**Why:** Different dependency chains. Combining them would force core startup to depend on Authentik being healthy, slowing the entire stack. Different languages (bash vs Python) and different concerns reinforce the split.

---

## DD-003: CloudTAK Excluded from FastTAK Stack

**Date:** 2026-03-23
**Status:** Decided

**Decision:** CloudTAK is not part of the Docker Compose stack. All CloudTAK subdomain vars, Caddyfile routes, and `.env.example` entries are removed.

**Why:** FastTAK is a server-side infrastructure platform. It provides backend orchestration for TAK deployments, not client-side applications. Client platforms like CloudTAK have their own deployment requirements and are separate concerns.

---

## DD-002: No Docker Compose Profiles — Single Unified Stack

**Date:** 2026-03-23
**Status:** Decided

**Decision:** The stack has no `--profile` flags. All services (TAK Server, Authentik, Caddy, MediaMTX, Node-RED, TAK Portal) are always present. TAK Server application files are served via a bind mount using the `TAK_HOST_PATH` variable (pointing to the user's `tak/` directory on the host), not baked into Docker images.

**Why:** Profiles added complexity without benefit — Authentik/LDAP/TAK Portal were being used for all testing anyway. A single stack is simpler to document, test, and support. Bind mounts via `TAK_HOST_PATH` keep the user's TAK configuration on the host filesystem, making upgrades and customization straightforward without rebuilding images.

---

## DD-001: TAK Server Images Are Built with Application Files Baked In

**Date:** 2026-03-23
**Status:** Decided

**Decision:** Users add `COPY tak/ /opt/tak/` to the official TAK Dockerfiles before building. A `setup.sh` script automates extracting the TAK zip and building images. Named Docker volumes (not bind mounts) are used for persistence.

**Why:** The official tak.gov Dockerfiles are just base runtimes (Java, PostgreSQL+PostGIS) with no application files. Bind mounts were tried but rejected. Baking files in makes everything self-contained and compatible with named volumes. Volumes were consolidated from 6 to 4 by merging config + certs into `tak-data`.

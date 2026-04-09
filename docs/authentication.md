# FastTAK Authentication Guide

## Overview

FastTAK supports two authentication modes:

1. **Certificate-only** — users authenticate with client certificates (.p12). No passwords, no LDAP. Simple but no centralized user management.

2. **LDAP via LLDAP** — users authenticate with username/password against LLDAP, a lightweight LDAP server. Certificates still required for TAK client connections, but web admin and portal access use password-based login.

## How LDAP Authentication Works

The `init-identity` container configures this chain on startup:

```
TAK Server ←→ ldap-proxy ←→ LLDAP ←→ PostgreSQL (app-db)
```

### The bootstrap sequence

1. **LLDAP starts** — lightweight LDAP server backed by PostgreSQL (app-db)
2. **`init-identity` runs** — creates the LDAP infrastructure via LLDAP's GraphQL API:
   - Creates `adm_ldapservice` service account (LDAP bind user)
   - Creates `webadmin` user with password from `TAK_WEBADMIN_PASSWORD` (default: `FastTAK-Admin-1!`)
   - Creates `tak_ROLE_ADMIN` group
   - Configures custom attribute schemas
   - Generates TAK Portal `settings.json`
   - Exits
3. **ldap-proxy starts** — listens on port 3389 (internal), proxies LDAP binds to LLDAP and provides `/auth/verify` for Caddy forward auth
4. **TAK Server reads CoreConfig.xml** — connects to ldap-proxy for auth

### How a user logs in (web admin on 8446)

1. User opens `https://takserver.example.com` (or `https://host:8446`)
2. TAK Server shows login form
3. User enters username + password
4. TAK Server queries ldap-proxy: `cn=<username>,ou=people,dc=takldap`
5. ldap-proxy forwards the bind to LLDAP, which validates credentials
6. If valid, TAK Server checks group membership for `tak_ROLE_ADMIN`
7. User gets admin or read-only access based on groups

### How a TAK client connects (ATAK/iTAK)

1. Client connects to port 8089 with client certificate (TLS mutual auth)
2. TAK Server validates the cert is signed by its CA
3. TAK Server looks up the cert's CN in LDAP to find group membership
4. Groups with `tak_` prefix become TAK channels (e.g., `tak_team1` → channel `team1`)
5. Group membership is cached for 30 seconds (`updateinterval="30"`)

### Important: LDAP cache delay

When a new user is created or group membership changes, TAK Server's LDAP cache takes up to 30 seconds to refresh. During this window:
- The user can connect (cert auth works immediately)
- But they may see "No channels found"
- After 30 seconds, disconnect and reconnect — channels appear

### Groups and TAK channels

| LLDAP Group | TAK Channel | Who sees it |
|----------------|-------------|-------------|
| `tak_ROLE_ADMIN` | (admin access) | Admin users |
| `tak_team1` | `team1` | Users in group |
| `tak_fires` | `fires` | Users in group |

Only groups with the `tak_` prefix appear as TAK channels. Create groups in LLDAP (or TAK Portal), then assign users.

### Key components

| Component | What it does | Runs on |
|-----------|-------------|---------|
| `lldap` | Lightweight LDAP server (Rust), user directory, GraphQL management API | Port 3890 (internal) |
| `ldap-proxy` | LDAP proxy with enrollment token interception, forward auth endpoint | Port 3389 (internal) |
| `init-identity` | One-shot bootstrap of LDAP users, groups, and schemas via GraphQL | Exits after setup |
| `adm_ldapservice` | Service account TAK Server uses to query LDAP | LLDAP user |
| `CoreConfig.xml` | TAK Server config with LDAP connection details | `/opt/tak/` |

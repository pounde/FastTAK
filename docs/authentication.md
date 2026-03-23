# FastTAK Authentication Guide

## Overview

FastTAK supports two authentication modes:

1. **Certificate-only** — users authenticate with client certificates (.p12). No passwords, no SSO. Simple but no centralized user management.

2. **LDAP via Authentik** — users authenticate with username/password against Authentik's LDAP outpost. Certificates still required for TAK client connections, but web admin and portal access use password-based login.

## How LDAP Authentication Works

The `init-identity` container configures this chain on startup:

```
TAK Server ←→ Authentik LDAP Outpost ←→ Authentik Server ←→ Authentik Database
```

### The bootstrap sequence

1. **Authentik starts** — server, worker, PostgreSQL, Redis all come up
2. **`init-identity` runs** — creates the LDAP infrastructure via Authentik's API:
   - Creates `adm_ldapservice` service account (LDAP bind user)
   - Creates `webadmin` user with password from `TAK_WEBADMIN_PASSWORD` (default: `FastTAK-Admin-1!`)
   - Creates `tak_ROLE_ADMIN` group
   - Creates LDAP authentication flow with 3 stages:
     - **Identification stage** — accepts username
     - **Password stage** — validates password
     - **Login stage** — creates session
   - Creates LDAP provider (base DN: `DC=takldap`)
   - Creates LDAP application and outpost
   - Generates TAK Portal `settings.json`
3. **LDAP outpost starts** — listens on port 3389 (internal)
4. **TAK Server reads CoreConfig.xml** — connects to LDAP outpost for auth

### How a user logs in (web admin on 8446)

1. User opens `https://takserver.example.com` (or `https://host:8446`)
2. TAK Server shows login form
3. User enters username + password
4. TAK Server queries Authentik LDAP: `cn=<username>,ou=users,dc=takldap`
5. LDAP outpost validates credentials against Authentik's user database
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

| Authentik Group | TAK Channel | Who sees it |
|----------------|-------------|-------------|
| `tak_ROLE_ADMIN` | (admin access) | Admin users |
| `tak_team1` | `team1` | Users in group |
| `tak_fires` | `fires` | Users in group |

Only groups with the `tak_` prefix appear as TAK channels. Create groups in Authentik (or TAK Portal), then assign users.

### Key components

| Component | What it does | Runs on |
|-----------|-------------|---------|
| `authentik-server` | SSO provider, user database, admin UI | Port 9000 (internal) |
| `authentik-ldap` | LDAP protocol frontend for Authentik | Port 3389 (internal) |
| `init-identity` | One-shot bootstrap of LDAP config | Exits after setup |
| `adm_ldapservice` | Service account TAK Server uses to query LDAP | Authentik user |
| `CoreConfig.xml` | TAK Server config with LDAP connection details | `/opt/tak/` |

### Authentik admin access

Authentik's admin UI is at `https://auth.example.com` (or whatever `AUTHENTIK_SUBDOMAIN` is set to). Login with `akadmin` and the `AUTHENTIK_ADMIN_PASSWORD` from `.env`.

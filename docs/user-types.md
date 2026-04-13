# User Types

Every account in FastTAK has a type that determines its purpose and what rules apply to it. The type is stored as the `fastak_user_type` attribute in LLDAP and set at creation time.

## Types

### `user` — People

Regular human users. They authenticate via QR enrollment or manual cert download, and use ATAK, iTAK, WinTAK, or WebTAK.

| Rule | Enforcement |
|------|-------------|
| At least one group required | Creation and group updates |
| Groups must exist | Creation and group updates |

### `svc_data` — Data Service Accounts

Machine accounts that send and receive CoT on assigned channels. Prefixed `svc_`. Authenticate exclusively via client certificate.

| Rule | Enforcement |
|------|-------------|
| At least one group required | Creation and group updates |
| Groups must exist | Creation and group updates |

Examples: UAS ground control stations, sensor feeds, ADS-B providers.

### `svc_admin` — Admin Service Accounts

Machine accounts with TAK Server admin API access via `certmod -A`. Prefixed `svc_`. Authenticate exclusively via client certificate.

| Rule | Enforcement |
|------|-------------|
| Groups forbidden | Creation and group updates |

Examples: `svc_fasttakapi` (the FastTAK API itself).

## The "stays or goes" test

When deciding whether something should be a user or a service account, ask: **if the device is handed to someone else, does the cert stay or go?**

- **Stays** → service account (`svc_data` or `svc_admin`)
- **Goes** → user

See [Certificate Guide — Users, Service Accounts, and Certificates](certificates.md#users-service-accounts-and-certificates) for more detail.

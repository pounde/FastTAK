# CHANGELOG

<!-- version list -->

## v0.13.0 (2026-04-06)

### Features

- Add PgBouncer for Authentik connection pooling
  ([`37b2632`](https://github.com/pounde/FastTAK/commit/37b2632e671ca3e7237b73e8030f8769fd82aa37))


## v0.12.0 (2026-04-06)

### Bug Fixes

- Add default_sni to direct mode Caddyfile
  ([`6233fd5`](https://github.com/pounde/FastTAK/commit/6233fd56331c139c5923aad7b3205fe136121166))

- Justfile up/down recipes use shebang for DEPLOY_MODE logic
  ([`991bda8`](https://github.com/pounde/FastTAK/commit/991bda836b9145213ee215e6160bc2db124d79db))

- Postgres tuning to prevent Authentik connection exhaustion
  ([`3e942c6`](https://github.com/pounde/FastTAK/commit/3e942c6686a5ca5d43ef249a696a0845c52d5c2e))

- TLS health probe mode-aware, fix docs port table
  ([`0f05036`](https://github.com/pounde/FastTAK/commit/0f05036a7b94c5016f9a3a6e9a6eb491aae44a93))

### Chores

- Rename FQDN to SERVER_ADDRESS in portal, nodered, setup, and video-cot scripts
  ([`2318635`](https://github.com/pounde/FastTAK/commit/2318635a70f5e1e8570a2b65599e19fdf27ad40a))

### Documentation

- Add deploy modes documentation and decision record
  ([`f5b106e`](https://github.com/pounde/FastTAK/commit/f5b106e0ed2e723ef8ab9423800d60efdc09fc4f))

- Address PR review — link to cert guide, broaden TAK enrollment note
  ([`cef5177`](https://github.com/pounde/FastTAK/commit/cef51779a0183f455e440a26202a673ad37f0073))

- Update certificate guide for deploy modes
  ([`e99210b`](https://github.com/pounde/FastTAK/commit/e99210bf972fbeb85b719c72b3fa2d8c3998f01e))

### Features

- Generate Caddyfile from init-config based on DEPLOY_MODE
  ([`8819af6`](https://github.com/pounde/FastTAK/commit/8819af67d153165088b8a67e2266511cc9758c92))

- Mode-aware start.sh and justfile, remove dev-up/dev-down
  ([`b6e74dc`](https://github.com/pounde/FastTAK/commit/b6e74dc958125b5e91705d9c832d4a81a85754a1))

- **init-identity**: Create proxy providers for forward auth
  ([`33a0484`](https://github.com/pounde/FastTAK/commit/33a0484fd0134ba722447de660187b3d6c3e815a))

- **init-identity**: Mode-aware URL construction for settings.json
  ([`924b791`](https://github.com/pounde/FastTAK/commit/924b7918297aece902bfdadb8fcd5c7d24662c14))

- **monitor**: Replace Caddyfile parser with mode-aware URL builder
  ([`3fd9359`](https://github.com/pounde/FastTAK/commit/3fd935920879896a5d26e1c7b4b292fdf53ad9b3))

### Testing

- Update integration test setup for SERVER_ADDRESS rename
  ([`66b66c8`](https://github.com/pounde/FastTAK/commit/66b66c8e29e57cc9179476aca9d359c89168e960))

- Update tests for FQDN -> SERVER_ADDRESS rename
  ([`0f0a267`](https://github.com/pounde/FastTAK/commit/0f0a267db0af11c38ce1d160702aa084f082e200))


## v0.11.1 (2026-04-04)

### Bug Fixes

- Prevent LDAP/FileAuthenticator race and app-db connection exhaustion
  ([`1e14f62`](https://github.com/pounde/FastTAK/commit/1e14f621e6daceec665b5bed0e473d3b8aafea0a))


## v0.11.0 (2026-04-03)

### Bug Fixes

- Remove double quotes in confirm() that broke x-data attribute
  ([`fde46f5`](https://github.com/pounde/FastTAK/commit/fde46f510b830a233da5a32f8b1a5787d8e4d745))

### Documentation

- Add group management UI design spec
  ([`f633e48`](https://github.com/pounde/FastTAK/commit/f633e488a642b43bdc0ac75f439421fc5d7956a5))

- Add group management UI implementation plan
  ([`91fa544`](https://github.com/pounde/FastTAK/commit/91fa5441c332a011bc114e2739755a24dc8cde0e))

### Features

- Add Alpine.js state and methods for group management
  ([`bbdabb8`](https://github.com/pounde/FastTAK/commit/bbdabb8b3d87b058b3e8bc5056771cf671fe2cd8))

- Add group assignment dropdown to user detail panel
  ([`3b7aac1`](https://github.com/pounde/FastTAK/commit/3b7aac175e5aa916ec805ce11873255f93ba3cd6))

- Add groups card with create/delete on users page
  ([`b34172b`](https://github.com/pounde/FastTAK/commit/b34172bcc1b5902572756b72866fc9f8572233d8))


## v0.10.0 (2026-04-03)

### Features

- Expose TAK Server API port 8443 for external integrations
  ([`4b5619d`](https://github.com/pounde/FastTAK/commit/4b5619ddb67c9b2d06c65b1866302706a960866b))


## v0.9.0 (2026-04-01)

### Documentation

- Add Google-style API docstrings for Swagger documentation
  ([`2f19eb5`](https://github.com/pounde/FastTAK/commit/2f19eb54cf02028df8d13dfd26a941c3c19d444d))

### Features

- Filter cert health monitoring to infrastructure/service certs only
  ([#25](https://github.com/pounde/FastTAK/pull/25),
  [`2c34eda`](https://github.com/pounde/FastTAK/commit/2c34edabc49ebb0ccb880fd76bf393b4904c1a5b))


## v0.8.1 (2026-04-01)

### Bug Fixes

- **docs**: Add mkdocs extension to render mermaid diagrams
  ([`1104048`](https://github.com/pounde/FastTAK/commit/1104048a73c84ae974b4f3717edaf9fa5760ee0c))


## v0.8.0 (2026-04-01)

### Bug Fixes

- Block revoked service account cert download
  ([`3596f46`](https://github.com/pounde/FastTAK/commit/3596f467ad2104149ca9a6444dc4a53e5cd49448))

- Restore v0.7.1 version files (reverted during branch reconstruction)
  ([`c58434c`](https://github.com/pounde/FastTAK/commit/c58434cc8d27bbfc6305d86ce75054bfe38623dd))

### Documentation

- Design decisions, certificates rewrite, CLAUDE.md
  ([`dcb5dfd`](https://github.com/pounde/FastTAK/commit/dcb5dfd4b14f3c54b666cf5d6712c3ca38a68b27))

### Features

- Bootstrap cleanup and ops cert endpoint removal
  ([`0e1ac45`](https://github.com/pounde/FastTAK/commit/0e1ac45971602898e4a683a74a989a440f6bdf42))

- Service account API, user cert management, and dashboard
  ([`fde9a06`](https://github.com/pounde/FastTAK/commit/fde9a0646f70bff53d0e33197fb364ba55d62382))

- Test infrastructure — test-up/run/down, idempotent tests
  ([`39bb71e`](https://github.com/pounde/FastTAK/commit/39bb71e0f562cd8933d1ce98203c3d67d43ffc1a))

### Refactoring

- Extract shared CRL logic, add DD-028
  ([`b940ef4`](https://github.com/pounde/FastTAK/commit/b940ef4bbe4a3342a2f1995626f7e1ee8aedec79))


## v0.7.1 (2026-03-30)

### Bug Fixes

- Update dev docker compose to expose Authentik port to host
  ([`2f354a4`](https://github.com/pounde/FastTAK/commit/2f354a4a3273525bf32585af74fbb986ea36809c))


## v0.7.0 (2026-03-30)

### Bug Fixes

- Just dev-up {service} now rebuilds selected service
  ([`cbb1298`](https://github.com/pounde/FastTAK/commit/cbb1298369bd974f344986af072db68aa1dfdf27))

### Features

- **dashboard**: Add /users page route, nav link, and x-cloak CSS
  ([`0084af0`](https://github.com/pounde/FastTAK/commit/0084af0caaa3142e5fba042bbdc3849e269bf749))

- **dashboard**: Add user list with search, pagination, and full management page
  ([`0084af0`](https://github.com/pounde/FastTAK/commit/0084af0caaa3142e5fba042bbdc3849e269bf749))


## v0.6.1 (2026-03-30)

### Bug Fixes

- Correct enrollment URL
  ([`31e1d94`](https://github.com/pounde/FastTAK/commit/31e1d9435e6a88c37bf26ec404139de9ad440bc1))


## v0.6.0 (2026-03-30)

### Chores

- Block direct commits to main, exclude test override from YAML check
  ([`f4eed81`](https://github.com/pounde/FastTAK/commit/f4eed81b306982a1d1a3b53483237462e2bef77f))

### Documentation

- Add CONTRIBUTING.md and update decisions.md re: production hardening
  ([`27f8409`](https://github.com/pounde/FastTAK/commit/27f84097ebb9ec942d4239c85e95b099b626411e))

- Add DD-023 (production-first compose) and DD-024 (test port
  ([`27f8409`](https://github.com/pounde/FastTAK/commit/27f84097ebb9ec942d4239c85e95b099b626411e))

### Features

- Add dev compose override with direct-access ports
  ([`87514f6`](https://github.com/pounde/FastTAK/commit/87514f63295dcaad59852e783606522588b9af42))

- Add just recipes for production, dev, and help
  ([`27adac8`](https://github.com/pounde/FastTAK/commit/27adac82ba7b343258e46ff2305567a4106d2bcf))

- Add test compose override with +10000 port offset
  ([`3b1e715`](https://github.com/pounde/FastTAK/commit/3b1e715812ab727639eb039e6301cac6ff45bde1))

- Harden base compose — remove direct-access and admin API ports
  ([`33fec29`](https://github.com/pounde/FastTAK/commit/33fec2932256dd8acdf4fccf11f169bb0b67ed05))

- Use test compose override for port isolation in integration tests
  ([`b97fad5`](https://github.com/pounde/FastTAK/commit/b97fad55e1fc8185b3b298105831d3c863ce85f1))


## v0.5.0 (2026-03-29)

### Features

- Autovacuum tuning and direct database connection ([#9](https://github.com/pounde/FastTAK/pull/9),
  [`bdf1cac`](https://github.com/pounde/FastTAK/commit/bdf1cacec21d53c4e0f8a96df2c35313c6e724e0))

- Configurable data retention and .env additions ([#9](https://github.com/pounde/FastTAK/pull/9),
  [`bdf1cac`](https://github.com/pounde/FastTAK/commit/bdf1cacec21d53c4e0f8a96df2c35313c6e724e0))

- Data retention, autovacuum tuning, and health monitoring refactor
  ([#9](https://github.com/pounde/FastTAK/pull/9),
  [`bdf1cac`](https://github.com/pounde/FastTAK/commit/bdf1cacec21d53c4e0f8a96df2c35313c6e724e0))

- Health monitoring architecture refactor ([#9](https://github.com/pounde/FastTAK/pull/9),
  [`bdf1cac`](https://github.com/pounde/FastTAK/commit/bdf1cacec21d53c4e0f8a96df2c35313c6e724e0))


## v0.4.0 (2026-03-27)

### Chores

- Add unit tests to pre-commit hook, style fixes
  ([`3a7f3fe`](https://github.com/pounde/FastTAK/commit/3a7f3fef84f8bdee168426ce34ff0306d8779da4))

### Features

- **users**: Add user management API
  ([`59dce17`](https://github.com/pounde/FastTAK/commit/59dce175e478f3e366bae2ad9c98d99695ed4ed0))

- **users**: Isolated integration tests and setup.sh -d flag
  ([`e7877f1`](https://github.com/pounde/FastTAK/commit/e7877f1c99072ea678d17309a30efb8ddc0434f1))


## v0.3.0 (2026-03-27)

### Bug Fixes

- Add curl to monitor container for debugging and integration tests
  ([`1a15a33`](https://github.com/pounde/FastTAK/commit/1a15a33292b621459d9989a3e65a6a4fc5f0439e))

- Cert registration retry, upgrade-path generation, and review fixes
  ([`10413a4`](https://github.com/pounde/FastTAK/commit/10413a4c3e8c5d85ff10ed1f8ae530dfe583a6e3))

- Handle cd failure in register-api-cert script
  ([`0b389d5`](https://github.com/pounde/FastTAK/commit/0b389d5f0eb94c5748d9a16147ec9169a451096d))

- Improve cert registration wait loop and HTTP code parsing
  ([`a62b4f2`](https://github.com/pounde/FastTAK/commit/a62b4f2426a00ee5a3a72aaa5356575b3defb5bb))

- Prevent LDAP startup race with health check and depends_on
  ([`c3c5baf`](https://github.com/pounde/FastTAK/commit/c3c5bafd33c31ea45fd7f4dd5a23c833a418a4a0))

- Wait for cert registration before testing and add curl to monitor
  ([`58b6735`](https://github.com/pounde/FastTAK/commit/58b6735421ea8a998ad7f2d197a61e91a1e92018))

### Chores

- **ci**: Bump actions to Node.js 24 versions
  ([`acaad4a`](https://github.com/pounde/FastTAK/commit/acaad4a449f2f704d68815065ca6221a8e5acba3))

### Features

- Add svc_fasttakapi service account, rename nodered to svc_nodered
  ([`b85040d`](https://github.com/pounde/FastTAK/commit/b85040dd9a0c191d338203471156dcf4a17adabd))

- Register API service cert on TAK Server startup
  ([`719a902`](https://github.com/pounde/FastTAK/commit/719a902a319b98dad02978c09c4cbb922ecfc243))

### Refactoring

- Rename nodered cert to svc_nodered
  ([`bd2dc48`](https://github.com/pounde/FastTAK/commit/bd2dc48174bbc87bce03694864ac2ce1048b558f))

- Rename service account certs to svc_ convention
  ([`f658f2e`](https://github.com/pounde/FastTAK/commit/f658f2e9e5b202567c224823199fdbe5576c634e))

### Testing

- Add service account cert and passwordless auth integration tests
  ([`4c4653c`](https://github.com/pounde/FastTAK/commit/4c4653c8f82e9dc821e03a388e853f2de7dcf6d7))


## v0.2.0 (2026-03-25)

### Documentation

- Add Node-RED guide with USGS earthquake tutorial
  ([`adb4147`](https://github.com/pounde/FastTAK/commit/adb4147ed1aa68143da8c3c72dbda0e17f8d827a))

### Features

- **nodered**: Bake TLS config and GeoJSON-to-CoT guide into container
  ([`f887beb`](https://github.com/pounde/FastTAK/commit/f887beb90ef10de38a4807f24512ccaf11333306))


## v0.1.1 (2026-03-24)

### Bug Fixes

- Correct semantic-release config and bump upload-pages-artifact
  ([`5dc9f01`](https://github.com/pounde/FastTAK/commit/5dc9f012c5d4062f02e3516ab6fe99f31ed50614))


## v0.1.0 (2026-03-24)

- Initial Release

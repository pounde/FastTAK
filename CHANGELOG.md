# CHANGELOG

<!-- version list -->

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

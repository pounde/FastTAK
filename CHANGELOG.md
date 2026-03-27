# CHANGELOG

<!-- version list -->

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

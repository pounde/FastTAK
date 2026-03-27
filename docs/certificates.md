# FastTAK Certificate Guide

## The Big Picture

TAK doesn't use passwords to connect devices. Instead, every device gets a **certificate file** (`.p12`) that acts like a digital ID badge. When your phone (running ATAK or iTAK) connects to TAK Server, both sides show their ID badges to each other and verify they were issued by the same authority. If the badges check out, the connection is established.

This is called **mutual TLS** — both the server and the client prove their identity. It's more secure than passwords because there's nothing to guess, phish, or brute-force.

### Why not just use passwords?

TAK is designed for field operations where you might not have reliable connectivity. Certificate-based auth works offline — once a device has its cert, it can connect without needing to reach an authentication server. The cert *is* the credential.

### What's in a .p12 file?

A `.p12` file is a bundle containing:
- The device's identity certificate (who they are)
- The device's private key (proves they own the certificate)
- The CA certificate chain (so the device knows who to trust)

All protected by a password. The default password for TAK certs is `atakatak` — this is a well-known convention across the TAK ecosystem, not a secret. It just prevents accidental installation.

## How FastTAK Manages Certificates

FastTAK generates its own **certificate authority (CA)** on first boot. Think of the CA as the office that issues ID badges — every cert it creates is automatically trusted by TAK Server.

### The trust chain

```
Root CA (root-ca.pem)
  └── Intermediate CA (ca.pem)  ← issues all certs below
        ├── Server cert (takserver.pem)  ← TAK Server's identity
        ├── Service certs (svc_*.p12)     ← internal service accounts
        └── Client certs (alice.p12)     ← one per user/device
```

**Root CA** is the top-level authority. Created once, lasts ~10 years. Think of it as the organization's seal.

**Intermediate CA** is a delegated authority signed by the Root CA. It does the actual work of issuing certs. If it's ever compromised, you can replace it without invalidating the Root CA.

**Server cert** proves TAK Server is who it claims to be. FastTAK automatically creates one matching your FQDN.

**Client certs** prove a user/device's identity. One per person or device.

### Two ways to get a cert onto a device

**1. QR Code (recommended)** — Create a user in TAK Portal, click the QR button, user scans with ATAK/iTAK. The device connects to TAK Server, receives its cert, and auto-configures. No file transfers needed.

**2. Manual** — Generate a cert with `./certs.sh`, transfer the `.p12` file to the device (email, USB, shared drive), import it into the TAK app. The user enters the cert password (`atakatak`) and configures the server connection manually.

## Common Tasks

### Create a client cert

```bash
./certs.sh create-client alice
./certs.sh download alice.p12
```

### Check CA expiry

```bash
./certs.sh ca-info
```

FastTAK's healthcheck also monitors cert expiry — TAK Server becomes `unhealthy` when any cert is within 30 days of expiring.

### Revoke a cert

```bash
./certs.sh revoke alice
```

### List all certs

```bash
./certs.sh list
```

### Full command reference

```bash
./certs.sh help
```

## Two Separate Certificate Systems

FastTAK runs **two independent certificate systems** that don't interact:

| System | What it secures | Who manages it | Where |
|--------|----------------|---------------|-------|
| **Caddy / Let's Encrypt** | Web browser HTTPS (admin UI, portal) | Automatic — Caddy handles everything | Caddy's internal storage |
| **TAK Server CA** | Device connections (ATAK, iTAK, WinTAK) | You, via `./certs.sh` or QR enrollment | `./tak/certs/files/` |

When you visit `https://takserver.example.com` in a browser, that's a Let's Encrypt cert (managed by Caddy). When ATAK connects to port 8089, that's a TAK CA cert (managed by FastTAK). They're completely separate.

## Key Files

All cert files live at `./tak/certs/files/` on the host (bind-mounted into containers). They survive `docker compose down` — only `down -v` with a manual `rm -rf tak/` removes them.

| File | What it is |
|------|-----------|
| `root-ca.pem` | Root CA public cert |
| `root-ca-do-not-share.key` | Root CA private key — protect this |
| `ca.pem` | Intermediate CA public cert |
| `ca-do-not-share.key` | Intermediate CA private key — protect this |
| `takserver.jks` | Server keystore (Java KeyStore format) |
| `truststore-root.jks` | Trusted CA store for verification |
| `ca-signing.jks` | CA keystore used for QR enrollment cert signing |
| `svc_fasttakapi.p12` | API service cert (monitor → TAK Server) |
| `svc_nodered.p12` | Node-RED service cert (automation → TAK Server) |
| `<name>.p12` | Per-user/device client cert |

### What to protect

The `.key` files are the crown jewels. Anyone with `ca-do-not-share.key` can issue certs that TAK Server will trust. The `./tak/certs/files/` directory should have restricted permissions in production.

The `.p12` files are sensitive too — they're user credentials. Distribute them securely.

## Certificate Expiry and Renewal

- **Root CA**: ~10 years. If this expires, every device needs a new cert.
- **Intermediate CA**: ~5 years. Can be rotated without disrupting existing clients.
- **Client/server certs**: ~1-3 years.

### Rotating the intermediate CA

1. Generate a new intermediate CA (signed by the same Root CA)
2. The old CA stays in the truststore — existing clients keep connecting
3. New certs are signed by the new CA
4. Users re-enroll at their convenience
5. After everyone has re-enrolled, revoke the old CA

### What happens when a cert expires?

The device can't connect. Generate a new cert and deliver it to the user (QR code or manual transfer). No server restart needed.

## Compatibility Notes

FastTAK checks all `.p12` files on startup and re-exports any using legacy ciphers with modern AES-256-CBC encryption. TAK Server's upstream cert tools use RC2-40 which modern OpenSSL 3.x rejects — FastTAK handles this transparently so certs work with CloudTAK, modern Linux, and any other OpenSSL 3.x tool out of the box.

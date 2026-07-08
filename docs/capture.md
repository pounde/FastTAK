# Traffic Capture

FastTAK can run an [mitmproxy](https://mitmproxy.org/) sidecar in front of TAK
Server that decrypts and records the traffic flowing between a TAK client and
the server. The recordings land on disk as mitmproxy `.flow` files you can
inspect, replay, or keep as test fixtures.

## What this is — and isn't

TAK Server has many undocumented quirks: mission-sync REST shapes, CoT
streaming framing, error envelopes, and behaviors that aren't specified
canonically anywhere. Capture lets you **document the protocol from ground
truth** — point a known-good client (e.g. WinTAK) at a real TAK Server through
the proxy, drive a workflow, and keep the decrypted exchange.

The same `.flow` files double as **deterministic test fixtures**: mitmproxy's
`--server-replay` mode turns a recording into a fake server, so a client under
test can run against recorded responses with no TAK Server (or FastTAK) booted.

Capture is a **local development tool** for observing **one client at a time**.
It is not production monitoring, and it is not a way to run a live multi-client
picture — clients connected through the proxy can't see each other (see
"Clients can't see each other" below).

!!! danger "Captured flows contain real auth material"

    A `.flow` records the decrypted TLS session, including client certificates
    exchanged in the handshake and any session tokens. `captures/` and
    `capture/` are gitignored and **must stay that way** — never commit a flow
    file or a promoted fixture. Do not capture PII or live operational data;
    capture is for protocol understanding and fixtures only.

## How it works

`just up --capture` layers `docker-compose.capture.yml` onto the stack. That
adds an `mitmdump` reverse proxy (`tak-mitm`) which:

- Binds host ports **8443** (HTTPS / Marti API) and **8089** (CoT stream), the
  ports TAK Server would otherwise bind directly.
- Reverse-proxies each to `tak-server` on the Docker network, decrypting in the
  middle.
- Presents **TAK Server's own certificate** to connecting clients, so no client
  trust changes are needed — a normal data package connects with no TLS errors.
- Presents a TAK-issued client certificate (`mitm-proxy`, minted at bring-up)
  **upstream** to TAK Server, which is mTLS.
- Writes a rolling capture to `./captures/current.flow`.

Port **8446** (cert enrollment / admin) is **not** proxied — it stays a direct
`tak-server` binding.

!!! warning "Clients can't see each other (identity is flattened)"

    mitmproxy does not request a client certificate from the connecting client,
    so it authenticates upstream to TAK Server as the single `mitm-proxy` user
    for **every** connection. Two consequences:

    - **Captured identity is flat.** Request/response **shapes** are ground
      truth, but the acting user — and any per-user authorization in 8443 Marti
      responses — is `mitm-proxy`, not the real client. Keep that in mind when
      reading a flow or building a fixture that depends on identity.
    - **Multiple clients won't see each other.** Because every client presents
      as `mitm-proxy`, TAK Server can't route situational awareness between
      them. Each client connects fine and its CoT still reaches the database,
      but positions/markers are not fanned out to the other clients. You'll see
      this in the TAK Server messaging log:

        ```
        ERROR c.b.m.s.DistributedSubscriptionManager
              sendLatestReachableSA : User lookup failed for mitm-proxy
        ```

    Capture is for recording a **single** client's exchange with the server.
    For normal multi-client operation (clients seeing each other), run **without**
    `--capture` (`just down` then `just up`) so each client connects with its own
    cert identity. Preserving per-client identity through the proxy would require
    forwarding each client's cert upstream — a non-goal for this passive-capture
    tool.

## Quickstart

1. Make sure `SERVER_ADDRESS` in `.env` is an IP or hostname the TAK client can
   actually reach (a LAN IP or Tailscale name — not `localhost`). The proxy
   presents TAK's certificate for `SERVER_ADDRESS`, and the generated data
   package tells the client to connect there.

2. Bring the stack up with capture enabled:

   ```bash
   just up --capture
   ```

3. Generate a data package for a user from the FastTAK portal and import it into
   a TAK client (WinTAK, ATAK, iTAK). The client connects through mitmproxy with
   no TLS errors.

4. Drive the workflow you want to document. Decrypted flows accumulate in
   `./captures/current.flow` — inspect them as described below.

## Saving a named fixture

`./captures/current.flow` is a **rolling** file — it is overwritten the next
time `tak-mitm` restarts. Promote anything worth keeping before that happens:

```bash
cp ./captures/current.flow ./captures/mission-sync-wintak.flow
```

Promoted files stay gitignored (everything under `captures/` is). Move them out
of the repo entirely if you need to share them.

## Inspecting a flow

```bash
mitmproxy -nr ./captures/mission-sync-wintak.flow   # terminal UI
mitmweb   -nr ./captures/mission-sync-wintak.flow   # browser UI
```

`-n` starts without listening; `-r` reads the flow file.

## Replaying a flow as a fixture

`--server-replay` makes the `.flow` _be_ the server — it answers matching
requests with the recorded responses. Point the client (or code) under test at
the replay listener; no TAK Server or FastTAK boot is needed:

```bash
mitmdump --server-replay ./captures/mission-sync-wintak.flow --listen-port 8443
```

## What's captured

| Port | Traffic                 | Captured?          |
| ---- | ----------------------- | ------------------ |
| 8443 | HTTPS / Marti API       | ✅                 |
| 8089 | CoT stream (mTLS)       | ✅                 |
| 8446 | Cert enrollment / admin | ❌ (direct to TAK) |

FastTAK does not currently expose TAK federation (ports 9000/9001). If
federation is enabled in the future, it will bypass the proxy unless the
capture overlay is extended to cover it.

## Tearing down

```bash
just down
```

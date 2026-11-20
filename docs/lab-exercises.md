# Lab Exercises (multi-container + WireGuard VPN)

A **lab exercise** is the third challenge type on the platform, alongside
`static` (downloadable file) and `docker` (single per-player container).

A lab is a **multi-container topology** described by a Docker Compose file. When
a player launches it, Scenario Management builds a **fully isolated instance**
and the player tunnels into it over **WireGuard**. Every instance gets its own
network, subnet, WireGuard server and keypair — two players (or two instances of
the same player) never share any network resource.

---

## Architecture (what happens on launch)

When a player starts a lab, **Scenario Management** does all of the following —
instance lifecycle lives entirely in Scenario Management; Orchestration only
sequences the call:

1. **Allocates a unique subnet** from a managed pool — `10.10.X.0/24`, where `X`
   is the lowest free index across all live instances (1–254).
2. **Creates a dedicated isolated bridge network** with that subnet.
3. **Starts every service** in the compose file on that network (services
   resolve each other by their compose service name).
4. **Injects a WireGuard server container** onto the same network, with:
   - a freshly generated **server keypair** (X25519),
   - a freshly generated **player keypair**,
   - a **unique host UDP port** (auto-assigned, never shared),
   - NAT/forwarding so player traffic reaches the lab subnet.
5. **Produces a `.conf`** WireGuard client file scoped to this exact
   player-instance pair.

On teardown (player, instructor, or Orchestration), **all** containers
(including WireGuard) are stopped, the network is removed, and the subnet
returns to the pool.

```
player's WireGuard client
        │  UDP  (Endpoint = host:UDP_PORT)
        ▼
┌─────────────────────────────────────────────┐
│ isolated network  10.10.X.0/24               │
│                                              │
│  ┌────────────┐   ┌──────────┐   ┌────────┐  │
│  │ wireguard  │──▶│ service1 │   │service2│  │
│  │  (NAT)     │   └──────────┘   └────────┘  │
│  └────────────┘     (reachable over the VPN) │
└─────────────────────────────────────────────┘
```

---

## For admins / instructors — creating a lab

1. Go to **Challenges → Create**.
2. Set **Type = `lab`**.
3. Fill in title, category, difficulty, points, flag, etc. (same as other types).
4. Upload your **Docker Compose file** as the file field (`.yml` / `.yaml`).
5. (Optional) untick **Visible to players** to keep it hidden until you're ready
   — you can flip visibility any time from the challenge card (**🙈 Hide / 👁
   Show**, admin/instructor only).

### Compose format & constraints

The compose file is stored verbatim and a **safe subset** is run at launch:

| Supported per service | Notes |
|---|---|
| `image:` | **Required.** The image must be pullable or already on the host. |
| `environment:` | Map or `KEY=value` list. |
| `command:` | Overrides the image command. |

**Not supported (ignored):** `build:` (pre-built images only), `ports:` /
`networks:` (the platform manages the isolated network and host ports), `volumes:`,
`depends_on:` (services start in file order). Max **12 services** per lab.

> Services are hardened: all Linux capabilities dropped, `no-new-privileges`,
> 512 MB / 1 CPU / 512 PIDs each. If a service needs more, it isn't suitable as
> a lab service.

### Example: a 2-container lab

```yaml
services:
  web:
    image: traefik/whoami
    environment:
      WHOAMI_NAME: lab-web
  cache:
    image: redis:7-alpine
```

After launch, the player can reach `web` on port 80 and `cache` on 6379 **inside
the lab subnet**, over the VPN. Put your flag wherever you like inside the
topology — the player finds it and submits it on the challenge card like any
other challenge.

---

## For players — connecting to a lab

You need a WireGuard client: <https://www.wireguard.com/install/>.

1. On the lab challenge card, click **Start lab**. Wait for it to go *running*
   (the first launch on a fresh platform takes a little longer while the
   WireGuard image builds and service images pull).
2. Click **⤓ Download VPN config** — you get a `sor-lab-XXXX.conf` file.
3. Import it into your WireGuard client:
   - **Desktop app:** *Add Tunnel → Import from file*.
   - **CLI:** `sudo wg-quick up ./sor-lab-XXXX.conf`
4. **Activate** the tunnel. You're now on the lab network.
5. Reach the services by their lab IPs in `10.10.X.0/24` (the card shows your
   subnet). For example, browse to `http://10.10.X.2` or scan the subnet.
6. Find the flag inside the lab and submit it on the card.
7. When you're done, **deactivate** the tunnel and click **Stop lab**. Your
   network and subnet are torn down immediately.

> Only the routes for *your* lab subnet are sent through the tunnel, so your
> normal internet access is unaffected.

### Example `.conf` (what you download)

```ini
[Interface]
PrivateKey = <your unique private key>
Address = 10.13.X.2/32

[Peer]
PublicKey = <this instance's server public key>
Endpoint = <host>:<unique UDP port>
AllowedIPs = 10.10.X.0/24
PersistentKeepalive = 25
```

---

## Isolation guarantee

Each instance has its **own** Docker network, **own** `/24` subnet, **own**
WireGuard server, and **own** player keypair. The subnet index is allocated from
the set of live instances, and the WireGuard host port is unique per container.
No two simultaneous instances share a network, subnet, server, port or key.

## Lifecycle / limits

- One running instance per player per lab (start again after stopping).
- Instances have a TTL (default 180 min) and can be stopped by the player, an
  instructor (Challenges → Live sessions → **Stop instance**), or Orchestration.
- The `Endpoint` host in the `.conf` comes from the platform's
  `PLAYER_SESSION_HOSTNAME` setting — set it to the host's reachable address (LAN
  IP / public hostname) so remote players can connect.

## Requirements

- The host kernel must support WireGuard (built in to modern Linux / WSL2).
- The Scenario Management container needs the Docker socket (already mounted).

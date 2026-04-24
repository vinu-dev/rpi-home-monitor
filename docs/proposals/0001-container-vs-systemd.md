# Proposal 0001: Container Strategy — Add-on Apps via Docker on Yocto

**Status:** Draft — decision pending  
**Date:** 2026-04-24  
**Author:** Vinu  
**Relates to:** ADR-0001 (custom Yocto distro), ADR-0006 (modular monolith), ADR-0008 (swupdate A/B),
ADR-0010 (LUKS), ADR-0020 (dual-transport OTA)

---

## 1. Context & Goals

### The pivot

rpi-home-monitor was designed as a private appliance: one operator, known hardware, full control
over the image. That framing drove the architecture — custom Yocto distro, swupdate A/B rollback,
mTLS pairing, LUKS data partition, no package manager at runtime. Those were correct decisions
for an appliance and they remain correct. Nothing in this proposal changes them.

What is changing is the project's target audience. An open-source community release creates two
new requirements that the current architecture does not address:

1. **Arbitrary add-on apps.** Users want to run Claude AI automation, a RADIUS server, a Zigbee
   coordinator, or things nobody has thought of yet — on the same server Pi — without modifying
   the core stack, without waiting for an upstream PR, and without a Yocto toolchain.

2. **No rebuild required.** Adding `meta-home-monitor` recipes for every community idea scales
   to the team's ideas, not the community's. The feedback loop (write recipe → run bitbake →
   wait 4-6 hours → test) is incompatible with the pace of open-source contribution.

Everything else — the core monitor Flask app, nginx, mediamtx, the camera agent, OTA, pairing,
auth — stays exactly as it is, on systemd, on the Yocto image.

### What this proposal decides

Whether and how to add Docker to the Yocto server image as a runtime for user-contributed
add-on apps, while leaving the core monitor stack untouched.

### Non-goals

- Containerising the monitor Flask app, nginx, or mediamtx. They stay on systemd.
- Containerising camera agents. See Section 2 for the numbers; it is not physically possible.
- Replacing swupdate A/B with `docker compose pull`. OTA stays on swupdate.
- Removing the privileged-helper refactor from the long-term backlog — it remains correct
  work for security hardening — but it is no longer a prerequisite for this proposal.

---

## 2. The Camera Constraint (Settled)

The Pi Zero 2W camera has **176 MB usable RAM** (512 MB total, 256 MB reserved as CMA for the
ISP/GPU pipeline). Docker daemon baseline is 35-50 MB. Camera-streamer under streaming is 35-40 MB.
The math doesn't work. Cameras stay on Yocto + systemd, period. This section will not be
revisited.

---

## 3. Options

### Option A: Stay entirely on current stack

New apps get Yocto recipes in `meta-home-monitor`. Users build or wait for a release image.

**Why this is wrong for the open-source goal:** BitBake is not a reasonable ask for a community
user. Building the image requires 50+ GB of disk, 8+ GB RAM, and 4-6 hours. The feedback loop
for a new recipe is hours. The community will not use this — they will use Home Assistant
instead. Option A is the right answer for the camera (it is an appliance) and the wrong answer
for server extensibility.

### Option B: Fully containerise the server stack

Monitor, nginx, mediamtx, and add-ons all in Docker. docker-compose as the user-facing install.

**Why this is wrong:** The monitor makes direct privileged subprocess calls (swupdate, systemctl,
timedatectl, hostnamectl, mount, tailscale) that require `--privileged` in a container, or a
2-3 week refactor before they work without it. The OTA story degrades — swupdate A/B rollback
goes away. The Yocto security model (no package manager, minimal attack surface, LUKS) is
discarded. This trades the things the current stack is good at for a user convenience that only
matters for the core install path, not for add-on apps.

### Option C: Yocto core + Docker for add-ons only (Recommended)

Add Docker (or Podman) to the Yocto server image exclusively as a runtime for user-contributed
add-on apps. The monitor, nginx, mediamtx, and OTA remain on systemd. Add-ons live under
`/data/stacks/` and are managed by a lightweight systemd unit. The core image is never
modified by running an add-on.

**Why this is right:** It solves the actual problem (community extensibility) without creating
new problems (OTA regression, refactoring the privileged subprocess calls, losing the security
model). The monitor stays in its well-tested, well-understood position. Docker becomes
infrastructure, not architecture.

---

## 4. Option C in Detail

### 4.1 Overall model

```
┌─────────────────────────────────────────────────────────────────┐
│  SERVER  (RPi 4B / Pi 5 — Yocto "Home Monitor OS")             │
│                                                                 │
│  systemd — unchanged core stack                                 │
│    monitor.service     — Flask app (auth, API, dashboard)       │
│    nginx.service       — TLS termination, static files          │
│    mediamtx.service    — RTSP broker                            │
│    swupdate.service    — OTA A/B update daemon                  │
│    tailscaled.service  — VPN                                    │
│                                                                 │
│  docker (new, on /data volume — survives OTA)                   │
│    /data/stacks/claude-automation/docker-compose.yml            │
│    /data/stacks/freeradius/docker-compose.yml                   │
│    /data/stacks/<anything>/docker-compose.yml                   │
│                                                                 │
│  hm-stacks@.service   — manages each /data/stacks/<name> stack │
│                                                                 │
│  /data/docker/        — Docker data root (images, volumes)      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  CAMERA  (Pi Zero 2W — Yocto "Home Monitor OS")                │
│  Unchanged. systemd only. No Docker.                            │
└─────────────────────────────────────────────────────────────────┘
```

The core insight: Docker is a guest runtime, not the host. The Yocto image controls what
hardware-facing services run. Docker controls what user add-ons run. They operate in separate
namespaces of concern.

### 4.2 Adding Docker to the Yocto image

#### Layer

Add `meta-virtualization` from the OpenEmbedded layer index. It is maintained by the OE
community and ships current Docker CE and/or Podman builds for ARM.

```bitbake
# config/bblayers.conf  (add one line)
  ${YOCTODIR}/meta-virtualization \
```

#### Packages in the server image recipe

```bitbake
# meta-home-monitor/recipes-monitor/images/home-monitor-server.bb
IMAGE_INSTALL:append = " \
    docker-ce \
    docker-ce-cli \
    containerd \
    docker-compose-plugin \
"

# Required kernel features — most are already enabled by meta-raspberrypi
# for the Pi 4B kernel. Verify with: bitbake -e virtual/kernel | grep KERNEL_FEATURES
DISTRO_FEATURES:append = " virtualization"
```

See Section 4.2.1 for the Podman alternative (recommended).

#### Docker data root on `/data`

By default Docker stores images and volumes under `/var/lib/docker`, which is on the rootfs.
The rootfs is limited to 8 GB and, critically, is **replaced on every OTA update**. User's
container images would be lost on every system update. This is unacceptable.

Move Docker's data root to `/data/docker/`, which lives on the `/data` partition and survives
A/B root partition swaps:

```json
// /etc/docker/daemon.json  (Yocto recipe writes this file)
{
    "data-root": "/data/docker",
    "log-driver": "journald",
    "log-opts": {
        "tag": "docker/{{.Name}}"
    },
    "userland-proxy": false
}
```

The `data` partition is 39 GB on the current server SD card. Docker images are typically
50-500 MB each; a user running 3-4 add-on services can expect to use 1-3 GB. There is
ample space. (See Section 8 for open questions on storage limits.)

#### Kernel config

meta-raspberrypi already enables the namespaces and cgroup features needed (confirmed
from live hardware: cgroupv2 with cpuset, cpu, io, pids; all namespaces present in
`/proc/1/ns/`). No custom kernel config changes are expected. Verify by running
`check-config.sh` from the Moby project after the first build.

#### Rootfs size impact

```
meta-virtualization packages (docker-ce + containerd + compose plugin): ~60-80 MB compressed
Docker daemon binaries:                                                  ~30 MB on rootfs
Total rootfs size increase:                                              ~100-120 MB

Current server rootfs used:   499 MB
After Docker:                 ~615-620 MB
Rootfs partition size:        8 GB
Headroom remaining:           ~7.4 GB  ✓
```

The rootfs partition has more than enough room. The camera image is unaffected (Docker is
only in the server image recipe, not the zero2w recipe).

#### Build time impact

Adding `meta-virtualization` adds roughly 20-40 minutes to a clean build (fetching and
compiling containerd + runc + Docker daemon from source). Incremental builds that don't
touch Docker packages have no additional cost.

#### 4.2.1 Podman instead of Docker (preferred)

Podman is available via `meta-virtualization` and is arguably a better fit for this use case:

| | Docker | Podman |
|---|---|---|
| Daemon | Yes (root daemon, always running, ~30 MB RAM) | No daemon (daemonless) |
| Rootless | Possible but complex | Native, default |
| systemd integration | Manual | Native (`podman generate systemd`) |
| `docker-compose` compatibility | Native | `podman-compose` or compose v2 |
| meta-virtualization | ✓ | ✓ |
| Community familiarity | Higher | Lower, but growing |

A daemonless container runtime is meaningfully better on embedded hardware:
- No persistent daemon consuming 30-50 MB RAM when no add-ons are running.
- No Docker socket to secure (a compromised add-on cannot escape via `/var/run/docker.sock`).
- Systemd can start/stop containers directly without a daemon intermediary.

**Recommendation: use Podman.** Use `podman-compose` for docker-compose file compatibility
(the format is identical; only the runtime differs). The `hm-stacks@.service` unit invokes
`podman compose up -d` rather than `docker compose up -d`. Users write standard
`docker-compose.yml` files and they work unchanged.

If the community friction of Podman vs Docker is a concern, Docker CE can be offered as an
alternative in the Yocto recipe with a `PACKAGECONFIG` knob.

### 4.3 The `/data/stacks/` convention

#### Directory structure

```
/data/stacks/
├── claude-automation/
│   ├── docker-compose.yml    ← user-managed; defines the service
│   ├── .env                  ← secrets (ANTHROPIC_API_KEY, etc.)
│   └── data/                 ← optional: app-specific volumes
├── freeradius/
│   ├── docker-compose.yml
│   └── data/
│       └── users             ← RADIUS user database
└── my-custom-app/
    ├── docker-compose.yml
    └── data/
```

Rules:
- One compose file per stack directory.
- The stack name is the directory name.
- Stack-specific data lives under `<stack>/data/` (bind-mounted in the compose file).
- Secrets in `.env` (not committed to any repo). `.env.example` is provided with every
  official example stack.
- The monitor never reads or writes these directories. It can optionally enumerate them for
  a management UI (Phase 3), but never modifies compose files.

#### The `hm-stacks@.service` systemd template unit

A single parameterised systemd template unit manages every stack. The parameter is the
stack directory name.

```ini
# /usr/lib/systemd/system/hm-stacks@.service
# Written by Yocto recipe; survives OTA (it's on rootfs, but stacks are on /data)
[Unit]
Description=Home Monitor Add-on Stack: %i
After=network-online.target docker.service data.mount
Requires=data.mount
PartOf=hm-stacks.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/data/stacks/%i
ExecStart=/usr/bin/podman compose up -d --remove-orphans
ExecStop=/usr/bin/podman compose down
TimeoutStartSec=120
TimeoutStopSec=60
# Prevent a broken stack from blocking boot
FailureAction=none

[Install]
WantedBy=hm-stacks.target
```

```ini
# /usr/lib/systemd/system/hm-stacks.target
# Collective target: enable all stacks at once
[Unit]
Description=Home Monitor Add-on Stacks
After=hm-stacks@*.service
```

**Enabling a stack:**
```bash
# After creating /data/stacks/claude-automation/docker-compose.yml:
systemctl enable --now hm-stacks@claude-automation
```

**Disabling a stack:**
```bash
systemctl disable --now hm-stacks@claude-automation
```

**Starting/stopping without changing boot behaviour:**
```bash
systemctl start hm-stacks@freeradius
systemctl stop hm-stacks@freeradius
```

The unit template is part of the Yocto image (rootfs). It survives OTA because it is
standard systemd infrastructure, not user state. Stack enablement is tracked by systemd
symlinks in `/etc/systemd/system/` — these *are* on the rootfs and would be wiped by an
OTA swap. See Section 4.5 for how this is handled.

### 4.4 How add-on containers communicate with the monitor

Add-ons are third-party code. They should not have access to the monitor's internals, the
camera mTLS certificates, or any privileged host state. The communication surface must be
explicit and documented.

#### Option 1: HTTP webhook (recommended for most add-ons)

The monitor exposes (in Phase 3) a webhook registration API. Add-ons register a URL; the
monitor POSTs signed event payloads to it on motion events, camera state changes, etc.

```
add-on container ──────── POST /api/v1/webhooks/register ─────→ monitor (127.0.0.1:5000)
monitor          ──────── POST http://claude-automation:8080/event ─────→ add-on container
```

No special network configuration needed. Both services are on the same Docker bridge network
(`hm-addons`). The monitor is accessible on the host network at `127.0.0.1:5000`.

#### Option 2: Shared read-only volumes

For add-ons that need access to recordings or live clips (e.g., Claude automation analysing
motion clips):

```yaml
volumes:
  - /data/recordings:/data/recordings:ro    # read-only; no write access
  - /data/live:/data/live:ro                # live HLS segments; read-only
```

This is explicit in the compose file and visible to the operator. The monitor never grants
write access to its data directories to add-on containers.

#### Network model

```yaml
# In the base docker-compose network (created by the stacks framework):
networks:
  hm-addons:
    driver: bridge
    # monitor is accessible at host IP; add-ons communicate with each other on this bridge
```

Add-ons are NOT on the host network by default. They reach the monitor via the host's
loopback (`host-gateway` alias in compose) or via the host IP on the local LAN.
They do NOT have access to the Docker socket. They do NOT run `--privileged` by default.

If an add-on needs privileged access (e.g., a Zigbee coordinator needing `/dev/ttyACM0`),
that is explicit in its compose file and visible to the operator who chooses to install it.

#### What add-ons explicitly cannot do (by default)

- Access camera mTLS certificates (`/data/certs/`)
- Write to monitor config or databases (`/data/config/`, `cameras.db`, `monitor.db`)
- Call swupdate or systemctl (no access to the Docker socket or D-Bus socket)
- Access the monitor session secret (`/data/config/.secret_key`)

### 4.5 OTA survival: what happens when swupdate installs a new rootfs

This is the most important correctness question. An OTA swap replaces the active root
partition. The `/data` partition is untouched. Let's trace what survives and what doesn't:

```
A/B swap
├── /data/docker/                ← ON /data → SURVIVES  ✓ (all images, volumes)
├── /data/stacks/                ← ON /data → SURVIVES  ✓ (all compose files, add-on data)
├── /data/config/                ← ON /data → SURVIVES  ✓ (monitor config, secret key)
├── /data/certs/                 ← ON /data → SURVIVES  ✓ (mTLS certs)
├── /data/recordings/            ← ON /data → SURVIVES  ✓ (clips)
└── /etc/systemd/system/hm-stacks@*.service.d/  ← ON rootfs → WIPED ✗
    (systemctl enable symlinks also on rootfs)
```

**The problem:** When the operator has enabled `hm-stacks@claude-automation` via `systemctl
enable`, the symlink lives in `/etc/systemd/system/multi-user.target.wants/`. After OTA, that
symlink is gone. The stack's data survives; the stack's boot registration doesn't.

**The fix:** Track enabled stacks on `/data`, not in rootfs symlinks. Use a small helper
that runs once on each boot and re-enables whatever `/data/stacks/*/enabled` marker files
exist:

```bash
# /usr/lib/systemd/system/hm-stacks-restore.service
# Runs before hm-stacks.target; re-enables any stacks persisted on /data
[Unit]
Description=Restore Add-on Stack Registrations After OTA
After=data.mount
Before=hm-stacks.target
RequiresMountsFor=/data

[Service]
Type=oneshot
ExecStart=/usr/lib/home-monitor/restore-stacks.sh

[Install]
WantedBy=multi-user.target
```

```bash
#!/bin/sh
# /usr/lib/home-monitor/restore-stacks.sh
for marker in /data/stacks/*/enabled; do
    [ -f "$marker" ] || continue
    name=$(basename "$(dirname "$marker")")
    systemctl enable --no-reload "hm-stacks@${name}" 2>/dev/null || true
done
systemctl daemon-reload
```

The `enabled` marker file is created by `systemctl enable hm-stacks@<name>` via a
`ExecStartPost` drop-in, or written directly by the Phase 3 management UI.

With this approach:
1. OTA wipes rootfs → boot → `hm-stacks-restore.service` runs → re-enables all stacks
   that had `/data/stacks/<name>/enabled` → stacks start as normal.
2. Stack data on `/data/stacks/<name>/data/` was never touched.
3. Container images in `/data/docker/` were never touched.
4. The operator does not need to re-enable stacks after an update.

### 4.6 Security model

#### Container isolation (defaults)

All add-on containers run as non-root by default. The official example stacks enforce this:

```yaml
services:
  claude-automation:
    image: ghcr.io/vinu-dev/hm-claude-automation:latest
    user: "1000:1000"                    # non-root
    read_only: true                      # immutable container filesystem
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
```

User-written stacks are the user's own business. The documentation should make clear that
`--privileged` in a user stack is the user's own risk, not ours.

#### No Docker socket exposure

The monitor does not mount `/var/run/docker.sock` (or `/run/podman/podman.sock`). Add-on
containers cannot start, stop, or inspect other containers. A compromised add-on cannot
use the container runtime to escalate to host root.

#### Monitor API authentication for add-ons

Add-ons that call the monitor API (`/api/v1/`) need credentials. The Phase 3 management
feature should include per-add-on API tokens with limited scopes (e.g., `webhook:subscribe`,
`recordings:read`). For the initial release, add-ons can use a manually-created admin
session or a shared API token in their `.env`. Full per-app token scoping is a future feature.

#### Secrets management

Secrets (API keys, passwords) live in `/data/stacks/<name>/.env`. This file is on the LUKS-
encrypted `/data` partition in production (ADR-0010). It is never included in the Yocto image.
The documentation must make clear that `.env` files should not be committed to personal
repos where they're used.

---

## 5. Example Add-on Stacks

### 5.1 Claude automation (AI motion event analysis)

Subscribes to motion events from the monitor webhook API, fetches the corresponding clip
thumbnail, calls Claude claude-haiku-4-5 for a natural-language description, and dispatches a
push notification.

```yaml
# /data/stacks/claude-automation/docker-compose.yml
services:
  claude-automation:
    image: ghcr.io/vinu-dev/hm-claude-automation:latest
    restart: unless-stopped
    user: "1000:1000"
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    environment:
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
      HM_SERVER_URL: "http://${HM_SERVER_IP:-192.168.1.245}"
      HM_API_TOKEN: "${HM_API_TOKEN}"
      NOTIFY_WEBHOOK_URL: "${NOTIFY_WEBHOOK_URL}"   # ntfy.sh, Slack, etc.
    volumes:
      - /data/recordings:/data/recordings:ro
      - /data/stacks/claude-automation/data:/app/state
    extra_hosts:
      - "host-gateway:host-gateway"
    networks:
      - hm-addons

networks:
  hm-addons:
    external: true
```

```bash
# /data/stacks/claude-automation/.env.example
ANTHROPIC_API_KEY=sk-ant-...
HM_SERVER_IP=192.168.1.245
HM_API_TOKEN=                   # generate in Settings → API Tokens
NOTIFY_WEBHOOK_URL=https://ntfy.sh/my-topic
```

**What this does at runtime:**
1. On start, registers with the monitor: `POST /api/v1/webhooks/register` with `{event: "motion", url: "http://claude-automation:8080/event"}`.
2. When a motion event fires, the monitor POSTs to the add-on.
3. The add-on fetches the thumbnail from `/data/recordings` (read-only bind mount).
4. Calls `anthropic.messages.create(model="claude-haiku-4-5", ...)` with the thumbnail.
5. POSTs the enriched notification to `NOTIFY_WEBHOOK_URL`.

### 5.2 FreeRADIUS server

Provides RADIUS authentication for a WiFi network (WPA2-Enterprise, 802.1X), using the home
monitor's local CA for EAP-TLS.

```yaml
# /data/stacks/freeradius/docker-compose.yml
services:
  freeradius:
    image: freeradius/freeradius-server:3.2-alpine
    restart: unless-stopped
    ports:
      - "1812:1812/udp"      # RADIUS authentication
      - "1813:1813/udp"      # RADIUS accounting
    volumes:
      - /data/stacks/freeradius/data/users:/etc/freeradius/3.0/users:ro
      - /data/stacks/freeradius/data/clients.conf:/etc/freeradius/3.0/clients.conf:ro
      - /data/certs/ca.crt:/etc/freeradius/3.0/certs/ca.crt:ro   # reuse home monitor CA
      - /data/certs/server.crt:/etc/freeradius/3.0/certs/server.crt:ro
      - /data/certs/server.key:/etc/freeradius/3.0/certs/server.key:ro
    networks:
      - hm-addons

networks:
  hm-addons:
    external: true
```

The home monitor CA (already in `/data/certs/`) is exposed read-only for EAP-TLS certificate
chain validation. No write access to monitor data. RADIUS users are managed in
`/data/stacks/freeradius/data/users` — entirely independent of the monitor's user store unless
a future integration feature is added.

### 5.3 Generic placeholder (user-written app)

A skeleton that shows the minimal viable add-on:

```yaml
# /data/stacks/my-app/docker-compose.yml
services:
  my-app:
    image: my-registry/my-app:latest     # or build: . for local development
    restart: unless-stopped
    user: "1000:1000"
    security_opt:
      - no-new-privileges:true
    env_file: .env
    volumes:
      - ./data:/app/data                 # relative to the stack directory
    networks:
      - hm-addons

networks:
  hm-addons:
    external: true
```

The `hm-addons` bridge network is created once during setup (Phase 2) and is always available
to any stack that declares it as external. Add-ons on this network can reach each other by
service name and can reach the monitor at the host's LAN IP.

---

## 6. Update and Upgrade Flow

### Updating an add-on

```bash
# Pull the latest image and restart (no downtime to the core monitor):
cd /data/stacks/claude-automation
podman compose pull
podman compose up -d

# Or via systemd (which calls compose under the hood):
systemctl restart hm-stacks@claude-automation
```

Add-on data in `./data/` is never touched. Container state that is not in a volume is
discarded (which is the correct behaviour — containers should be stateless outside their volumes).

### Upgrading the OS image via OTA

1. User uploads `.swu` bundle via the monitor dashboard (unchanged flow).
2. swupdate writes to the inactive root partition and sets the boot flag.
3. System reboots into new rootfs.
4. `hm-stacks-restore.service` runs, re-enables stacks persisted on `/data`.
5. `hm-stacks.target` starts, which starts `hm-stacks@<name>` for each enabled stack.
6. `podman compose up -d` runs for each stack. Images are pulled from `/data/docker/` (already present). No internet required post-update.
7. Add-on containers are running again within ~30-60 seconds of boot.

The operator never needs to re-configure their add-ons after an OS update.

### Updating the Docker/Podman runtime itself

Docker/Podman lives on the rootfs (installed by the Yocto recipe). It is updated as part of
the OS `.swu` bundle — the same swupdate path as everything else. This means:

- The container runtime version is always in sync with the OS.
- Users cannot accidentally run a Docker version that conflicts with the kernel version baked
  into the image.
- Security patches to the container runtime are delivered via the normal OTA channel, not
  via `apt upgrade`.

This is strictly better than having Docker installed separately from the OS image.

---

## 7. Open-Source Distribution

The core monitor is still a Yocto image that users flash. That is correct for camera and server
alike. The "easy install for strangers" story is:

1. **Server**: Flash the pre-built server `.img` from GitHub Releases onto an SD card using
   Raspberry Pi Imager. The image is published by CI on every tagged release.
   First boot takes ~2 minutes (resize2fs, first-boot provisioning).
   Open `https://raspberrypi.local` (or the server's IP) and complete the setup wizard.
   Docker/Podman is already installed and the `hm-addons` network is already created.
   To add Claude automation: download the example stack, put it in `/data/stacks/claude-automation/`,
   fill in `.env`, run `systemctl enable --now hm-stacks@claude-automation`. Done.

2. **Camera**: Flash the pre-built Zero 2W `.img` from GitHub Releases. Boot. The camera
   appears in the monitor's discovery feed. Pair it from the dashboard. Done.

3. **Add-on stacks**: Published as a GitHub repository of example stacks
   (`vinu-dev/rpi-hm-stacks`). Each directory is a ready-to-use add-on with a `.env.example`
   and a `README.md`. Users copy the directory to `/data/stacks/<name>/` and enable it.
   No build. No compilation. Pull a pre-built container image. The barrier is: "do you know
   how to copy a directory and fill in an API key?" — yes.

The remaining friction is that users must flash an `.img` file rather than running
`docker compose up`. That friction exists for every purpose-built appliance OS (Raspberry Pi
OS itself, Home Assistant OS, etc.) and is accepted as normal by the home-lab community.

---

## 8. Migration Plan

### Phase 0: Publish pre-built images from CI (enables community testing)

**Scope:** Add a GitHub Actions workflow that builds the Yocto server image and Zero 2W camera
image on every release tag, and uploads `.img.xz` artifacts to GitHub Releases.

This is the first thing the open-source community needs. Without downloadable images, nobody
can try the project without a build environment.

**Effort:** 1 week (self-hosted build runner setup + workflow + release script)  
**Risk:** Low — build infrastructure change only; no code changes.  
**If skipped:** Open-source release has no install path for non-developers.

### Phase 1: Add Docker/Podman to the server Yocto image

**Scope:**
- Add `meta-virtualization` to `config/bblayers.conf`.
- Add `docker-ce` + `containerd` + `docker-compose-plugin` (or `podman` + `podman-compose`)
  to the server image recipe.
- Add `daemon.json` recipe to point data root at `/data/docker/`.
- Add `hm-stacks.target` and `hm-stacks@.service` template unit to the image.
- Add `hm-stacks-restore.service` and `restore-stacks.sh` to the image.
- Create the `hm-addons` Docker bridge network at first-boot (via existing
  `camera-hotspot.sh` or a new first-boot hook).
- Test: build, flash, enable a test stack, do a simulated OTA (swupdate with a new `.swu`
  built from the same commit), confirm stacks restart correctly.

**Does NOT touch:** Monitor Flask app, nginx config, mediamtx, OTA flow, camera image.

**Effort:** 2 weeks (Yocto layer work + service authoring + OTA survival test)  
**Risk:** Medium — Yocto layer integration has occasional dependency conflicts; OTA survival
path needs hardware test with real `.swu` cycle. The kernel cgroup/namespace config is
already correct on the Pi 4B (verified from live hardware); no kernel changes expected.  
**If skipped:** No containers. Entire proposal blocked.

### Phase 2: Example stacks and documentation

**Scope:**
- Publish `vinu-dev/rpi-hm-stacks` repository with:
  - `claude-automation/` — complete working example (see §5.1)
  - `freeradius/` — complete working example (see §5.2)
  - `_template/` — skeleton for user-written stacks
- Write `docs/getting-started/add-on-stacks.md` covering:
  - Installing an example stack end-to-end
  - Writing your own stack
  - Volume conventions, the `hm-addons` network, how to reach the monitor API
  - OTA survival contract

**Effort:** 1 week  
**Risk:** Low.  
**If skipped:** Users have Docker installed but no documentation on how to use it.

### Phase 3: Stack management in the monitor UI (optional, improves UX)

**Scope:** A "Add-ons" tab in the Settings dashboard. Lists stacks found in `/data/stacks/`,
shows status (running/stopped/not installed), allows enable/disable/restart from the UI.
Does NOT allow creating or editing compose files from the UI (that would require a text editor
in the web UI and is out of scope — use SSH).

Internally: the settings API calls `systemctl {enable,disable,start,stop} hm-stacks@<name>`.
The `enabled` marker file is written/removed to ensure OTA survival.

**Effort:** 1 week (server API + dashboard tab)  
**Risk:** Low — standard Flask blueprint + Alpine.js tab pattern already well-established in
the codebase.  
**If skipped:** Users manage stacks via SSH/systemctl. Fully functional; less polished for
non-technical users.

### Phase summary

| Phase | Scope | Effort | Risk | Required for launch? |
|---|---|---|---|---|
| 0 | CI image publishing | 1 week | Low | Yes — first thing needed |
| 1 | Docker/Podman in Yocto + stack framework | 2 weeks | Medium | Yes — core feature |
| 2 | Example stacks + docs | 1 week | Low | Yes — usability |
| 3 | Stack management UI | 1 week | Low | No — nice to have |
| **Total** | | **~5 weeks** | | |

Phases 0 and 1 can run in parallel. Phase 2 starts after Phase 1 (needs a working image to
test against). Phase 3 is independent of the others.

---

## 9. Open Questions

### 9.1 Docker CE or Podman?

**Recommendation: Podman.** Daemonless is meaningfully better for always-on embedded hardware.
No persistent 35 MB process when no add-ons are running. Better systemd integration.
No Docker socket to secure against container escape. `podman-compose` is compatible with
`docker-compose.yml` format at the level of complexity used in add-on stacks.

The main counterargument is community familiarity. Users know `docker compose up`, not
`podman compose up`. Mitigation: alias `docker` to `podman` on the Yocto image (standard
Podman packaging does this). From the user's perspective, the commands are identical.

Decision point: does the community benefit of "it's Docker" outweigh the hardware benefit
of "no always-on daemon"? For an 8 GB RAM Pi 4B, the daemon overhead is not a constraint —
but it is philosophically wrong to run unnecessary daemons on an appliance. Recommendation
stands: Podman.

### 9.2 Registry strategy for official example stacks

Option A: Docker Hub (`docker.io/rpihomemonitor/claude-automation:latest`).
Option B: GitHub Container Registry (`ghcr.io/vinu-dev/hm-claude-automation:latest`).

GHCR is free for public packages, integrates with the existing GitHub Actions CI, and keeps
everything in one place. Docker Hub's free tier has pull rate limits. Recommendation: GHCR.

Multi-arch images (`linux/arm64` + `linux/amd64`) are essential — users may run the server
on a Pi 5 or an x86 mini-PC. Use `docker buildx` with QEMU in CI.

### 9.3 How big can `/data/docker/` grow?

The `/data` partition is 39 GB on the current server SD card. Container images for typical
add-ons are 50-400 MB each. A user running 3-4 add-ons pulls 200 MB–1.5 GB of images.
Volumes for add-on data are usually small (RADIUS users file: KBs; Claude automation state:
MBs). Total Docker footprint for a typical install: under 3 GB.

The monitor's own recordings (currently consuming most of `/data`) are the bigger concern:
the lab server USB drive is at **87% full right now**. This should be addressed before the
open-source launch regardless. Recommendation: add a storage health check to the dashboard
that warns when `/data` (including `/data/docker/`) exceeds 80% utilisation. The existing
`StorageManager` can be extended for this.

### 9.4 First-boot vs post-install network creation

The `hm-addons` Docker bridge network must exist before any stack can start. It can be
created either:
- **At first boot** (by the existing `camera-hotspot.sh` first-boot hook or a dedicated
  hook), so it is always available.
- **By `hm-stacks-restore.service`** before starting any stack, with `podman network create
  hm-addons --ignore-existing`.

The second option is simpler (no new first-boot hook) and idempotent. Recommendation: create
the network in `restore-stacks.sh` before the enable loop, regardless of whether any stacks
are defined.

---

## 10. Recommendation

**Implement Option C as described above.**

The core monitor, OTA, and camera stacks remain on systemd and Yocto. They work correctly
today and the only thing broken in them is that there is no extension mechanism for community
add-ons. Docker/Podman as a guest runtime on the server solves exactly that problem without
touching anything that works.

The privileged-helper refactor (moving subprocess calls out of Flask) is **not a prerequisite**
for this proposal. The monitor is not being containerised. It can continue to call
`swupdate`, `systemctl`, `timedatectl`, and `mount` directly for as long as it lives on
systemd. That refactor remains on the backlog as a security hardening item but it is
decoupled from this work.

The total migration effort is approximately **5 person-weeks**, split as:
- 1 week: CI image publishing (Phase 0) — can start immediately
- 2 weeks: Yocto Docker integration + stack framework (Phase 1)
- 1 week: Example stacks + documentation (Phase 2)
- 1 week: Management UI (Phase 3, optional at launch)

The biggest risk is Phase 1 (Yocto layer integration). The mitigation is to do it on a
separate branch and test a full OTA cycle on hardware (Phase 1 build → flash → enable a
test stack → build Phase 1 again → swupdate → confirm stack survives) before merging.
That test plan should be written as a hardware test in `tests/hardware/` before any code
is merged.

The result for the open-source community: flash a `.img` to a Pi, boot, open a browser,
set up the monitor, drop a `docker-compose.yml` into `/data/stacks/`, enable it with one
`systemctl` command, and have a running Claude automation service or RADIUS server in
under five minutes. No Yocto toolchain. No bitbake. No recompilation. That is the correct
target experience.

# RPi Home Monitor

A self-hosted home security camera system built on Raspberry Pi,
running **Home Monitor OS** — a custom Yocto Linux distribution.
Like Tapo/Ring but open-source, no cloud subscriptions, no vendor lock-in.

## System Overview

```
RPi Zero 2W + ZeroCam             RPi 4 Model B (Server)            Phone
  (camera node)           RTSPS     (storage + web UI)      HTTPS    (dashboard)
  Captures 1080p video  ────────>  Receives streams       <────────  Live view
  Streams via RTSPS                Records 3-min clips               Clip playback
  Auto-discovered (mDNS)           Serves web dashboard              System health
  mTLS authenticated               TLS + auth + firewall             Login required
```

## Repository Structure

```
rpi-home-monitor/
│
├── app/                                    APPLICATION CODE
│   ├── server/                             RPi 4B (Flask web app)
│   │   ├── monitor/                        Python package
│   │   │   ├── api/                        REST API blueprints
│   │   │   ├── services/                   Background services
│   │   │   ├── templates/                  Web UI (Jinja2)
│   │   │   └── static/                     CSS + JS
│   │   └── config/                         systemd, nginx, nftables
│   └── camera/                             RPi Zero 2W (streaming)
│       ├── camera_streamer/                Python package
│       └── config/                         systemd, nftables
│
├── meta-home-monitor/                      CUSTOM YOCTO LAYER
│   ├── conf/
│   │   ├── layer.conf                      Layer definition
│   │   └── distro/
│   │       └── home-monitor.conf           Custom distro (replaces poky)
│   ├── classes/
│   │   └── monitor-image.bbclass           Shared image config
│   ├── recipes-core/
│   │   ├── images/
│   │   │   ├── home-monitor-image.inc      Shared server packages
│   │   │   ├── home-monitor-image-dev.bb   Server dev (debug, root SSH)
│   │   │   ├── home-monitor-image-prod.bb  Server prod (hardened)
│   │   │   ├── home-camera-image.inc       Shared camera packages
│   │   │   ├── home-camera-image-dev.bb    Camera dev
│   │   │   └── home-camera-image-prod.bb   Camera prod
│   │   ├── packagegroups/
│   │   │   ├── packagegroup-monitor-base.bb       Boot, SSH, networking
│   │   │   ├── packagegroup-monitor-video.bb      ffmpeg, gstreamer, v4l
│   │   │   ├── packagegroup-monitor-web.bb        nginx, flask, python
│   │   │   ├── packagegroup-monitor-security.bb   openssl, nftables, LUKS
│   │   │   └── packagegroup-camera-video.bb       ffmpeg, libcamera, v4l
│   │   └── base-files/
│   │       └── base-files_%.bbappend       OS branding (/etc/os-release)
│   ├── recipes-monitor/                    Server app recipe
│   ├── recipes-camera/                     Camera app recipe
│   ├── recipes-security/                   TLS cert generation
│   └── wic/                                A/B partition layouts
│
├── config/                                 YOCTO BUILD CONFIGS
│   ├── bblayers.conf                       Shared layer config
│   ├── rpi4b/local.conf                    Server (minimal, hw only)
│   └── zero2w/local.conf                   Camera (minimal, hw only)
│
├── scripts/
│   ├── setup-env.sh                        One-time host setup
│   ├── build.sh                            Build dev/prod images
│   └── sign-image.sh                       Sign OTA images
│
└── docs/
    ├── requirements.md                     Requirements + security spec
    └── architecture.md                     Software + security architecture
```

## Quick Start

### 1. Set up build machine

Ubuntu 24.04 VM, 8+ cores, 32GB RAM, 200GB disk.

```bash
git clone git@github.com:vinu-engineer/rpi-home-monitor.git ~/yocto
cd ~/yocto
./scripts/setup-env.sh
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0  # Ubuntu 24.04
```

### 2. Build images

```bash
# Development images (debug-tweaks, root SSH, dev tools)
./scripts/build.sh server-dev      # RPi 4B
./scripts/build.sh camera-dev      # RPi Zero 2W

# Production images (hardened, no root password, no debug)
./scripts/build.sh server-prod     # RPi 4B
./scripts/build.sh camera-prod     # RPi Zero 2W

# Both boards
./scripts/build.sh all-dev
./scripts/build.sh all-prod
```

First build takes 2-4 hours. Second board is much faster (shared sstate-cache).

### 3. Find images

| Target | Image location |
|--------|---------------|
| server-dev | `build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-dev-*.wic.bz2` |
| server-prod | `build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-prod-*.wic.bz2` |
| camera-dev | `build-zero2w/tmp/deploy/images/raspberrypi0-2w-64/home-camera-image-dev-*.wic.bz2` |
| camera-prod | `build-zero2w/tmp/deploy/images/raspberrypi0-2w-64/home-camera-image-prod-*.wic.bz2` |

Or download pre-built from [GitHub Releases](https://github.com/vinu-engineer/rpi-home-monitor/releases).

### 4. Flash to SD card

```bash
bzcat home-monitor-image-dev-*.wic.bz2 | sudo dd of=/dev/sdX bs=4M status=progress
```

Windows: decompress with 7-Zip, flash with [balenaEtcher](https://etcher.balena.io/).

## Custom Distro: `home-monitor`

We use a **custom distribution** instead of the reference `poky` distro. This is industry best practice for product development.

**What the distro controls** (in `meta-home-monitor/conf/distro/home-monitor.conf`):
- Init system: systemd (not sysvinit)
- Core features: usrmerge, WiFi, seccomp, PAM, zeroconf
- Package format: deb
- License policy: commercial + firmware blobs accepted
- Version pinning: kernel 6.6.x, Python 3.12.x, OpenSSL 3.5.x
- Build settings: SPDX license manifests, rm_work

**What local.conf controls** (machine-specific only):
- `MACHINE` — which board to build for
- `GPU_MEM` — GPU memory split
- `MACHINE_EXTRA_RRECOMMENDS` — WiFi firmware for specific chip
- CPU threads for parallel build

This separation means local.conf is portable — swap the MACHINE line and everything else stays correct.

## Dev vs Production Images

| Feature | Dev Image | Prod Image |
|---------|-----------|------------|
| Root login | Yes (no password) | No (locked) |
| SSH | Root SSH open | Key-only SSH |
| Debug tools | gdb, strace, tcpdump | None |
| debug-tweaks | Enabled | Disabled |
| First-boot wizard | Skipped | Required |
| Use case | Development, testing | Real devices |

## Development Workflow

### Fast iteration (app changes — seconds)

```bash
rsync -av app/server/monitor/ root@<rpi4b-ip>:/opt/monitor/monitor/
ssh root@<rpi4b-ip> systemctl restart monitor
```

### Full image rebuild (OS/package changes)

```bash
./scripts/build.sh server-dev
```

## Multi-Machine Build

Both boards share `bblayers.conf` and the `home-monitor` distro. Only `local.conf` differs:

```
config/bblayers.conf        shared (identical layers for both)
config/rpi4b/local.conf     MACHINE="raspberrypi4-64", GPU_MEM=128
config/zero2w/local.conf    MACHINE="raspberrypi0-2w-64", GPU_MEM=64
```

Shared `downloads/` and `sstate-cache/` — the second board reuses most compiled artifacts.

## Security

- **TLS everywhere** — HTTPS for web, RTSPS with mTLS for cameras
- **Encrypted storage** — LUKS2 on /data partition
- **Firewall** — nftables, minimal ports, cameras only talk to server
- **No default passwords** — prod images require first-boot setup
- **Camera pairing** — mTLS client certs, rogue cameras rejected
- **Signed OTA** — Ed25519 signed firmware updates
- **Audit logging** — all security events logged

See [docs/architecture.md](docs/architecture.md) for full threat model.

## Useful Commands

```bash
# Build environments
source poky/oe-init-build-env build          # server
source poky/oe-init-build-env build-zero2w   # camera

# Rebuild specific packages
bitbake monitor-server -c cleansstate && bitbake home-monitor-image-dev
bitbake camera-streamer -c cleansstate && bitbake home-camera-image-dev

# On the device
systemctl status monitor
journalctl -u monitor -f
cat /etc/os-release                          # Shows "Home Monitor OS 1.0.0"
nmcli device wifi connect "SSID" password "pass"
```

## Documentation

| Document | Contents |
|----------|----------|
| [docs/requirements.md](docs/requirements.md) | User needs, software requirements, security requirements, API spec |
| [docs/architecture.md](docs/architecture.md) | Software architecture, security design, threat model, data model |
| [docs/development-guide.md](docs/development-guide.md) | Mandatory development rules (git, Yocto, app, security) |
| [CLAUDE.md](CLAUDE.md) | Project context for development |

## Phases

- **Phase 1** (current): Single camera, live view, clip recording, web dashboard, auth, health, security, OTA-ready
- **Phase 2**: Multi-camera, motion detection, notifications, cloud relay, mobile app
- **Phase 3**: AI/ML, zones, clip protection, smart home integration

## Tech Stack

Home Monitor OS (Yocto Scarthgap 5.0 LTS) | Python 3 + Flask | nginx | ffmpeg | HLS | Avahi/mDNS | swupdate | LUKS2 | nftables | OpenSSL | systemd

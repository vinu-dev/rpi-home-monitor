# RPi Home Monitor

A self-hosted home security camera system built on Raspberry Pi.
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
├── app/                               APPLICATION CODE
│   ├── server/                        RPi 4B server (Flask web app)
│   │   ├── monitor/                   Python package
│   │   │   ├── __init__.py            App factory
│   │   │   ├── auth.py               Login, sessions, CSRF
│   │   │   ├── models.py             Camera, User, Settings, Clip
│   │   │   ├── api/                   REST API blueprints
│   │   │   │   ├── cameras.py        Camera CRUD + discovery
│   │   │   │   ├── recordings.py     Clip listing + timeline
│   │   │   │   ├── live.py           HLS streaming
│   │   │   │   ├── system.py         Health + storage
│   │   │   │   ├── settings.py       Config management
│   │   │   │   ├── users.py          User management
│   │   │   │   └── ota.py            OTA updates
│   │   │   ├── services/             Background services
│   │   │   │   ├── recorder.py       ffmpeg clip recording
│   │   │   │   ├── discovery.py      mDNS camera scanner
│   │   │   │   ├── storage.py        Loop recording + cleanup
│   │   │   │   ├── health.py         CPU/temp/RAM/disk
│   │   │   │   └── audit.py          Security event logging
│   │   │   ├── templates/            Web UI (Jinja2)
│   │   │   └── static/               CSS + JS
│   │   └── config/                    systemd, nginx, nftables, logrotate
│   │
│   └── camera/                        RPi Zero 2W (streaming service)
│       ├── camera_streamer/           Python package
│       │   ├── main.py               Entry point
│       │   ├── capture.py            v4l2 device management
│       │   ├── stream.py             ffmpeg RTSPS + reconnect
│       │   ├── discovery.py          mDNS advertisement
│       │   ├── config.py             Config management
│       │   ├── pairing.py            Certificate exchange
│       │   └── ota_agent.py          Accept OTA pushes
│       └── config/                    systemd, nftables, default config
│
├── meta-home-monitor/                 YOCTO LAYER
│   ├── conf/layer.conf
│   ├── recipes-core/images/           Image recipes (server + camera)
│   ├── recipes-monitor/               Packages app/server/ into image
│   ├── recipes-camera/                Packages app/camera/ into image
│   ├── recipes-security/              TLS cert generation (first boot)
│   └── wic/                           A/B partition layouts (OTA-ready)
│
├── config/                            YOCTO BUILD CONFIGS
│   ├── bblayers.conf                  Shared layer config
│   ├── rpi4b/local.conf               Server build config
│   └── zero2w/local.conf              Camera build config
│
├── scripts/                           BUILD SCRIPTS
│   ├── setup-env.sh                   One-time host setup
│   ├── build.sh                       Build server/camera/all
│   └── sign-image.sh                  Sign OTA images (Ed25519)
│
├── docs/                              DOCUMENTATION
│   ├── requirements.md                User needs + SW/security requirements
│   └── architecture.md                Software + security architecture
│
└── CLAUDE.md                          Project context
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
./scripts/build.sh server    # RPi 4B server image
./scripts/build.sh camera    # RPi Zero 2W camera image
./scripts/build.sh all       # Both
```

### 3. Find images

| Board | Image |
|-------|-------|
| RPi 4B | `build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-*.wic.bz2` |
| Zero 2W | `build-zero2w/tmp/deploy/images/raspberrypi0-2w-64/home-camera-image-*.wic.bz2` |

Or download pre-built from [GitHub Releases](https://github.com/vinu-engineer/rpi-home-monitor/releases).

### 4. Flash to SD card

```bash
bzcat home-monitor-image-*.wic.bz2 | sudo dd of=/dev/sdX bs=4M status=progress
bzcat home-camera-image-*.wic.bz2 | sudo dd of=/dev/sdY bs=4M status=progress
```

Windows: decompress with 7-Zip, flash with [balenaEtcher](https://etcher.balena.io/).

## Development Workflow

### Fast iteration (app changes — seconds, not hours)

```bash
# Edit app code locally, then rsync to running device
rsync -av app/server/monitor/ root@<rpi4b-ip>:/opt/monitor/monitor/
ssh root@<rpi4b-ip> systemctl restart monitor

# Camera app
rsync -av app/camera/camera_streamer/ root@<zero2w-ip>:/opt/camera/camera_streamer/
ssh root@<zero2w-ip> systemctl restart camera-streamer
```

### Full image rebuild (OS or package changes)

```bash
./scripts/build.sh server   # or camera, or all
```

## Multi-Machine Build

Both boards share the same Yocto layers and `bblayers.conf`. Only `local.conf` differs:

```
config/bblayers.conf        shared (identical layers for both)
config/rpi4b/local.conf     MACHINE="raspberrypi4-64", GPU_MEM=128
config/zero2w/local.conf    MACHINE="raspberrypi0-2w-64", GPU_MEM=64
```

Shared `downloads/` and `sstate-cache/` — second board build is much faster.

## Security

This is a home camera system — security is critical:

- **TLS everywhere** — HTTPS for web, RTSPS with mTLS for cameras
- **Encrypted storage** — LUKS2 on /data partition (recordings, config, certs)
- **Firewall** — nftables, minimal ports, cameras only talk to server
- **No default passwords** — first-boot wizard forces account creation
- **Camera pairing** — mTLS client certs, rogue cameras rejected
- **Signed OTA** — Ed25519 signed firmware updates
- **Audit logging** — all security events logged

See [docs/architecture.md](docs/architecture.md) for full threat model and security design.

## Useful Commands

```bash
# Yocto build environment
source poky/oe-init-build-env build          # server
source poky/oe-init-build-env build-zero2w   # camera

# Rebuild specific packages
bitbake monitor-server -c cleansstate && bitbake home-monitor-image
bitbake camera-streamer -c cleansstate && bitbake home-camera-image

# On the device
systemctl status monitor                      # server app
systemctl status camera-streamer              # camera app
journalctl -u monitor -f                      # server logs
nmcli device wifi connect "SSID" password "pass"  # WiFi
```

## Documentation

| Document | Contents |
|----------|----------|
| [docs/requirements.md](docs/requirements.md) | User needs, software requirements, security requirements, REST API spec |
| [docs/architecture.md](docs/architecture.md) | Software architecture, security design, threat model, data model, partition layout |
| [CLAUDE.md](CLAUDE.md) | Project context for development |

## Phases

- **Phase 1** (current): Single camera, live view, clip recording, web dashboard, auth, health, security, OTA-ready
- **Phase 2**: Multi-camera, motion detection, notifications, cloud relay, mobile app
- **Phase 3**: AI/ML, zones, clip protection, smart home integration

## Tech Stack

Yocto Scarthgap 5.0 LTS | Python 3 + Flask | nginx | ffmpeg | HLS | Avahi/mDNS | swupdate | LUKS2 | nftables | OpenSSL | systemd

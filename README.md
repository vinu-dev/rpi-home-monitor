# RPi Home Monitor

A self-hosted home security camera system built on Raspberry Pi,
running **Home Monitor OS** — a custom Yocto Linux distribution.
Like Tapo/Ring but open-source, no cloud subscriptions, no vendor lock-in.

```
RPi Zero 2W + ZeroCam             RPi 4 Model B (Server)            Phone
  (camera node)           RTSPS     (storage + web UI)      HTTPS    (dashboard)
  Captures 1080p video  ────────>  Receives streams       <────────  Live view
  Streams via RTSPS                Records 3-min clips               Clip playback
  Auto-discovered (mDNS)           Serves web dashboard              System health
  mTLS authenticated               TLS + auth + firewall             Login required
```

## Quick Start

```bash
# 1. Clone
git clone git@github.com:vinu-engineer/rpi-home-monitor.git ~/yocto
cd ~/yocto

# 2. Install all prerequisites (Ubuntu 24.04)
./scripts/setup-env.sh

# 3. Build images
./scripts/build.sh server-dev      # RPi 4B development image
./scripts/build.sh camera-dev      # RPi Zero 2W development image

# 4. Flash to SD card
bzcat build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-dev-*.wic.bz2 \
  | sudo dd of=/dev/sdX bs=4M status=progress
```

First build takes 2-4 hours. Subsequent builds are much faster (shared sstate-cache).

Pre-built images: [GitHub Releases](https://github.com/vinu-engineer/rpi-home-monitor/releases)

## Build Targets

| Command | Board | Image |
|---------|-------|-------|
| `./scripts/build.sh server-dev` | RPi 4B | Dev (debug, root SSH) |
| `./scripts/build.sh server-prod` | RPi 4B | Prod (hardened) |
| `./scripts/build.sh camera-dev` | Zero 2W | Dev (debug, root SSH) |
| `./scripts/build.sh camera-prod` | Zero 2W | Prod (hardened) |
| `./scripts/build.sh all-dev` | Both | Dev |
| `./scripts/build.sh all-prod` | Both | Prod |

## Run Tests

```bash
cd app/server && pytest     # Server: 49 tests
cd app/camera && pytest     # Camera: 21 tests
```

## Documentation

| Document | Contents |
|----------|----------|
| [docs/build-setup.md](docs/build-setup.md) | Build machine setup, prerequisites, troubleshooting |
| [docs/hardware-setup.md](docs/hardware-setup.md) | Shopping list, assembly, flashing, first boot |
| [docs/requirements.md](docs/requirements.md) | User needs, software/security requirements, API spec |
| [docs/architecture.md](docs/architecture.md) | Software architecture, security design, threat model |
| [docs/development-guide.md](docs/development-guide.md) | Development rules (git, Yocto, app, security) |
| [docs/testing-guide.md](docs/testing-guide.md) | Writing tests, running tests, coverage |

## Phases

- **Phase 1** (current): Single camera, live view, clip recording, web dashboard, auth, security, OTA-ready
- **Phase 2**: Multi-camera, motion detection, notifications, cloud relay, mobile app
- **Phase 3**: AI/ML, zones, clip protection, smart home integration

## License

MIT

# RPi Home Monitor

[![CI](https://github.com/vinu-engineer/rpi-home-monitor/actions/workflows/test.yml/badge.svg)](https://github.com/vinu-engineer/rpi-home-monitor/actions/workflows/test.yml)

**A self-hosted home security camera system built on Raspberry Pi.** Open-source alternative to Ring, Tapo, and Nest — with no cloud subscriptions, no vendor lock-in, and complete control over your data.

RPi Home Monitor runs **Home Monitor OS**, a custom Linux distribution built with the Yocto Project, purpose-built for home surveillance on low-cost hardware.

## Why RPi Home Monitor?

- **Your data stays home.** Video never leaves your network. No cloud uploads, no third-party access, no monthly fees.
- **Security by design.** HTTPS for web, mTLS camera streaming, encrypted storage (LUKS), firewall-hardened OS, bcrypt auth with rate limiting, PIN-based camera pairing.
- **Built on real hardware.** Runs on a $35 Raspberry Pi 4B (server) and $15 Zero 2W (cameras). No proprietary hardware required.
- **Automatic camera discovery.** Plug in a camera node, connect it to WiFi, and it appears in your dashboard via mDNS.
- **OTA updates with rollback.** A/B partition scheme means failed updates automatically roll back. No bricked devices.
- **Tapo-style recording.** Continuous 3-minute MP4 clips organized by camera and date, with timeline playback.
- **Fully open source.** Inspect every line, from the OS image to the web dashboard. AGPL-3.0 licensed.

## Architecture

```
┌─────────────────┐    RTSP stream      ┌──────────────────┐    HTTPS     ┌──────────┐
│  Camera Node    │ ─────────────────> │   Home Server     │ <────────── │  Phone / │
│  RPi Zero 2W   │                     │   RPi 4 Model B   │             │  Laptop  │
│                 │    mDNS discovery   │                    │             │          │
│  1080p capture  │ <─ ─ ─ ─ ─ ─ ─ ─> │  Records clips     │             │  Web UI  │
│  RTSP stream    │                     │  Serves dashboard  │             │  Live    │
│  Auto-pairs     │    OTA push         │  Manages cameras   │             │  Playback│
│                 │ <───────────────── │  System health     │             │  Admin   │
└─────────────────┘                     └──────────────────┘             └──────────┘
       x N                                     x 1                           x N
```

| Component | Hardware | Role |
|-----------|----------|------|
| **Home Server** | Raspberry Pi 4 Model B (4GB+) | Receives streams, records clips, serves web dashboard, manages cameras |
| **Camera Node** | Raspberry Pi Zero 2W + ZeroCam | Captures 1080p video, streams to server over RTSPS |
| **Dashboard** | Any phone/laptop on LAN | Live view (WebRTC/HLS), clip playback, camera management, system admin |

## First Boot Setup

Both the server and camera use a **captive portal** for zero-config WiFi provisioning. Cameras also have **LED status feedback**. Full step-by-step instructions are in [CHANGELOG.md](CHANGELOG.md#setup-guide).

**Quick version:**
1. **Power on** — Camera LED starts slow blinking = setup mode
2. **Connect phone** to hotspot (`HomeMonitor-Setup` / `HomeCam-Setup`)
3. **Setup wizard auto-opens** — configure WiFi + admin password (server) or WiFi + server address + camera login (camera)
4. **Done** — LED goes solid = running. Camera finds server automatically via `rpi-divinu.local`
5. **Access camera** at `http://rpi-divinu-cam-XXXX.local` (shown after setup completes)

### LED Status Indicators

| LED Pattern | Meaning |
|-------------|---------|
| **Slow blink** (1s on / 1s off) | Setup mode — waiting for WiFi configuration |
| **Fast blink** (200ms on / 200ms off) | Connecting — attempting to join WiFi network |
| **Very fast blink** (100ms on / 100ms off) | Error — WiFi connection failed, hotspot restarting |
| **Solid on** | Running normally — connected and operational |
| **Off** | Service stopped |

### Server Discovery (mDNS)

The server advertises itself as `rpi-divinu.local` on the local network via Avahi/mDNS. Cameras find the server automatically — no need to know the server's IP address. Each camera also gets its own `.local` address (e.g., `http://rpi-divinu-cam-d8ee.local`) for direct access to its status page. If mDNS doesn't work on your network, enter IPs manually.

## Key Features

| Feature | Details |
|---------|---------|
| Live View | WebRTC (sub-second latency) with HLS fallback in any mobile browser |
| Recording | Continuous 3-minute MP4 clips, organized by camera/date |
| Camera Management | Auto-discovery, confirm/rename/remove via dashboard |
| User Auth | Server: bcrypt + CSRF + rate limiting. Camera: PBKDF2-SHA256 + sessions. **Note:** a default `admin`/`admin` account is created on first boot — change the password during setup |
| Role-Based Access | Admin (full control) and Viewer (read-only) roles |
| System Health | CPU temp, memory, disk usage, uptime monitoring |
| Storage Management | Automatic cleanup of oldest clips when disk is full |
| OTA Updates | Signed firmware updates with A/B rollback |
| Audit Logging | All admin actions logged (append-only) |
| Encrypted Storage | LUKS-encrypted /data partition for recordings and config |
| Firewall | nftables — cameras can only talk to server, minimal open ports |

### Security Feature Status

| Feature | Status | Notes |
|---------|--------|-------|
| HTTPS (TLS) | **Implemented** | Self-signed certs, NGINX terminates TLS |
| bcrypt auth + CSRF | **Implemented** | Cost 12, rate limiting (warn at 5, block at 10) |
| Session management | **Implemented** | 30min idle / 24hr absolute timeout |
| LUKS encryption | **Implemented** | /data partition encrypted at rest |
| nftables firewall | **Implemented** | Default DROP, minimal open ports |
| Audit logging | **Implemented** | Append-only JSON, all admin actions |
| Default admin warning | **Implemented** | `admin`/`admin` created on first boot, must change during setup |
| RTSPS (mTLS) | **Implemented** | Camera streams over RTSPS with mTLS client certs after pairing |
| mTLS camera pairing | **Implemented** | PIN-based pairing with certificate exchange (ADR-0009) |
| Factory reset | **Implemented** | WiFi wipe, config reset, returns to first-boot state |
| OTA updates | **Implemented** | Server OTA service (verify, stage, install) + camera OTA agent (port 8080, mTLS) |

## Quick Start

```bash
# Clone
git clone git@github.com:vinu-engineer/rpi-home-monitor.git ~/yocto
cd ~/yocto

# Install prerequisites (Ubuntu 24.04)
./scripts/setup-env.sh

# Build images
./scripts/build.sh server-dev      # RPi 4B development image
./scripts/build.sh camera-dev      # Zero 2W development image

# Flash to SD card
bzcat build/tmp/deploy/images/raspberrypi4-64/home-monitor-image-dev-*.wic.bz2 \
  | sudo dd of=/dev/sdX bs=4M status=progress
```

First build takes 2-4 hours. Subsequent builds use cached artifacts and are much faster.

## Build Targets

| Command | Board | Image |
|---------|-------|-------|
| `./scripts/build.sh server-dev` | RPi 4B | Development (debug, root SSH) |
| `./scripts/build.sh server-prod` | RPi 4B | Production (hardened, no root) |
| `./scripts/build.sh camera-dev` | Zero 2W | Development (debug, root SSH) |
| `./scripts/build.sh camera-prod` | Zero 2W | Production (hardened, no root) |

## Run Tests

```bash
cd app/server && pytest    # threshold: 80% coverage
cd app/camera && pytest    # threshold: 55% coverage
```

Results and coverage reports are available in the [CI workflow](https://github.com/vinu-engineer/rpi-home-monitor/actions).

## Documentation

| Document | What's Inside |
|----------|---------------|
| [Hardware Setup](docs/hardware-setup.md) | Shopping list, assembly, flashing, first boot, troubleshooting |
| [Build Setup](docs/build-setup.md) | Build machine requirements, prerequisites, build commands |
| [Requirements](docs/requirements.md) | User stories, software/security requirements, API specification |
| [Architecture](docs/architecture.md) | System design, security model, threat analysis, data model |
| [Development Guide](docs/development-guide.md) | Git workflow, Yocto rules, app conventions, security rules |
| [Testing Guide](docs/testing-guide.md) | Writing tests, running tests, coverage targets |

## Roadmap

- **Phase 1** (current): Single camera, live view, clip recording, web dashboard, authentication, security hardening, mTLS camera pairing, OTA updates, factory reset
- **Phase 2**: Multi-camera support, motion detection, push notifications, audio
- **Phase 3**: Cloud relay, mobile app, AI/ML object detection, activity zones, clip protection, smart home integration

## Contributing

Contributions are welcome. Please read the [Development Guide](docs/development-guide.md) before submitting a PR.

## License

This project is licensed under **AGPL-3.0** — see [LICENSE](LICENSE) for details.

# RPi Home Monitor

[![CI](https://github.com/vinu-engineer/rpi-home-monitor/actions/workflows/test.yml/badge.svg)](https://github.com/vinu-engineer/rpi-home-monitor/actions/workflows/test.yml)

**A self-hosted home security camera system built on Raspberry Pi.** Open-source alternative to Ring, Tapo, and Nest — with no cloud subscriptions, no vendor lock-in, and complete control over your data.

RPi Home Monitor runs **Home Monitor OS**, a custom Linux distribution built with the Yocto Project, purpose-built for home surveillance on low-cost hardware.

## Why RPi Home Monitor?

- **Your data stays home.** Video never leaves your network. No cloud uploads, no third-party access, no monthly fees.
- **Security by design.** TLS everywhere, mTLS between cameras, encrypted storage (LUKS), firewall-hardened OS, bcrypt auth with rate limiting.
- **Built on real hardware.** Runs on a $35 Raspberry Pi 4B (server) and $15 Zero 2W (cameras). No proprietary hardware required.
- **Automatic camera discovery.** Plug in a camera node, connect it to WiFi, and it appears in your dashboard via mDNS.
- **OTA updates with rollback.** A/B partition scheme means failed updates automatically roll back. No bricked devices.
- **Tapo-style recording.** Continuous 3-minute MP4 clips organized by camera and date, with timeline playback.
- **Fully open source.** Inspect every line, from the OS image to the web dashboard. AGPL-3.0 licensed.

## Architecture

```
┌─────────────────┐    RTSPS (mTLS)    ┌──────────────────┐    HTTPS     ┌──────────┐
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
| **Dashboard** | Any phone/laptop on LAN | Live view (HLS), clip playback, camera management, system admin |

## First Boot Setup

Both the server and camera use a **captive portal** for zero-config WiFi provisioning:

1. **Power on** the device. The onboard LED starts **slow blinking** (1s on/off) = setup mode.
2. **Connect your phone** to the WiFi hotspot:
   - Server: `HomeMonitor-Setup` (password: `homemonitor`)
   - Camera: `HomeCam-Setup` (password: `homecamera`)
3. **Captive portal auto-opens** — your phone shows a "Sign in to network" popup with the setup wizard. If it doesn't, open `http://10.42.0.1` manually in a browser.
4. **Configure WiFi** credentials and (for camera) the server address. Default server address is `homemonitor.local` (auto-discovery via mDNS).
5. **Save & Connect** — the LED switches to **fast blinking** (connecting), then **solid on** (connected). The hotspot disappears.

If WiFi connection fails, the LED blinks rapidly (error) and the hotspot restarts automatically for retry.

### LED Status Indicators

The onboard ACT LED provides visual feedback on both server and camera devices:

| LED Pattern | Meaning |
|-------------|---------|
| **Slow blink** (1s on / 1s off) | Setup mode — waiting for WiFi configuration |
| **Fast blink** (200ms on / 200ms off) | Connecting — attempting to join WiFi network |
| **Very fast blink** (100ms on / 100ms off) | Error — WiFi connection failed, hotspot restarting |
| **Solid on** | Running normally — connected and operational |
| **Off** | Service stopped |

### Server Discovery (mDNS)

The server advertises itself as `homemonitor.local` on the local network using Avahi/mDNS. Cameras use this to find the server automatically — no need to know or type the server's IP address. The camera setup page pre-fills `homemonitor.local` as the default server address.

If mDNS is not available on your network, you can enter the server's IP address manually during camera setup.

## Key Features

| Feature | Details |
|---------|---------|
| Live View | HLS streaming in any mobile browser |
| Recording | Continuous 3-minute MP4 clips, organized by camera/date |
| Camera Management | Auto-discovery, confirm/rename/remove via dashboard |
| User Auth | bcrypt passwords, session management, CSRF protection, rate limiting |
| Role-Based Access | Admin (full control) and Viewer (read-only) roles |
| System Health | CPU temp, memory, disk usage, uptime monitoring |
| Storage Management | Automatic cleanup of oldest clips when disk is full |
| OTA Updates | Signed firmware updates with A/B rollback |
| Audit Logging | All admin actions logged (append-only) |
| Encrypted Storage | LUKS-encrypted /data partition for recordings and config |
| Firewall | nftables — cameras can only talk to server, minimal open ports |

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
cd app/server && pytest
cd app/camera && pytest
```

Test results and coverage reports are available in the [CI workflow](https://github.com/vinu-engineer/rpi-home-monitor/actions).

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

- **Phase 1** (current): Single camera, live view, clip recording, web dashboard, authentication, security hardening, OTA-ready
- **Phase 2**: Multi-camera support, motion detection, push notifications, cloud relay, mobile app, audio
- **Phase 3**: AI/ML object detection, activity zones, clip protection, smart home integration

## Contributing

Contributions are welcome. Please read the [Development Guide](docs/development-guide.md) before submitting a PR.

## License

This project is licensed under **AGPL-3.0** — see [LICENSE](LICENSE) for details.

Commercial licensing is available for organizations that want to use RPi Home Monitor in proprietary products without the AGPL obligations. Contact the maintainer for details.

# RPi Home Monitor - Project Context

## What This Is

A self-hosted home security camera system (like Tapo/Ring but open-source, no cloud fees).
Built on Raspberry Pi hardware running custom Yocto Linux.

## Architecture

- **RPi 4 Model B** = Home server. Receives camera streams, records 3-min clips, serves web dashboard.
- **RPi Zero 2W + PiHut ZeroCam** = Camera nodes. One per location. Streams video to server over WiFi via RTSPS.
- **Mobile Web UI** = Dashboard accessed from phone/laptop over HTTPS.

Two separate applications in `app/`:
- `app/server/` = Flask web app (monitor-server) — runs on RPi 4B
- `app/camera/` = Python streaming service (camera-streamer) — runs on Zero 2W

Yocto layer in `meta-home-monitor/` packages the apps into bootable images.

## Key Technical Decisions

- **TLS everywhere from Phase 1** — HTTPS for web, RTSPS (mTLS) for cameras. Self-signed local CA.
- **LUKS encrypted /data partition** — recordings, config, certs all encrypted at rest.
- **nftables firewall** — minimal open ports, cameras can only talk to server.
- **swupdate A/B partitions** — atomic OTA with rollback.
- **3-minute MP4 clips** (Tapo-style) — stored per camera per date.
- **JSON files for data** (no database) — cameras.json, users.json, settings.json.
- **Avahi/mDNS** for camera auto-discovery on LAN.
- **HLS** for live view in mobile browsers.
- **bcrypt + CSRF + rate limiting** for web auth.
- **Audit logging** for security events.

## Phases

- **Phase 1:** Local-only. Single camera, live view, clip recording, web dashboard, auth, health, security, OTA-ready.
- **Phase 2:** Multi-camera, motion detection, notifications, cloud relay, mobile app, audio.
- **Phase 3:** AI/ML detection, zones, clip protection, smart home integration.

## Repository Layout

```
app/server/          — Server Flask application (developed independently of Yocto)
app/camera/          — Camera streamer application
meta-home-monitor/   — Yocto layer (recipes, image configs, partition layouts)
config/              — Yocto build configs (bblayers.conf, per-machine local.conf)
scripts/             — Build, setup, and signing scripts
docs/                — requirements.md, architecture.md
```

## Development Workflow

**Fast iteration (app changes):**
```bash
rsync -av app/server/monitor/ root@<rpi4b-ip>:/opt/monitor/monitor/
ssh root@<rpi4b-ip> systemctl restart monitor
```

**Full image rebuild (OS/package changes):**
```bash
./scripts/build.sh server   # or camera, or all
```

## Build VM

- Host: 35.230.155.87 (GCP, europe-west2)
- User: vinu_emailme
- Access: `ssh vinu_emailme@35.230.155.87`
- Repo on VM: `~/yocto/`

## Docs

- `docs/requirements.md` — User needs, software requirements, security requirements, API spec
- `docs/architecture.md` — Software architecture, security design, threat model, data model, partition layout

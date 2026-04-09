# RPi Home Monitor - Project Context

## What This Is

A DIY home security camera system built on Raspberry Pi hardware running custom Yocto Linux.
Think of it as a self-hosted Tapo/Ring camera system — no cloud subscription, no vendor lock-in.

## Architecture

- **RPi 4 Model B** = Home server. Receives camera streams, records clips, serves web dashboard.
- **RPi Zero 2W + PiHut ZeroCam** = Camera nodes. One per location (front door, hallway, etc.). Streams video to the server over WiFi via RTSP.
- **Mobile Web UI** = Dashboard accessed from phone/laptop on the same network. Shows live feeds, recorded clips, system health.

## Build System

- Yocto Scarthgap (5.0 LTS), built on Ubuntu 24.04 GCP VM
- Single repo, two images: `home-monitor-image` (server), `home-camera-image` (camera)
- Build: `./scripts/build.sh server|camera|all`
- GitHub Releases for distributing flashable images

## Key Technical Decisions

- systemd + usrmerge
- swupdate for OTA (dual A/B partition scheme) — planned from Phase 1
- Recordings segmented into 3-minute MP4 clips (Tapo-style)
- Authentication required (admin + viewer roles)
- Camera auto-discovery via mDNS/Avahi on local network
- Loop recording (oldest clips deleted when storage full)

## Phases

- **Phase 1:** Local-only. Single camera, live view, clip recording, web dashboard, auth, health monitoring, OTA-ready partition layout.
- **Phase 2:** Multi-camera, motion detection, push notifications, cloud relay for remote access, mobile app.
- **Phase 3:** AI/ML detection, zones, clip protection/starring, smart home integration.

## Repository Layout

- `config/` — Yocto build configs (bblayers.conf, per-machine local.conf)
- `meta-home-monitor/` — Custom Yocto layer with all recipes
- `scripts/` — Build and setup scripts
- `docs/` — Requirements and design documents

## Build Commands

```bash
# On Ubuntu 24.04 VM:
./scripts/setup-env.sh          # one-time host setup
./scripts/build.sh server       # build RPi 4B image
./scripts/build.sh camera       # build Zero 2W image
```

## Remote Build VM

- Host: 35.230.155.87 (GCP, europe-west2)
- User: vinu_emailme
- Access: `ssh vinu_emailme@35.230.155.87`
- Repo on VM: `~/yocto/`

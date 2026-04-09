# RPi Home Monitor - Project Context

## What This Is

A self-hosted home security camera system (like Tapo/Ring but open-source, no cloud fees).
Built on Raspberry Pi hardware running **Home Monitor OS** — a custom Yocto Linux distribution.

## Architecture

- **RPi 4 Model B** = Home server. Receives camera streams, records 3-min clips, serves web dashboard.
- **RPi Zero 2W + PiHut ZeroCam** = Camera nodes. One per location. Streams video to server via RTSPS.
- **Mobile Web UI** = Dashboard accessed from phone/laptop over HTTPS.

Two separate applications in `app/`:
- `app/server/` = Flask web app (monitor-server) — runs on RPi 4B
- `app/camera/` = Python streaming service (camera-streamer) — runs on Zero 2W

## Custom Distro: `home-monitor`

We do NOT use `DISTRO = "poky"` (the reference distro). We have our own:
- **Distro config:** `meta-home-monitor/conf/distro/home-monitor.conf`
- **Controls:** systemd, usrmerge, WiFi, seccomp, PAM, licensing, version pinning
- **local.conf is minimal** — only machine-specific hardware settings
- **Dev vs Prod images** — dev has debug-tweaks + root; prod is hardened

## Key Technical Decisions

- **Custom distro** — all system policy in `home-monitor.conf`, not scattered in local.conf
- **Packagegroups** — logical package bundles (base, video, web, security, camera-video)
- **Dev/Prod image variants** — dev for development, prod for real devices
- **TLS everywhere from Phase 1** — HTTPS for web, RTSPS (mTLS) for cameras
- **LUKS encrypted /data partition** — recordings, config, certs all encrypted at rest
- **nftables firewall** — minimal open ports, cameras can only talk to server
- **swupdate A/B partitions** — atomic OTA with rollback
- **3-minute MP4 clips** (Tapo-style) — stored per camera per date
- **JSON files for data** (no database) — cameras.json, users.json, settings.json
- **Avahi/mDNS** for camera auto-discovery on LAN
- **HLS** for live view in mobile browsers
- **OS branding** — `/etc/os-release` shows "Home Monitor OS"

## Phases

- **Phase 1:** Local-only. Single camera, live view, clip recording, web dashboard, auth, health, security, OTA-ready.
- **Phase 2:** Multi-camera, motion detection, notifications, cloud relay, mobile app, audio.
- **Phase 3:** AI/ML detection, zones, clip protection, smart home integration.

## Repository Layout

```
app/server/          — Server Flask application
app/camera/          — Camera streamer application
meta-home-monitor/   — Custom Yocto layer
  conf/distro/       — Custom distro definition (home-monitor.conf)
  classes/           — Shared bbclass files
  recipes-core/
    images/          — Dev and prod image variants (.inc + .bb)
    packagegroups/   — Logical package bundles
    base-files/      — OS branding (/etc/os-release)
  recipes-monitor/   — Server app recipe
  recipes-camera/    — Camera app recipe
  recipes-security/  — TLS cert generation
  wic/               — A/B partition layouts
config/              — Yocto build configs (minimal local.conf per machine)
scripts/             — Build, setup, and signing scripts
docs/                — requirements.md, architecture.md, development-guide.md
```

## Build Commands

```bash
# Development images (debug-tweaks, root SSH, dev tools)
./scripts/build.sh server-dev
./scripts/build.sh camera-dev

# Production images (hardened, no root, no debug)
./scripts/build.sh server-prod
./scripts/build.sh camera-prod
```

## ⚠️ MANDATORY: Development Rules

**Before making ANY changes, read [`docs/development-guide.md`](docs/development-guide.md).**

This document defines ALL rules for:
- **Git workflow** — branch naming, commit messages, PR process, release process
- **Yocto rules** — distro policy, layer structure, recipe conventions, image rules
- **App development** — code organization, Python style, security, API design, testing
- **File/directory conventions** — naming, where things go
- **Security rules** — TLS, secrets, input validation, firewall
- **Deployment/operations** — systemd, logging, OTA

**Key rules summary (read the full guide for details):**
1. Never commit directly to `main` — use feature branches + PRs
2. Never put distro policy in `local.conf` — use `home-monitor.conf`
3. Never add packages directly to image recipes — use packagegroups
4. Never store secrets in code or config files
5. Always verify Yocto changes parse: `bitbake -p` before committing
6. Always follow the branch naming convention: `feature/`, `fix/`, `recipe/`, `docs/`, `release/`

## Development Workflow

**Fast iteration (app changes):**
```bash
rsync -av app/server/monitor/ root@<rpi4b-ip>:/opt/monitor/monitor/
ssh root@<rpi4b-ip> systemctl restart monitor
```

**Full image rebuild (OS/package changes):**
```bash
./scripts/build.sh server-dev
```

## Yocto Builds on VM

- Always use `./scripts/build.sh` to build images — it sets up the correct MACHINE, build directory, and local.conf per board
- Server builds use `build/` dir with `config/rpi4b/local.conf`
- Camera builds use `build-zero2w/` dir with `config/zero2w/local.conf`
- Never manually run `bitbake` with `MACHINE=` env var overrides — `local.conf` takes precedence and silently ignores it
- Always run builds inside `tmux` on the VM so they survive SSH disconnects
- Recipe LICENSE fields use MIT (Yocto recipe metadata convention) — don't change them to match the project AGPL-3.0

## Build VM

Set up your own build VM following [docs/build-setup.md](docs/build-setup.md).
The developer must provide their own VM IP and credentials — never commit these to the repo.

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/requirements.md](docs/requirements.md) | User needs, software requirements, security requirements, API spec |
| [docs/architecture.md](docs/architecture.md) | Software architecture, security design, threat model, data model |
| [docs/development-guide.md](docs/development-guide.md) | **Mandatory rules** for all development (git, Yocto, app, security) |
| [docs/testing-guide.md](docs/testing-guide.md) | **Mandatory** — how to write tests, run tests, measure coverage |
| [docs/build-setup.md](docs/build-setup.md) | Build machine setup, prerequisites, distro details, troubleshooting |
| [docs/hardware-setup.md](docs/hardware-setup.md) | Shopping list, assembly, flashing, first boot, troubleshooting |
| [README.md](README.md) | Quick start, build targets, doc index |

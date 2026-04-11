# RPi Home Monitor - Project Context

## What This Is

A self-hosted home security camera system (like Tapo/Ring but open-source, no cloud fees).
Built on Raspberry Pi hardware running **Home Monitor OS** — a custom Yocto Linux distribution.

## Architecture

- **RPi 4 Model B** = Home server. Receives camera streams, records 3-min clips, serves web dashboard.
- **RPi Zero 2W + PiHut ZeroCam** = Camera nodes. One per location. Streams video to server via RTSP.
- **Mobile Web UI** = Dashboard accessed from phone/laptop over HTTPS.

Two separate applications in `app/`:
- `app/server/` = Flask web app (monitor-server) — runs on RPi 4B
- `app/camera/` = Python streaming service (camera-streamer) — runs on Zero 2W

## Video Pipeline

```
Camera (V4L2) → FFmpeg (H.264 RTSP push)
    → MediaMTX (:8554) on server
        ├→ WebRTC (WHEP :8889) → browser <video> (sub-1s latency, live view)
        ├→ FFmpeg Record → /data/recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.mp4 (3-min clips)
        └→ FFmpeg Snap   → /data/live/<cam-id>/snapshot.jpg (every 30s)
        └→ FFmpeg HLS    → /data/live/<cam-id>/stream.m3u8 (fallback only)

Web browser → NGINX (:443 HTTPS)
    ├→ /webrtc/<cam-id>/ → proxy to MediaMTX :8889 (WHEP, primary live view)
    ├→ /live/<cam-id>/*  → HLS segments (fallback)
    ├→ /clips/<cam-id>/* → served direct from /data/recordings/
    └→ /api/*            → Flask (:5000)
```

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
- **TLS everywhere from Phase 1** — HTTPS for web, RTSPS (mTLS) for cameras (Phase 2)
- **LUKS encrypted /data partition** — recordings, config, certs all encrypted at rest
- **nftables firewall** — minimal open ports, cameras can only talk to server
- **swupdate A/B partitions** — atomic OTA with rollback
- **3-minute MP4 clips** (Tapo-style) — stored per camera per date
- **JSON files for data** (no database) — cameras.json, users.json, settings.json
- **Avahi/mDNS** for camera auto-discovery on LAN (`homemonitor.local`)
- **HLS** for live view in mobile browsers (HLS.js)
- **MediaMTX** as RTSP relay server (port 8554)
- **OS branding** — `/etc/os-release` shows "Home Monitor OS"

## Design Patterns (Mandatory)

Full rules in [`docs/development-guide.md`](docs/development-guide.md) Section 3.6.

**Patterns we follow:**
- **Single Responsibility** — one class per file, one concern per class. No god files (>300 lines).
- **Service Layer** — business logic in service classes (`CameraService`, `StorageService`), routes are thin HTTP adapters.
- **State Machine** — camera lifecycle as explicit states (`INIT → SETUP → CONNECTING → VALIDATING → RUNNING → SHUTDOWN`).
- **Platform Provider** — `camera/platform.py` provides all hardware paths. Never hardcode `/dev/video0`, `/sys/class/leds/ACT`, `wlan0`, `thermal_zone0`.
- **Strategy** — swappable backends via `typing.Protocol` (streaming, capture, detection, player).
- **Constructor Injection** — pass deps in `__init__()`. No DI frameworks, no global registries.
- **Fail-Silent Adapter** — all hardware access wrapped in try/except, fails gracefully.
- **App Factory** — Flask `create_app()` decomposed into `_init_infrastructure`, `_init_services`, `_startup`, `_register_blueprints`.
- **Repository** — `Store` class for JSON persistence with atomic writes.
- **Observer/Callback** — simple callback for cross-service notifications (e.g., StorageManager → StreamingService dir change).

**Patterns we NEVER use:**
- No DI containers, no event sourcing, no CQRS, no microservices, no plugin systems, no ORM.

**Live streaming:**
- WebRTC (MediaMTX WHEP) for live view (sub-1s). HLS as fallback only. Recordings stay FFmpeg→MP4.

## Phases

- **Phase 1:** Local-only. Single camera, live view, clip recording, web dashboard, auth, health, security, OTA-ready. **~95% complete.**
- **Phase 2:** Multi-camera, motion detection, notifications, cloud relay, mobile app, audio.
- **Phase 3:** AI/ML detection, zones, clip protection, smart home integration.

## Component Status Map

### Server App (`app/server/monitor/`)

| Component | File(s) | Status | What It Does |
|-----------|---------|--------|--------------|
| App factory | `__init__.py` | COMPLETE | Creates Flask app, decomposes into _init_infrastructure, _init_services, _startup, _register_blueprints |
| Auth/CSRF | `auth.py` | COMPLETE | bcrypt (cost 12), sessions (30min idle/24hr max), rate limit (5/min), CSRF tokens |
| Data models | `models.py` | COMPLETE | Camera, User, Settings, Clip dataclasses — no DB, JSON files |
| JSON store | `store.py` | COMPLETE | Thread-safe JSON persistence with atomic writes (cameras.json, users.json, settings.json) |
| Setup wizard | `provisioning.py` | COMPLETE | WiFi scan → save creds → admin password → apply all at once (PR #11 fix) |
| Page routes | `views.py` | COMPLETE | /setup, /login, /dashboard, /live, /recordings, /settings — all auth-gated |
| Camera API | `api/cameras.py` | COMPLETE | Thin routes → delegates to CameraService |
| Camera svc | `services/camera_service.py` | COMPLETE | Camera CRUD, lifecycle, streaming coordination, audit |
| Auth API | `api/auth.py` (in __init__) | COMPLETE | Login/logout/me |
| Live API | `api/live.py` | COMPLETE | `/live/<id>/stream.m3u8` (HLS playlist), `/live/<id>/snapshot` (JPEG) |
| Recordings API | `api/recordings.py` | COMPLETE | List clips, filter by date, get latest, delete clip |
| System API | `api/system.py` | COMPLETE | Health (CPU temp, RAM, disk), system info |
| Settings API | `api/settings.py` | COMPLETE | GET/PUT timezone, thresholds, hostname |
| Users API | `api/users.py` | COMPLETE | CRUD users, change password, role-based access |
| OTA API | `api/ota.py` | PARTIAL | Status endpoint works; upload/push endpoints are stubs |
| Streaming svc | `services/streaming.py` | COMPLETE | Manages FFmpeg pipelines: HLS + recorder + snapshots per camera |
| Recorder svc | `services/recorder.py` | COMPLETE | Clip metadata, listing, deletion (actual recording done by streaming svc) |
| Health svc | `services/health.py` | COMPLETE | CPU temp, RAM, disk, uptime. Warns at CPU>70C, disk>85%, RAM>90% |
| Audit svc | `services/audit.py` | COMPLETE | Append-only JSON audit log at /data/logs/audit.log |
| Discovery svc | `services/discovery.py` | PARTIAL | Camera online/offline tracking, pending camera reports |
| Storage svc | `services/storage.py` | COMPLETE | FIFO loop recording, background cleanup, USB/internal dir switching |
| USB svc | `services/usb.py` | COMPLETE | USB detection (lsblk), mount/unmount, format to ext4, auto-mount |
| Storage API | `api/storage.py` | COMPLETE | Thin routes → delegates to StorageService |
| Storage svc | `services/storage_service.py` | COMPLETE | USB select, format, eject orchestration with audit |
| Logging | `logging_config.py` | COMPLETE | RotatingFileHandler (10MB×5), console + file output |

### Server Templates (`app/server/monitor/templates/`)

| Template | Status | What It Does |
|----------|--------|--------------|
| `setup.html` | COMPLETE | 4-step wizard: Welcome → WiFi (save) → Admin password → Review & Apply |
| `login.html` | COMPLETE | Login form |
| `dashboard.html` | COMPLETE | Camera overview, system health, quick actions |
| `live.html` | COMPLETE | HLS.js video player, camera selector, snapshot button |
| `recordings.html` | COMPLETE | Calendar + timeline, clip playback |
| `settings.html` | COMPLETE | System settings, user management, OTA |
| `base.html` | COMPLETE | Base layout with nav |

### Server Config (`app/server/config/`)

| File | What It Does |
|------|--------------|
| `nginx-monitor.conf` | Reverse proxy (:80→:443→:5000), captive portal detection, HLS/clip serving |
| `nftables-server.conf` | Firewall: default DROP, allow private nets, rate-limit SSH |
| `monitor-hotspot.sh` | WiFi AP "HomeMonitor-Setup", waits for wlan0, retry logic, LED control |
| `monitor-hotspot.service` | Systemd: runs hotspot on boot if /data/.setup-done missing |
| `monitor.service` | Systemd: Flask app (python3 -m monitor) |
| `avahi-homemonitor.service` | Avahi static service file for mDNS (_homemonitor._tcp, _https._tcp) |
| `logrotate-monitor.conf` | Log rotation |
| `captive-portal-dnsmasq.conf` | DNS redirect to setup wizard during hotspot mode |

### Camera App (`app/camera/camera_streamer/`)

| Component | File | Status | What It Does |
|-----------|------|--------|--------------|
| Entry point | `main.py` | COMPLETE | Thin: config load → platform detect → CameraLifecycle.run() |
| Lifecycle | `lifecycle.py` | COMPLETE | State machine: INIT→SETUP→CONNECTING→VALIDATING→RUNNING→SHUTDOWN |
| Platform | `platform.py` | COMPLETE | Hardware abstraction — detects device paths, LED, thermal, WiFi interface |
| Config | `config.py` | COMPLETE | /data/config/camera.conf, auto-generates cam ID from hardware serial |
| V4L2 capture | `capture.py` | COMPLETE | Detects camera device, queries H.264 support, validates resolution |
| RTSP stream | `stream.py` | COMPLETE | FFmpeg v4l2→RTSP push to server:8554, exponential backoff reconnect |
| WiFi setup | `wifi_setup.py` | COMPLETE | "HomeCam-Setup" AP, HTTP server :80, first-boot setup wizard |
| Status server | `status_server.py` | COMPLETE | Post-setup status page with auth, WiFi change, system health |
| WiFi utils | `wifi.py` | COMPLETE | Shared WiFi operations: scan, connect, hotspot start/stop |
| LED control | `led.py` | COMPLETE | LedController class — patterns via sysfs, injectable path, fail-silent |
| Discovery | `discovery.py` | PARTIAL | Avahi _rtsp._tcp advertisement with TXT records |
| Health | `health.py` | COMPLETE | CPU temp, memory, uptime, watchdog — injectable thermal path |
| Pairing | `pairing.py` | STUB | mTLS cert exchange — Phase 2 |
| OTA agent | `ota_agent.py` | STUB | Update listener — Phase 2 |

### Yocto Layer (`meta-home-monitor/`)

| Component | Path | Status |
|-----------|------|--------|
| Distro config | `conf/distro/home-monitor.conf` | COMPLETE — systemd, usrmerge, version pins (kernel 6.6, python 3.12, openssl 3.5) |
| Server image (dev) | `recipes-core/images/home-monitor-image-dev.bb` | COMPLETE |
| Server image (prod) | `recipes-core/images/home-monitor-image-prod.bb` | COMPLETE |
| Camera image (dev) | `recipes-core/images/home-camera-image-dev.bb` | COMPLETE |
| Camera image (prod) | `recipes-core/images/home-camera-image-prod.bb` | COMPLETE |
| Server recipe | `recipes-monitor/monitor-server/monitor-server_1.0.bb` | COMPLETE |
| Camera recipe | `recipes-camera/camera-streamer/camera-streamer_1.0.bb` | COMPLETE |
| MediaMTX recipe | `recipes-multimedia/mediamtx/mediamtx_1.11.3.bb` | COMPLETE |
| TLS certs recipe | `recipes-security/monitor-certs/monitor-certs_1.0.bb` | COMPLETE |
| First boot | `recipes-core/first-boot/first-boot_1.0.bb` | COMPLETE — hostname, certs, LUKS setup |
| Packagegroup: base | `recipes-core/packagegroups/packagegroup-monitor-base.bb` | COMPLETE |
| Packagegroup: web | `recipes-core/packagegroups/packagegroup-monitor-web.bb` | COMPLETE |
| Packagegroup: video | `recipes-core/packagegroups/packagegroup-monitor-video.bb` | COMPLETE |
| Packagegroup: security | `recipes-core/packagegroups/packagegroup-monitor-security.bb` | COMPLETE |
| Packagegroup: camera-video | `recipes-core/packagegroups/packagegroup-camera-video.bb` | COMPLETE |
| WKS (partitions) | `wic/home-monitor-ab.wks` | COMPLETE — A/B + data partition |

## Network Ports

| Port | Service | Device | Purpose |
|------|---------|--------|---------|
| 80 | NGINX | Server | HTTP → setup wizard / HTTPS redirect |
| 443 | NGINX | Server | HTTPS web dashboard |
| 5000 | Flask | Server | App (loopback, proxied by NGINX) |
| 8554 | MediaMTX | Server | RTSP camera stream input |
| 8889 | MediaMTX | Server | WebRTC WHEP signaling (proxied by NGINX) |
| 8189/udp | MediaMTX | Server | WebRTC ICE media (direct browser→server) |
| 22 | SSH | Both | Admin (dev images only) |
| 80 | Flask | Camera | Setup wizard (first boot only) |
| 5353 | Avahi | Both | mDNS discovery |

## Device File Paths

| Path | Purpose | Persists OTA? |
|------|---------|---------------|
| `/opt/monitor/` | Server app code | No (rebuilt) |
| `/opt/camera/` | Camera app code | No (rebuilt) |
| `/data/` | All persistent state (LUKS encrypted) | Yes |
| `/data/config/` | cameras.json, users.json, settings.json | Yes |
| `/data/certs/` | TLS certs (server.crt, ca.crt) | Yes |
| `/data/recordings/<cam-id>/YYYY-MM-DD/` | 3-min MP4 clips + thumbnails | Yes |
| `/data/live/<cam-id>/` | HLS segments (ephemeral) | No |
| `/data/logs/audit.log` | Security audit log | Yes |
| `/data/.setup-done` | Setup completion stamp | Yes |
| `/data/.secret_key` | Flask session secret | Yes |

## Key Constants

| Setting | Value | Where |
|---------|-------|-------|
| Hotspot SSID (server) | `HomeMonitor-Setup` | monitor-hotspot.sh |
| Hotspot SSID (camera) | `HomeCam-Setup` | wifi_setup.py |
| Hotspot password | `homemonitor` / `homecamera` | scripts |
| Default admin | `admin` / `admin` | __init__.py |
| Session timeout | 30min idle / 24hr absolute | auth.py |
| Bcrypt cost | 12 | auth.py |
| Rate limit (login) | 5 attempts/60s | auth.py |
| HLS segment | 2s, rolling 5 | streaming.py |
| Clip duration | 180s (3 min) | streaming.py |
| Snapshot interval | 30s | streaming.py |
| Camera offline timeout | 30s | discovery.py |
| Storage cleanup threshold | 90% disk | storage.py |
| CPU temp warning | >70C | health.py |

## Tests

- **Server:** 549 tests, 84.5% coverage (`python -m pytest app/server/tests/ -v`)
- **Camera:** 255 tests, 70.6% coverage (`python -m pytest app/camera/tests/ -v`)
- **Total:** 804 tests

## PR History

| PR | Title | Status |
|----|-------|--------|
| #11 | Fix setup wizard disconnect — collect settings before WiFi connect | Merged |
| #10 | mDNS server discovery (homemonitor.local via Avahi) | Merged |
| #9 | Fix hotspot startup race condition — WiFi readiness + retry | Merged |
| #8 | Captive portal auto-popup + LED status feedback | Merged |
| #7 | NGINX mp4, Flask watchdog, fstab cleanup, boot ordering | Merged |

## What's NOT Done Yet (Stubs/Phase 2)

- `pairing.py` — mTLS certificate exchange (STUB)
- `ota_agent.py` — Camera OTA update listener (STUB)
- OTA server upload/push endpoints (PARTIAL)
- RTSPS (currently plaintext RTSP between camera→server)
- Motion detection recording mode
- Multi-camera (framework exists, untested with real hardware)
- Cloud relay, mobile app, AI/ML (Phase 2-3)

## ⚠️ MANDATORY: Development Rules

**Before making ANY changes, read [`docs/development-guide.md`](docs/development-guide.md).**

**Key rules:**
1. Never commit directly to `main` — use feature branches + PRs
2. Never put distro policy in `local.conf` — use `home-monitor.conf`
3. Never add packages directly to image recipes — use packagegroups
4. Never store secrets in code or config files
5. Always verify Yocto changes parse: `bitbake -p` before committing
6. Branch naming: `feature/`, `fix/`, `recipe/`, `docs/`, `release/`

## Development Workflow

**Fast iteration (app changes only — NO rebuild needed):**
```bash
rsync -av app/server/monitor/ root@homemonitor.local:/opt/monitor/monitor/
ssh root@homemonitor.local systemctl restart monitor
```

**Full image rebuild (OS/package changes):**
```bash
./scripts/build.sh server-dev   # or camera-dev
```

## Yocto Build Notes

- Always use `./scripts/build.sh` — sets correct MACHINE, build dir, local.conf
- Server builds: `build/` dir → `build/tmp-glibc/deploy/images/raspberrypi4-64/`
- Camera builds: `build-zero2w/` dir → `build-zero2w/tmp-glibc/deploy/images/raspberrypi0-2w-64/`
- Custom distro uses `tmp-glibc/` NOT `tmp/` (old poky builds left stale files in `tmp/`)
- Always run builds in `tmux` on the VM
- Never manually run `bitbake` with `MACHINE=` env overrides
- Recipe LICENSE fields use MIT (Yocto convention) — project is AGPL-3.0

## Build VM

Set up your own build VM following [docs/build-setup.md](docs/build-setup.md).
Never commit VM credentials to the repo.

## Documentation

| Document | Purpose |
|----------|---------|
| [docs/requirements.md](docs/requirements.md) | User needs, software requirements, security requirements, API spec |
| [docs/architecture.md](docs/architecture.md) | Software architecture, security design, threat model, data model |
| [docs/development-guide.md](docs/development-guide.md) | **Mandatory rules** for all development |
| [docs/testing-guide.md](docs/testing-guide.md) | How to write tests, run tests, measure coverage |
| [docs/build-setup.md](docs/build-setup.md) | Build machine setup, prerequisites, troubleshooting |
| [docs/hardware-setup.md](docs/hardware-setup.md) | Shopping list, assembly, flashing, first boot |
| [docs/adr/](docs/adr/) | Architecture Decision Records (6 ADRs) |
| [CHANGELOG.md](CHANGELOG.md) | Release notes + detailed setup walkthrough |
| [README.md](README.md) | Quick start, build targets, doc index |

## Tooling

| Tool | Config | Purpose |
|------|--------|---------|
| ruff | `pyproject.toml` | Linting + formatting (replaces flake8/black/isort) |
| pre-commit | `.pre-commit-config.yaml` | Git hooks: lint, format, trailing whitespace, no main commits |
| GitHub Actions | `.github/workflows/test.yml` | CI: lint + server tests + camera tests on every push/PR |
| CODEOWNERS | `.github/CODEOWNERS` | Auto-assign reviewers for PRs |

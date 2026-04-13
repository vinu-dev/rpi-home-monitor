# RPi Home Monitor - Requirements Specification

Version: 1.0
Date: 2026-04-13

---

## 1. Product Overview

### 1.1 Vision

A self-hosted home security camera system using Raspberry Pi hardware. The system provides live video monitoring, automatic clip recording, and a mobile-friendly web dashboard — without relying on cloud subscriptions or vendor ecosystems.

### 1.2 System Concept

```
                          Home WiFi Network
                    ┌──────────────────────────┐
                    │                          │
   ┌────────────┐   │   ┌──────────────────┐   │   ┌─────────────┐
   │ Zero 2W    │───┼──>│  RPi 4B Server   │<──┼───│ Phone/      │
   │ + ZeroCam  │ RTSP  │                  │ HTTP  │ Laptop      │
   │ (camera 1) │   │   │  - Receives      │   │   │ (web UI)    │
   └────────────┘   │   │    streams       │   │   └─────────────┘
                    │   │  - Records clips  │   │
   ┌────────────┐   │   │  - Serves web UI  │   │
   │ Zero 2W    │───┼──>│  - Manages cams   │   │
   │ + ZeroCam  │ RTSP  │                  │   │
   │ (camera 2) │   │   │  Storage:        │   │
   └────────────┘   │   │  SD + USB disk   │   │
                    │   └──────────────────┘   │
   ┌────────────┐   │            │             │
   │ Camera N   │───┘            │             │
   └────────────┘       (Phase 2: cloud relay  │
                         for remote access)    │
                    └──────────────────────────┘
```

### 1.3 Comparison to Existing Products

The system aims to provide a similar experience to TP-Link Tapo or Ring cameras:
- Auto-segmented clip recordings (like Tapo's 3-minute clips)
- Live view from mobile
- Timeline playback of past recordings
- Loop recording when storage is full
- Camera health/status visibility

**Key differentiator:** Fully self-hosted, open-source, no monthly fees, no vendor cloud.

---

## 2. Phased Delivery

### Phase 1 — Local Foundation (Current)

- Single camera node (Zero 2W + ZeroCam)
- RPi 4B server with live view and clip recording
- Mobile web dashboard with authentication (HTMX + Alpine.js)
- System health monitoring
- OTA updates with SWUpdate A/B partitions and Ed25519 signing (ADR-0008)
- SD card and USB external disk storage with loop recording
- Ethernet + WiFi support on server
- mTLS camera authentication with pairing protocol (ADR-0009)
- LUKS2 encryption at rest on `/data` partition (ADR-0010)
- WiFi provisioning via captive portal setup wizard (server and camera)
- Tailscale VPN for remote access
- Factory reset support

### Phase 2 — Multi-Camera & Remote Access

- Multiple camera nodes (auto-discovered on network)
- Motion detection (triggers recording, flags events)
- Push notifications (motion alerts via email/Telegram/push)
- Cloud relay server for remote access outside home WiFi
- Mobile app (Android + iOS)
- Audio support (for cameras with microphones)

### Phase 3 — Intelligence & Integration

- AI/ML: person detection, face recognition, object classification
- Detection zones (ignore specific areas)
- Clip protection (star/lock important recordings)
- Smart home integration (Home Assistant, Google Home, Alexa, ONVIF)
- Advanced user management

---

## 3. User Needs

### UN-01: Live Camera View

As a homeowner, I want to open the dashboard on my phone and see what each camera sees right now, so I can check on my home at any time.

**Acceptance criteria:**
- Live video stream visible within 3 seconds of opening the page
- Stream resolution up to 1080p @ 25fps
- Configurable quality (720p/1080p, adjustable FPS)
- Works on mobile browsers (Chrome, Safari) over WiFi

### UN-02: Recorded Clip Playback

As a homeowner, I want to browse and play back past recordings, so I can see what happened while I was away.

**Acceptance criteria:**
- Recordings stored as 3-minute MP4 clips
- Browsable by camera and by date/time
- Playable directly in the mobile browser
- Timeline view showing available clips
- Direct/continuous video stream available alongside clips (not stored long-term)

### UN-03: Automatic Storage Management

As a homeowner, I want the system to manage storage automatically, so I never have to manually delete old recordings.

**Acceptance criteria:**
- Loop recording: when storage reaches threshold (e.g., 90%), oldest clips are deleted
- Dashboard shows storage usage (used/free/total)
- Configurable retention: maximum days or maximum storage percentage
- Works with SD card and USB external disks

### UN-04: Secure Access

As a homeowner, I want the dashboard to require login, so that neighbors or visitors on my WiFi can't see my cameras.

**Acceptance criteria:**
- Login required to access any page or API
- Admin role: full control (add/remove cameras, manage users, configure settings, delete clips)
- Viewer role: can watch live and recorded video, cannot change settings
- Passwords stored securely (hashed, never plaintext)
- Session timeout after inactivity

### UN-05: System Health Visibility

As a homeowner, I want to see if the system is healthy, so I know if a camera goes offline or the server is running low on resources.

**Acceptance criteria:**
- Dashboard shows per-camera status: online/offline, uptime, last seen
- Server health: CPU temperature, CPU usage, RAM usage, disk usage, network status
- Camera health: connection status, stream FPS, uptime

### UN-06: Camera Auto-Discovery

As a homeowner, I want new cameras to appear automatically when I plug them in and connect them to WiFi, so I don't have to manually enter IP addresses.

**Acceptance criteria:**
- Camera nodes advertise themselves on the network (mDNS/Avahi)
- Server discovers and lists new cameras automatically
- User confirms/names the camera from the dashboard before it becomes active
- Camera can be removed/renamed from the dashboard

### UN-07: Easy Setup

As a homeowner, I want to flash an SD card, plug in the device, connect to WiFi, and have it working, with minimal manual configuration.

**Acceptance criteria:**
- Server: flash SD, plug in, connect to `HomeMonitor-Setup` WiFi hotspot, complete captive portal setup wizard (WiFi, admin account, timezone), access dashboard
- Camera: flash SD, plug in, connect to `HomeCam-Setup` WiFi hotspot, complete captive portal setup wizard (WiFi, server address, camera credentials), camera auto-connects to server
- First-boot setup wizard for initial admin account creation, WiFi configuration, and timezone

### UN-08: Over-the-Air Updates

As a homeowner, I want to update the software on my server and cameras without physically removing SD cards.

**Acceptance criteria:**
- Server can be updated from the dashboard (upload new image or pull from URL)
- Camera updates pushed from server
- Dual-partition A/B scheme: failed update rolls back to previous working version
- Update status visible in dashboard

---

## 4. Design Requirements

### 4.1 Hardware

| Component | Specification |
|-----------|---------------|
| Server | Raspberry Pi 4 Model B (2GB+ RAM) |
| Camera | Raspberry Pi Zero 2W + PiHut ZeroCam |
| Camera power | USB 5V adapter per camera |
| Server power | USB-C 5V/3A, always-on |
| Server storage | SD card (32GB+), future: USB SSD/HDD |
| Server network | WiFi (bcm43455) + Ethernet (gigabit) |
| Camera network | WiFi (bcm43436s) |

### 4.2 Software Platform

| Item | Choice | Rationale |
|------|--------|-----------|
| OS | Home Monitor OS (custom Yocto distro) | Product-specific, not reference poky |
| Yocto release | Scarthgap (5.0 LTS) | Long-term support, latest stable |
| Init system | systemd | Service management, journald logging |
| Kernel | linux-raspberrypi 6.6.x (aarch64) | RPi Foundation kernel, 64-bit, pinned |
| Package format | deb | Easy on-device package management |
| Image variants | dev (debug) + prod (hardened) | Separate concerns, safe production |
| Boot loader | U-Boot (`u-boot-rpi`) | Boot counting, A/B slot management, `fw_printenv`/`fw_setenv` |
| OTA framework | SWUpdate (A/B partitions) | Atomic updates with rollback; file-level handlers for app-only updates |

### 4.3 Video Pipeline

| Item | Choice | Rationale |
|------|--------|-----------|
| Camera capture | v4l2 (h264 from hardware encoder) | Zero CPU encode on Zero 2W |
| Transport | RTSPS (mTLS) with RTSP fallback | Encrypted stream delivery over WiFi, mutual TLS authentication |
| RTSP relay | MediaMTX (:8554) | Single stream hub — camera pushes, consumers read |
| Streaming tool | ffmpeg | Proven, flexible, available in Yocto |
| Recording format | MP4 (3-minute segments) | Browser-native playback, small files |
| Live view in browser | WebRTC (WHEP) with HLS.js fallback | Sub-second latency, works on mobile Safari/Chrome |

### 4.4 Web Application

| Item | Choice | Rationale |
|------|--------|-----------|
| Backend | Python 3 + Flask | Simple, already in Yocto, easy to extend |
| Frontend | HTMX + Alpine.js, mobile-first dark theme (ADR-0012) | Server-driven UI, no build tools, works everywhere |
| Reverse proxy | nginx | Serves video files efficiently, proxies Flask and MediaMTX |
| Authentication | Flask session-based (bcrypt hashed passwords) | Simple, secure for local network |
| Video playback | WebRTC (WHEP) primary with HLS.js fallback | Sub-second live view, broad mobile support |

### 4.5 Network Architecture

```
Camera Node                    Server                          Client
┌─────────────┐   RTSPS/mTLS  ┌───────────────────┐  HTTPS    ┌────────┐
│ ffmpeg       │──────────────>│ MediaMTX (:8554)  │  :443    │ Phone  │
│ v4l2 → h264  │               │  RTSP relay       │<─────────│ browser│
│              │    mDNS       │  ├─ WebRTC WHEP   │──────────>│        │
│ avahi-daemon │<─────────────>│  │   (:8889)      │          │ Web UI │
│              │               │  ├─ FFmpeg record  │          │        │
│ camera-      │               │  └─ FFmpeg snap    │          │        │
│  streamer    │               │                   │          └────────┘
│  .service    │               │ nginx (:443 HTTPS)│
└─────────────┘               │  ├─ Flask (:5000)  │
                               │  ├─ /webrtc/ proxy│
                               │  └─ /clips/ files │
                               │                   │
                               │ avahi-daemon       │
                               │ monitor.service    │
                               └───────────────────┘
```

### 4.6 Storage Layout

**Server (RPi 4B):**
```
/
├── /boot               # U-Boot, kernel, DTBs, config.txt, U-Boot env (partition 1, vfat)
├── /                   # Root filesystem A (partition 2, ext4)
├── /                   # Root filesystem B (partition 3, ext4, for OTA)
└── /data               # Persistent data partition (partition 4, LUKS → ext4 in production)
    ├── /recordings     # 3-min MP4 clips, organized by camera/date
    │   └── /<cam-id>/
    │       └── /YYYY-MM-DD/
    │           ├── 14-00-00.mp4
    │           ├── 14-03-00.mp4
    │           └── ...
    ├── /live           # Live HLS segments + snapshot JPEGs per camera
    ├── /config         # App config, user database, camera registry
    ├── /logs           # Persistent application logs
    ├── /certs
    │   └── /cameras    # Paired camera certs (client.crt, client.key, ca.crt, revoked/)
    ├── /tailscale      # Tailscale VPN state
    ├── /network        # Persisted WiFi configuration
    └── /ota            # OTA staging, inbox, history
```

**Camera (Zero 2W):**
```
/
├── /boot               # U-Boot, kernel, DTBs, config.txt, U-Boot env (partition 1, vfat)
├── /                   # Root filesystem A (partition 2, ext4)
├── /                   # Root filesystem B (partition 3, ext4, for OTA)
└── /data               # Persistent config (partition 4, LUKS → ext4 in production)
    ├── /config         # camera.conf, WiFi credentials
    ├── /certs          # client.crt, client.key, ca.crt (from pairing, ADR-0009)
    ├── /logs           # Persistent application logs
    └── /ota            # OTA inbox, staging, history (ADR-0008)
```

### 4.7 Partition Scheme (OTA-ready, SWUpdate A/B with U-Boot)

> **Status: Implemented.** A/B partition layout defined in WKS files with U-Boot boot counting (see ADR-0008).

| Partition | Type | Size (Server) | Size (Camera) | Purpose |
|-----------|------|---------------|---------------|---------|
| boot | vfat | 512 MB | 512 MB | U-Boot, kernel, DTBs, config.txt, U-Boot env |
| rootfsA | ext4 | 8 GB | 8 GB | Active root filesystem |
| rootfsB | ext4 | 8 GB | 8 GB | Standby root (OTA target) |
| data | ext4 / LUKS | Remaining (~47 GB on 64 GB card) | Remaining (~47 GB on 64 GB card) | Persistent data, recordings, config, certs (LUKS in production) |

Boot uses U-Boot (`u-boot-rpi` from meta-raspberrypi) for boot counting (`bootlimit=3`, `altbootcmd`) and `fw_printenv`/`fw_setenv` for A/B slot management. See ADR-0008 for full details.

### 4.8 Performance Targets

| Metric | Target |
|--------|--------|
| Live view latency | < 3 seconds from camera to browser |
| Clip segment duration | 3 minutes |
| Max cameras (Phase 1) | 1 |
| Max cameras (Phase 2+) | 8+ |
| Dashboard page load | < 2 seconds |
| Server boot to operational | < 60 seconds |
| Camera boot to streaming | < 45 seconds |

---

## 5. Software Requirements

### 5.1 Camera Node (Zero 2W)

#### SR-CAM-01: Video Capture

- Capture video from `/dev/video0` using v4l2 hardware H.264 encoder
- Default resolution: 1920x1080 @ 25fps
- Configurable via `/data/config/camera.conf`: resolution (720p/1080p), FPS (15/25)
- Start capture automatically on boot via systemd service

#### SR-CAM-02: RTSP Streaming

- Stream captured video to server via RTSP over TCP
- Server address configured in `/data/config/camera.conf`
- Auto-reconnect if server connection drops (exponential backoff, max 60s)
- Log stream status to journald

#### SR-CAM-03: Network Discovery

- Run avahi-daemon to advertise `_rtsp._tcp` service on local network
- Service TXT record includes: camera ID (hostname), resolution, firmware version
- Camera ID derived from hardware serial number (unique per device)

#### SR-CAM-04: WiFi Configuration

- NetworkManager for WiFi management
- First-boot: if no WiFi configured, start hotspot `HomeCam-Setup` (password: `homecamera`) with captive portal
- Setup wizard collects: WiFi SSID/password, server address, camera admin username/password
- Persist WiFi credentials and admin credentials to `/data/config/camera.conf` (survives OTA updates)
- Post-setup: camera hostname set to `rpi-divinu-cam-XXXX` (last 4 hex of CPU serial), advertised via mDNS

#### SR-CAM-05: Camera Local Authentication

- Camera status page (port 80) requires login with credentials set during provisioning
- Password hashing: PBKDF2-SHA256, 100,000 iterations, random 16-byte salt
- Session management: in-memory sessions, `cam_session` HttpOnly cookie, 2-hour timeout with activity refresh
- Authenticated endpoints: `/` (status), `/api/status`, `/api/networks`, `/api/wifi`, `/api/password`
- Public endpoints: `/login` (GET/POST), `/logout`
- Status page shows: device info, WiFi status, server connection, CPU temp, memory, uptime
- Users can change WiFi network and admin password from the status page

#### SR-CAM-06: OTA Update Support

> **Status: Implemented.** Camera OTA agent at `app/camera/camera_streamer/ota_agent.py` (HTTP server on port 8080, mTLS). See ADR-0008.

- Dual rootfs partitions (A/B layout) using SWUpdate + U-Boot boot counting (`bootlimit=3`)
- Accept update images pushed from server over HTTPS (mTLS authenticated, ADR-0009)
- Two artifact types: `.swu` (full-system A/B rootfs) and `.tar.zst` + `.sig` (app-only)
- App-only updates use symlink swap (`/opt/camera/releases/<version>/` with `current` symlink), no reboot
- Automatic rollback if new rootfs fails health check within 90 seconds
- Report current firmware version to server via mDNS TXT record
- Ed25519 signature verification before any install (public key in rootfs)

#### SR-CAM-07: System Watchdog

- Enable hardware watchdog timer
- camera-streamer service restarts on failure (systemd `Restart=always`)
- If stream cannot connect to server for 5 minutes, log warning

### 5.2 Server (RPi 4B)

#### SR-SRV-01: RTSP Stream Receiver

- Accept incoming RTSP streams from camera nodes
- One ffmpeg process per active camera
- Convert incoming RTSP to:
  - HLS segments for live browser playback (`.m3u8` + `.ts` segments, 2s duration)
  - MP4 clips for recording (3-minute segments)
- Handle camera disconnect/reconnect gracefully

#### SR-SRV-02: Recording Engine

- Segment recordings into 3-minute MP4 files
- File naming: `/data/recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.mp4`
- Generate thumbnail JPEG for each clip (extract frame at 1s mark)
- Recording modes (configurable per camera):
  - **Continuous**: always record
  - **Off**: stream only, no recording
  - (Phase 2: **Motion-triggered**: record on motion detection)

#### SR-SRV-03: Storage Management

- Monitor storage usage on data partition
- When usage exceeds 90% threshold: delete oldest clips across all cameras
- Never delete clips less than 24 hours old (even if over threshold — stop recording instead)
- Expose storage stats via API: total, used, free, oldest clip date, clip count per camera
- Configurable threshold percentage via settings

#### SR-SRV-04: Camera Discovery & Management

- Run avahi-daemon and browse for `_rtsp._tcp` services
- Display discovered cameras in dashboard as "pending" until user confirms
- For each confirmed camera, store in `/data/config/cameras.json`:
  - Camera ID, user-assigned name, location label, RTSP URL, recording mode
- Detect camera offline (no mDNS response for 30s) and update status
- API endpoints for: list cameras, add, remove, rename, get status

#### SR-SRV-05: Web Dashboard — Live View

- Display live video from each active camera in the browser
- Use HLS.js for playback (compatible with mobile Safari and Chrome)
- Multi-camera grid view (auto-layout based on camera count)
- Tap a camera to go full-screen
- Overlay: camera name, timestamp, recording indicator, online/offline badge

#### SR-SRV-06: Web Dashboard — Clip Playback

- Browse recordings by camera and date
- Timeline view showing clip availability (colored blocks on a 24h timeline)
- Play clips directly in browser (`<video>` tag, MP4 native playback)
- Navigation: previous/next clip, jump to time
- Thumbnail preview for each clip

#### SR-SRV-07: Web Dashboard — System Health

- Display for each camera: status (online/offline), uptime, current FPS, last seen timestamp
- Display for server: CPU temperature, CPU usage (%), RAM usage (%), disk usage (bar + numbers), uptime, network interfaces (IP, status)
- Auto-refresh every 10 seconds
- Warning indicators when thresholds exceeded (CPU temp > 70C, disk > 85%)

#### SR-SRV-08: Authentication & Authorization

- Login page required before accessing any route
- Two roles:
  - **Admin**: full access — configure cameras, manage users, change settings, delete clips, trigger OTA updates
  - **Viewer**: read-only — live view, clip playback, system health view
- First-boot setup wizard: create initial admin account, set timezone
- Password storage: bcrypt hashed, stored in `/data/config/users.json`
- Session-based auth with configurable timeout (default: 30 minutes)
- API endpoints: login, logout, create user (admin only), delete user (admin only), change password

#### SR-SRV-09: Settings

- Configurable via web dashboard (admin only):
  - Timezone (default: Europe/Dublin)
  - Per-camera: recording mode, resolution preference, name, location
  - Storage: retention threshold percentage, max retention days
  - Network: server hostname
- Settings persisted to `/data/config/settings.json`
- Settings survive OTA updates (on data partition)

#### SR-SRV-10: OTA Update Management

> **Status: Implemented.** Server OTA service at `app/server/monitor/services/ota_service.py` with API at `app/server/monitor/api/ota.py`. See ADR-0008.

- Dual rootfs partitions (A/B layout) using SWUpdate + U-Boot boot counting (`bootlimit=3`)
- **Multi-mode delivery** (5 modes, single `inbox → verify → staging → install` pipeline):
  - USB drive (auto-detected via udev, `*.swu`/`*.tar.zst` copied from root)
  - Manual upload via dashboard (`POST /api/v1/ota/server/upload`)
  - Server-mediated camera push (`POST /api/v1/ota/camera/<id>/push`, HTTPS + mTLS)
  - SSH/SCP direct copy to inbox (dev builds only)
  - Future: Suricatta polling from repository URL
- **Two artifact types:**
  - `.swu` — full-system A/B rootfs update (requires reboot)
  - `.tar.zst` + detached `.sig` — app-only update (symlink swap, no reboot)
- Ed25519 signature verification before any install (source is never trust)
- Artifact naming: `hm-<target>-<type>-<version>.<ext>` (e.g., `hm-server-system-1.2.0.swu`)
- Update status tracking: idle, downloading, verifying, staging, installing, rebooting, confirming, success, failed, rolled-back
- Automatic rollback on failed boot (3-attempt threshold) or failed health check (90s window)
- Space budget: inbox 2 GB, staging 500 MB, history retains last 2 successful versions

#### SR-SRV-11: Nginx Configuration

- Port 443 (HTTPS): reverse proxy to Flask app (port 5000)
- Self-signed TLS certificate (generated by local CA on first boot)
- `/live/<cam-id>/`: serve HLS segments for live view
- `/clips/<cam-id>/`: serve MP4 clips directly (nginx `mp4` module, byte-range support)
- `/snapshots/<cam-id>/`: serve thumbnail JPEGs
- `client_max_body_size`: 500MB (for OTA image uploads)
- Gzip compression for HTML/CSS/JS
- Rate limiting on `/api/v1/auth/login` (5 requests/minute per IP)

#### SR-SRV-12: REST API

All endpoints require authentication. Prefix: `/api/v1/`

**Auth:**
- `POST /auth/login` — authenticate, return session
- `POST /auth/logout` — end session
- `GET /auth/me` — current user info

**Cameras:**
- `GET /cameras` — list all cameras (confirmed + pending)
- `POST /cameras/<id>/confirm` — confirm a discovered camera (admin)
- `PUT /cameras/<id>` — update name, location, recording mode (admin)
- `DELETE /cameras/<id>` — remove camera (admin)
- `GET /cameras/<id>/status` — live status (online, fps, uptime)
- `POST /cameras/<id>/pair` — initiate pairing for a discovered camera (admin)
- `POST /cameras/<id>/unpair` — unpair and revoke camera certificate (admin)

**Pairing:**
- `POST /pair/register` — camera registers itself during pairing
- `POST /pair/exchange` — camera exchanges PIN for certificates and keys

**Recordings:**
- `GET /recordings/<cam-id>?date=YYYY-MM-DD` — list clips for a camera on a date
- `GET /recordings/<cam-id>/timeline?date=YYYY-MM-DD` — timeline data (start/end times of available clips)
- `GET /recordings/<cam-id>/latest` — most recent clip
- `DELETE /recordings/<cam-id>/<filename>` — delete a clip (admin)

**Live:**
- `GET /live/<cam-id>/stream.m3u8` — HLS playlist for live view
- `GET /live/<cam-id>/snapshot` — current frame as JPEG

**Storage:**
- `GET /storage/status` — storage usage stats
- `GET /storage/devices` — list available storage devices (SD, USB)
- `POST /storage/select` — select active storage device (admin)
- `POST /storage/format` — format a storage device (admin)
- `POST /storage/eject` — safely eject USB storage device (admin)

**System:**
- `GET /system/health` — server health (CPU, RAM, disk, temp)
- `GET /system/storage` — storage breakdown
- `GET /system/info` — firmware version, uptime, hostname
- `GET /system/tailscale` — Tailscale VPN connection status
- `POST /system/tailscale/connect` — connect to Tailscale network (admin)
- `POST /system/tailscale/disconnect` — disconnect from Tailscale (admin)
- `POST /system/factory-reset` — factory reset the device (admin)

**Settings:**
- `GET /settings` — current settings
- `PUT /settings` — update settings (admin)
- `GET /settings/wifi` — current WiFi configuration
- `POST /settings/wifi` — update WiFi settings (admin)

**Users:**
- `GET /users` — list users (admin)
- `POST /users` — create user (admin)
- `DELETE /users/<id>` — delete user (admin)
- `PUT /users/<id>/password` — change password (admin or self)

**OTA:**
- `POST /ota/server/upload` — upload update image for server (admin)
- `POST /ota/camera/<id>/push` — push update to camera (admin)
- `GET /ota/status` — update status for all devices
- `GET /ota/usb/scan` — scan USB devices for OTA update files (admin)
- `POST /ota/usb/import` — import OTA update from USB device (admin)

**Setup (unauthenticated, first-boot only):**
- `GET /setup/status` — setup completion status
- `GET /setup/wifi/scan` — scan for available WiFi networks
- `POST /setup/wifi/save` — save WiFi configuration
- `POST /setup/admin` — create initial admin account
- `POST /setup/complete` — finalize first-boot setup

---

## 5.3 Security Requirements

#### SR-SEC-01: TLS on All Connections

> **Status: Implemented.** HTTPS, RTSPS, and mTLS all operational. See ADR-0009.

- HTTPS (TLS 1.3) for all browser-to-server traffic (port 443)
- RTSPS (RTSP over TLS) for all camera-to-server streams
- Self-signed CA (ECDSA P-256, 10-year validity) generated on server first boot
- Server TLS certificate signed by local CA (5-year validity, auto-renewal via systemd timer)
- Camera client certificates signed by CA during pairing (ECDSA P-256, 5-year validity)
- No plaintext HTTP or RTSP permitted in production

#### SR-SEC-02: Mutual TLS for Camera Authentication

> **Status: Implemented.** PairingService on server + PairingManager on camera. See ADR-0009.

- Each paired camera receives a unique ECDSA P-256 client certificate (5-year validity)
- Server verifies camera cert on every RTSP connection
- Unpaired/unknown cameras cannot stream to server
- Camera removal revokes the client certificate (moved to `cameras/revoked/`, in-memory revocation set)
- Certificate serial numbers tracked in `cameras.json` as `cert_serial`
- Same certs used for RTSPS, OTA push authentication, and health polling (single trust chain)

#### SR-SEC-03: Encryption at Rest

> **Status: Implemented.** LUKS2 with Adiantum cipher on `/data` partition. See ADR-0010.

- `/data` partition encrypted with LUKS2 (`xchacha20,aes-adiantum-plain64`) — 2-3.5x faster than AES on ARM without hardware acceleration
- **Server:** passphrase set during first-boot setup wizard, argon2id KDF (1 GB memory, 4 iterations, 4 parallelism). Optional auto-unlock keyfile or Dropbear SSH unlock for headless operation
- **Camera:** key derived via HKDF-SHA256 from `pairing_secret` (ADR-0009) + CPU serial, argon2id KDF (64 MB memory, 4 iterations, 1 parallelism). Auto-unlock keyfile in initramfs
- Protects: recordings, WiFi credentials, user database, certificates, CA private key
- SD card theft yields no usable data without the passphrase (server) or pairing secret (camera)
- Dev builds skip encryption for faster iteration (plain ext4 on `/data`)

#### SR-SEC-04: Firewall (nftables)

- **Server:** Accept HTTPS (443) from LAN, RTSPS (8554) from paired camera IPs only, SSH (22) rate-limited from LAN. Drop everything else.
- **Camera:** Accept SSH from server IP only, OTA push from server IP only. Outbound: RTSPS to server only, DNS, DHCP, NTP, mDNS. Drop everything else.
- Camera IPs dynamically added to firewall set when paired
- All dropped packets logged

#### SR-SEC-05: No Default Credentials

- Production images built without `debug-tweaks` (no root password)
- Root SSH disabled in production; key-only SSH for a non-root service account
- First-boot wizard forces admin account creation before any access
- Development images retain debug-tweaks (separate build target)

#### SR-SEC-06: Secure Session Management

- Session cookies: `Secure`, `HttpOnly`, `SameSite=Strict`
- CSRF tokens on all state-changing requests (POST/PUT/DELETE)
- Session timeout: 30 minutes idle, 24 hours absolute
- Maximum 3 concurrent sessions per user
- Session invalidated on password change

#### SR-SEC-07: Rate Limiting

- Login endpoint: 5 attempts per minute per IP
- After 10 failed logins from same IP: block for 15 minutes
- API endpoints: 60 requests per minute per authenticated user
- OTA upload: 1 request per minute

#### SR-SEC-08: Audit Logging

- All security events logged to `/data/logs/audit.log`
- Events: login success/failure, session creation/expiry, camera pair/remove/offline, user CRUD, settings changes, clip deletion, OTA actions, firewall blocks
- Log format: JSON with timestamp, event type, user, source IP, detail
- Logs rotated at 50MB, retained 90 days
- Viewable in admin dashboard (Security Log page)
- Logs on encrypted partition (tamper-resistant when device is off)

#### SR-SEC-09: Signed OTA Updates

> **Status: Implemented.** Ed25519 signature verification in OTA service. See ADR-0008.

- All artifacts signed with Ed25519 keypair (both `.swu` and `.tar.zst` app bundles)
- Build machine holds private signing key (never on devices)
- Devices hold public verification key (in rootfs, not `/data` — survives factory reset)
- Update rejected if signature verification fails — source is never trust, only signature
- Artifact naming convention: `hm-<target>-<type>-<version>.<ext>` with detached `.sig` for app bundles
- Prevents installation of malicious firmware regardless of delivery mode (USB, upload, push, SCP)

#### SR-SEC-10: Camera Pairing Protocol

> **Status: Implemented.** PairingService + PairingManager + `/pair/exchange` endpoint. See ADR-0009.

- Camera discovered via mDNS appears as "pending" (untrusted)
- Admin clicks "Pair" in dashboard → server generates ECDSA P-256 client cert + 6-digit PIN (5-min expiry)
- PIN displayed on dashboard; admin enters PIN on camera's status page (`/pair`)
- Camera POSTs PIN to server (`POST /api/v1/pair/exchange`) — rate-limited to 3 attempts per 5-min window
- Server returns: `client.crt`, `client.key`, `ca.crt`, RTSPS URL, `pairing_secret` (for LUKS key derivation, ADR-0010)
- Camera stores certs at `/data/certs/`, transitions lifecycle to CONNECTING with mTLS
- Only after pairing: camera can stream (RTSPS), receives firewall allowance, OTA push authentication
- Camera removal revokes cert (moved to `cameras/revoked/`), removes from firewall `@camera_ips` set
- Single pairing ceremony establishes: mTLS identity + OTA trust + LUKS key material

---

## 6. Non-Functional Requirements

### NFR-01: Reliability

- Server must operate 24/7 without manual intervention
- Services auto-restart on crash (systemd `Restart=always`, `WatchdogSec`)
- Recordings must not be corrupted if power is lost mid-write (MP4 mux finalized every segment)
- OTA updates must be atomic — never leave device in unbootable state

### NFR-02: Performance

- Live view latency < 3 seconds end-to-end
- Dashboard initial load < 2 seconds on WiFi
- API response time < 500ms for non-streaming endpoints
- Camera node CPU usage < 30% during streaming (hardware encode offloads to GPU)
- Server handles up to 8 simultaneous camera streams (Phase 2)

### NFR-03: Security

- TLS on all connections from Phase 1 (HTTPS + RTSPS with mTLS)
- No default passwords — first-boot wizard forces admin account creation
- All passwords bcrypt hashed (cost factor 12)
- Session tokens: cryptographically random, Secure/HttpOnly/SameSite cookies
- CSRF protection on all state-changing endpoints
- Encrypted data partition (LUKS2) — SD card theft yields nothing
- Firewall (nftables) — minimal open ports, camera IPs allowlisted
- Mutual TLS camera authentication — no rogue device injection
- Signed OTA images (Ed25519) — no malicious firmware
- Audit logging of all security events
- Rate limiting on auth endpoints (5 attempts per minute, block after 10 failures)
- See Section 5.3 (SR-SEC-01 through SR-SEC-10) for detailed security requirements
- See docs/architecture.md Section 3 for security architecture and threat model

### NFR-04: Storage Efficiency

- 1080p @ 25fps H.264 estimated: ~3-5 MB per minute, ~9-15 MB per 3-min clip
- 32GB SD card: ~1,500-3,000 clips = ~3-6 days continuous single camera
- 128GB SD card: ~12-24 days continuous single camera
- 1TB USB disk: ~100-200 days continuous single camera
- Loop recording prevents storage exhaustion

### NFR-05: Maintainability

- All custom software in `meta-home-monitor` layer (single repo)
- Configuration and data on separate partition (survives OTA)
- Structured logging via journald
- Version-tagged releases with pre-built images on GitHub

### NFR-06: Extensibility

The architecture must support future additions without redesign:
- Multiple cameras (discovery + per-camera config already in data model)
- Motion detection (recording mode enum already includes placeholder)
- Cloud relay (API already versioned, HTTPS planned)
- Mobile app (REST API already defined, no browser-specific dependencies)
- Smart home integration (ONVIF compatibility layer)
- Audio (transport and recording pipeline can add audio track)
- Clip protection/starring (add `protected` flag to clip metadata)

---

## 7. Design & Architecture Requirements

### DR-01: Platform Abstraction

- All hardware-specific paths (camera device, LED sysfs, thermal sensor, WiFi interface) must be read from a `Platform` provider or environment variables — never hardcoded in business logic.
- The `Platform` class auto-detects hardware at startup and can be overridden via environment variables for testing or alternative boards.
- Modules receive hardware paths via constructor injection.
- Hardware access must fail silently on unsupported platforms (CI, containers, different SBCs).

### DR-02: Single Responsibility

- Each Python file contains one primary class with one clear responsibility.
- Files exceeding ~300 lines must be split into focused modules.
- Each module must be describable in a single sentence without the word "and".
- Exception: small related dataclasses may share a file (e.g., `models.py`).

### DR-03: Strategy Pattern for Backends

- Swappable components (streaming, capture, detection, notification) define interfaces using `typing.Protocol`.
- Backend selection happens once at startup, not scattered through business logic.
- Adding a new backend means creating a new class satisfying the Protocol — no changes to existing code.
- Current strategies: stream backend (FFmpeg/go2rtc), capture backend (v4l2/libcamera), player backend (WebRTC/HLS).

### DR-04: Constructor Dependency Injection

- All class dependencies are passed via `__init__()`, never imported and used as module-level globals.
- Flask `current_app` context is acceptable for app-wide singletons (Store, AuditLogger).
- No DI frameworks, no service locators, no global registries.

### DR-05: Fail-Silent Hardware Access

- All hardware interactions (LED control, thermal sensors, GPIO) are wrapped in try/except with debug-level logging on failure.
- Missing hardware must never crash the application — degrade gracefully.
- This enables the same codebase to run on different boards, in CI, and in containers.

### DR-06: Live Streaming Latency

- Live view must use WebRTC (via MediaMTX WHEP) for sub-second latency.
- HLS is retained as a fallback when WebRTC ICE negotiation fails.
- Recordings continue to use the FFmpeg HLS-to-MP4 pipeline (unchanged).
- MediaMTX is the single stream hub — camera pushes RTSP, all consumers read from MediaMTX.
- The browser player implements a fallback cascade: WebRTC first → HLS fallback.

---

## 8. Technology Stack Summary

| Layer | Technology |
|-------|-----------|
| OS | Home Monitor OS (Yocto Scarthgap 5.0 LTS, aarch64) |
| Init | systemd |
| Video capture | v4l2 hardware H.264 encoder |
| Streaming protocol | RTSPS (mTLS) via MediaMTX, ffmpeg for capture/record |
| Live view delivery | WebRTC (MediaMTX WHEP, sub-1s) with HLS fallback |
| Recording format | MP4 (3-minute segments, ffmpeg) |
| Web backend | Python 3 + Flask |
| Web frontend | HTMX + Alpine.js, mobile-first dark theme (ADR-0012) |
| Reverse proxy | nginx |
| Auth | Flask sessions + bcrypt |
| Network discovery | Avahi (mDNS/DNS-SD) |
| Boot loader | U-Boot (`u-boot-rpi` from meta-raspberrypi) |
| TLS | Self-signed CA (ECDSA P-256, OpenSSL), mTLS for cameras |
| Encryption at rest | LUKS2 with Adiantum cipher (`xchacha20,aes-adiantum-plain64`), argon2id KDF |
| Firewall | nftables |
| OTA updates | SWUpdate (dual A/B rootfs + app-only symlink swap), Ed25519 signed |
| Build system | Yocto BitBake |
| CI/Releases | GitHub Releases |

---

## 9. Open Questions / Future Decisions

| # | Question | When to decide |
|---|----------|---------------|
| ~~1~~ | ~~Exact swupdate partition sizes~~ — **Decided in ADR-0008:** 512 MB boot, 8 GB rootfsA/B, remaining for data | ~~Resolved~~ |
| ~~2~~ | ~~Cloud relay architecture~~ — **Resolved:** Tailscale VPN implemented for remote access (no custom relay needed for Phase 1) | ~~Resolved~~ |
| 3 | Mobile app framework — React Native, Flutter, or native | Phase 2 planning |
| 4 | Motion detection library — OpenCV on-device vs. server-side analysis | Phase 2 planning |
| 5 | ONVIF compliance level for smart home integration | Phase 3 planning |
| 6 | Audio codec and sync strategy when mic-equipped cameras are added | Phase 2 planning |
| 7 | Backup strategy for `/data` partition (clips, config) | Phase 1 refinement |

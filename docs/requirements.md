# RPi Home Monitor - Requirements Specification

Version: 1.0
Date: 2026-04-09

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
- Mobile web dashboard with authentication
- System health monitoring
- OTA-ready partition layout (swupdate/A-B)
- SD card storage with loop recording
- Ethernet + WiFi support on server

### Phase 2 — Multi-Camera & Remote Access

- Multiple camera nodes (auto-discovered on network)
- Motion detection (triggers recording, flags events)
- Push notifications (motion alerts via email/Telegram/push)
- Cloud relay server for remote access outside home WiFi
- Mobile app (Android + iOS)
- USB external disk support on server
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
- Works with SD card; extensible to USB disk (Phase 2)

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
- Server: flash SD, plug in, connect Ethernet or configure WiFi, access dashboard
- Camera: flash SD, plug in, configure WiFi (via serial or first-boot AP), camera auto-connects to server
- First-boot setup wizard for initial admin account creation and timezone

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
| OS base | Yocto Linux (Scarthgap 5.0 LTS) | Minimal, reproducible, custom |
| Init system | systemd | Service management, journald logging |
| Kernel | linux-raspberrypi 6.6.x (aarch64) | RPi Foundation kernel, 64-bit |
| Package format | deb | Easy on-device package management |
| OTA framework | swupdate (A/B partitions) | Atomic updates with rollback |

### 4.3 Video Pipeline

| Item | Choice | Rationale |
|------|--------|-----------|
| Camera capture | v4l2 (h264 from hardware encoder) | Zero CPU encode on Zero 2W |
| Transport | RTSP over TCP | Reliable stream delivery over WiFi |
| Streaming tool | ffmpeg | Proven, flexible, available in Yocto |
| Recording format | MP4 (3-minute segments) | Browser-native playback, small files |
| Live view in browser | HLS or fragmented MP4 | No plugin needed, works on mobile Safari/Chrome |

### 4.4 Web Application

| Item | Choice | Rationale |
|------|--------|-----------|
| Backend | Python 3 + Flask | Simple, already in Yocto, easy to extend |
| Frontend | Mobile-first HTML/CSS/JS | No build tools needed, works everywhere |
| Reverse proxy | nginx | Serves video files efficiently, proxies Flask |
| Authentication | Flask session-based (bcrypt hashed passwords) | Simple, secure for local network |
| Video playback | HTML5 `<video>` with HLS.js | Native mobile support |

### 4.5 Network Architecture

```
Camera Node                    Server                     Client
┌─────────────┐    RTSP/TCP    ┌──────────────┐   HTTP    ┌────────┐
│ ffmpeg       │──────────────>│ RTSP receiver │          │ Phone  │
│ v4l2 → h264  │               │ (ffmpeg)     │          │ browser│
│              │    mDNS       │              │   :80    │        │
│ avahi-daemon │<─────────────>│ avahi-daemon │<─────────│        │
│              │               │              │          │        │
│ camera-      │               │ Flask app    │──────────>│ Web UI │
│  streamer    │               │ nginx proxy  │          │        │
│  .service    │               │ monitor      │          │        │
└─────────────┘               │  .service    │          └────────┘
                               └──────────────┘
```

### 4.6 Storage Layout

**Server (RPi 4B):**
```
/
├── /boot               # Kernel, DTBs, config.txt (partition 1, vfat)
├── /                   # Root filesystem A (partition 2, ext4)
├── /                   # Root filesystem B (partition 3, ext4, for OTA)
└── /data               # Persistent data partition (partition 4, ext4)
    ├── /recordings     # 3-min MP4 clips, organized by camera/date
    │   └── /<cam-id>/
    │       └── /YYYY-MM-DD/
    │           ├── 14-00-00.mp4
    │           ├── 14-03-00.mp4
    │           └── ...
    ├── /snapshots      # Thumbnail JPEGs per clip
    ├── /config         # App config, user database, camera registry
    └── /logs           # Persistent application logs
```

**Camera (Zero 2W):**
```
/
├── /boot               # Kernel, DTBs, config.txt (partition 1, vfat)
├── /                   # Root filesystem A (partition 2, ext4)
├── /                   # Root filesystem B (partition 3, ext4, for OTA)
└── /data               # Persistent config (partition 4, ext4)
    └── /config         # camera.conf, WiFi credentials
```

### 4.7 Partition Scheme (OTA-ready, swupdate A/B)

| Partition | Type | Size (Server) | Size (Camera) | Purpose |
|-----------|------|---------------|---------------|---------|
| boot | vfat | 100 MB | 100 MB | Kernel + DTBs |
| rootfsA | ext4 | 2 GB | 1 GB | Active root filesystem |
| rootfsB | ext4 | 2 GB | 1 GB | Standby root (OTA target) |
| data | ext4 | Remaining | 256 MB | Persistent data, recordings, config |

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
- First-boot: if no WiFi configured, start a temporary AP (`HomeMonitor-Setup-XXXX`) for configuration
- Persist WiFi credentials to `/data/config/` (survives OTA updates)

#### SR-CAM-05: OTA Update Support

- Dual rootfs partitions (A/B layout) using swupdate
- Accept update images pushed from server over HTTP
- Automatic rollback if new rootfs fails to boot (3-attempt threshold)
- Report current firmware version to server via mDNS TXT record

#### SR-CAM-06: System Watchdog

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

- Dual rootfs partitions (A/B layout) using swupdate
- Dashboard page (admin only): current server firmware version, available update (manual upload or URL)
- Push updates to connected cameras from server
- Update status tracking: idle, downloading, installing, rebooting, success, failed
- Automatic rollback on failed boot (3-attempt threshold)

#### SR-SRV-11: Nginx Configuration

- Port 80: reverse proxy to Flask app (port 5000)
- `/live/<cam-id>/`: serve HLS segments for live view
- `/clips/<cam-id>/`: serve MP4 clips directly (nginx `mp4` module, byte-range support)
- `/snapshots/<cam-id>/`: serve thumbnail JPEGs
- `client_max_body_size`: 500MB (for OTA image uploads)
- Gzip compression for HTML/CSS/JS
- (Phase 2: port 443 with self-signed or Let's Encrypt TLS)

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

**Recordings:**
- `GET /recordings/<cam-id>?date=YYYY-MM-DD` — list clips for a camera on a date
- `GET /recordings/<cam-id>/timeline?date=YYYY-MM-DD` — timeline data (start/end times of available clips)
- `GET /recordings/<cam-id>/latest` — most recent clip
- `DELETE /recordings/<cam-id>/<filename>` — delete a clip (admin)

**Live:**
- `GET /live/<cam-id>/stream.m3u8` — HLS playlist for live view
- `GET /live/<cam-id>/snapshot` — current frame as JPEG

**System:**
- `GET /system/health` — server health (CPU, RAM, disk, temp)
- `GET /system/storage` — storage breakdown
- `GET /system/info` — firmware version, uptime, hostname

**Settings:**
- `GET /settings` — current settings
- `PUT /settings` — update settings (admin)

**Users:**
- `GET /users` — list users (admin)
- `POST /users` — create user (admin)
- `DELETE /users/<id>` — delete user (admin)
- `PUT /users/<id>/password` — change password (admin or self)

**OTA:**
- `POST /ota/server/upload` — upload update image for server (admin)
- `POST /ota/camera/<id>/push` — push update to camera (admin)
- `GET /ota/status` — update status for all devices

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

- No default passwords — first-boot wizard forces admin account creation
- All passwords bcrypt hashed (cost factor 12)
- Session tokens: cryptographically random, HTTP-only cookies
- No ports exposed except 80 (HTTP) and 8554 (RTSP, internal network only)
- (Phase 2: HTTPS with TLS)
- API rate limiting on auth endpoints (5 attempts per minute)

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

## 7. Technology Stack Summary

| Layer | Technology |
|-------|-----------|
| OS | Yocto Linux (Scarthgap 5.0 LTS, aarch64) |
| Init | systemd |
| Video capture | v4l2 hardware H.264 encoder |
| Streaming protocol | RTSP over TCP (ffmpeg) |
| Live view delivery | HLS (nginx serves .m3u8/.ts) |
| Recording format | MP4 (3-minute segments, ffmpeg) |
| Web backend | Python 3 + Flask |
| Web frontend | HTML5 + CSS + vanilla JS (mobile-first) |
| Reverse proxy | nginx |
| Auth | Flask sessions + bcrypt |
| Network discovery | Avahi (mDNS/DNS-SD) |
| OTA updates | swupdate (dual A/B rootfs) |
| Build system | Yocto BitBake |
| CI/Releases | GitHub Releases |

---

## 8. Open Questions / Future Decisions

| # | Question | When to decide |
|---|----------|---------------|
| 1 | Exact swupdate partition sizes — depends on rootfs size after all packages | During OTA implementation |
| 2 | Cloud relay architecture — small VPS with WireGuard tunnel vs. hosted signaling | Phase 2 planning |
| 3 | Mobile app framework — React Native, Flutter, or native | Phase 2 planning |
| 4 | Motion detection library — OpenCV on-device vs. server-side analysis | Phase 2 planning |
| 5 | ONVIF compliance level for smart home integration | Phase 3 planning |
| 6 | Audio codec and sync strategy when mic-equipped cameras are added | Phase 2 planning |
| 7 | Backup strategy for `/data` partition (clips, config) | Phase 1 refinement |

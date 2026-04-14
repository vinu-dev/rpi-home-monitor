# RPi Home Monitor - Software Architecture

Version: 1.0
Date: 2026-04-09

---

## 1. System Overview

The RPi Home Monitor is a distributed system with three component types:

```
┌─────────────┐     RTSPS (mTLS)     ┌─────────────────┐     HTTPS      ┌───────────┐
│ Camera Node │ ──────────────────> │   Home Server    │ <──────────── │  Client   │
│ (Zero 2W)   │                      │   (RPi 4B)      │               │  (Phone/  │
│             │     mDNS discovery   │                 │               │   Browser)│
│ Captures    │ <─ ─ ─ ─ ─ ─ ─ ─ > │  Records clips  │               │           │
│ + streams   │                      │  Serves web UI  │               │ Dashboard │
│ video       │     OTA push         │  Manages cams   │               │ Live view │
│             │ <─────────────────── │                 │               │ Playback  │
└─────────────┘                      └─────────────────┘               └───────────┘
      x N                                   x 1                            x N
```

- **Camera Nodes**: Capture video, stream to server. Minimal software footprint.
- **Home Server**: Central hub. Receives streams, records clips, serves dashboard, manages everything.
- **Clients**: Mobile phones/laptops accessing the web dashboard over HTTPS.

---

## 2. Application Architecture

### 2.1 Two Separate Applications

| Application | Runs On | Language | Purpose |
|---|---|---|---|
| `monitor-server` | RPi 4B | Python 3 (Flask) | Web UI, API, recording, camera management |
| `camera-streamer` | Zero 2W | Python 3 | Video capture, RTSP streaming, discovery |

They are separate codebases because they run on different hardware with different responsibilities. They communicate over the network via RTSP and HTTP.

### 2.2 Server Application Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        nginx (:443, TLS)                          │
│                                                                    │
│  /live/<cam>/*.ts ──> HLS segment files (disk)                    │
│  /clips/<cam>/*   ──> MP4 recordings (disk, byte-range)           │
│  /static/*        ──> CSS/JS/images (disk)                         │
│  /*               ──> reverse proxy to Flask (:5000)               │
└──────────┬────────────────────────┬────────────────────────────────┘
           │                        │
           │              ┌─────────▼──────────────────────┐
           │              │       Flask App (:5000)         │
           │              │                                │
           │              │  ┌───────────────────────────┐ │
           │              │  │     Security Middleware    │ │
           │              │  │  • TLS termination (nginx) │ │
           │              │  │  • Session auth (cookies)  │ │
           │              │  │  • CSRF protection         │ │
           │              │  │  • Rate limiting            │ │
           │              │  │  • Audit logging            │ │
           │              │  └─────────────┬─────────────┘ │
           │              │                │               │
           │              │  ┌─────────────▼─────────────┐ │
           │              │  │      API Blueprints       │ │
           │              │  │                           │ │
           │              │  │  /api/v1/auth/*           │ │
           │              │  │  /api/v1/cameras/*        │ │
           │              │  │  /api/v1/recordings/*     │ │
           │              │  │  /api/v1/live/*           │ │
           │              │  │  /api/v1/system/*         │ │
           │              │  │  /api/v1/settings/*       │ │
           │              │  │  /api/v1/users/*          │ │
           │              │  │  /api/v1/ota/*            │ │
           │              │  │  /cameras/<id>/pair       │ │
           │              │  │  /pair/exchange            │ │
           │              │  │  /pair/register            │ │
           │              │  └─────────────┬─────────────┘ │
           │              │                │               │
           │              │  ┌─────────────▼─────────────┐ │
           │              │  │   Background Services     │ │
           │              │  │   (threads in process)    │ │
           │              │  │                           │ │
           │              │  │  • RecorderService        │ │
           │              │  │  • StreamingService       │ │
           │              │  │  • StorageService         │ │
           │              │  │  • CameraService          │ │
           │              │  │  • PairingService         │ │
           │              │  │  • CertService            │ │
           │              │  │  • OtaService             │ │
           │              │  │  • HealthMonitor          │ │
           │              │  │  • AuditLogger            │ │
           │              │  └───────────────────────────┘ │
           │              └────────────────────────────────┘
           │
    ┌──────▼──────────────────────────────────────┐
    │           /data (LUKS encrypted)             │
    │                                              │
    │  /data/recordings/<cam-id>/YYYY-MM-DD/       │
    │      HH-MM-SS.mp4          (3-min clips)     │
    │      HH-MM-SS.thumb.jpg    (thumbnails)      │
    │                                              │
    │  /data/live/<cam-id>/                         │
    │      stream.m3u8           (HLS playlist)     │
    │      segment_NNN.ts        (HLS segments)     │
    │                                              │
    │  /data/config/                                │
    │      cameras.json          (camera registry)  │
    │      users.json            (user accounts)    │
    │      settings.json         (system settings)  │
    │                                              │
    │  /data/certs/                                 │
    │      ca.crt / ca.key       (local CA)         │
    │      server.crt / server.key                  │
    │      cameras/<cam-id>.crt  (client certs)     │
    │                                              │
    │  /data/logs/                                  │
    │      audit.log             (security events)  │
    └──────────────────────────────────────────────┘
```

### 2.3 Camera Application Architecture

```
┌──────────────────────────────────────────────────┐
│            Camera Node (Zero 2W)                  │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │        camera-streamer (Python)             │  │
│  │                                            │  │
│  │  ┌──────────┐    ┌─────────────────────┐  │  │
│  │  │ Capture  │    │ StreamManager       │  │  │
│  │  │ Manager  │───>│ ffmpeg pipeline     │  │  │
│  │  │ v4l2     │    │ RTSP push output    │──┼──┼──> Server (:8554)
│  │  │ /dev/    │    │ auto-reconnect      │  │  │
│  │  │ video0   │    │ backoff retry       │  │  │
│  │  └──────────┘    └─────────────────────┘  │  │
│  │                                            │  │
│  │  ┌──────────────────────────────────────┐  │  │
│  │  │ WiFi Setup (first boot, port 80)     │  │  │
│  │  │ Hotspot "HomeCam-Setup" → wizard     │  │  │
│  │  │ Collects: WiFi, server, admin creds  │  │  │
│  │  └──────────────────────────────────────┘  │  │
│  │                                            │  │
│  │  ┌──────────────────────────────────────┐  │  │
│  │  │ Status Server (post-setup, port 443) │  │  │
│  │  │ Login: PBKDF2-SHA256 + sessions      │  │  │
│  │  │ Pages: /login, /, /api/status        │  │  │
│  │  │ Actions: WiFi change, password change│  │  │
│  │  └──────────────────────────────────────┘  │  │
│  │                                            │  │
│  │  ┌──────────────┐  ┌───────────────────┐  │  │
│  │  │ Discovery    │  │ HealthMonitor     │  │  │
│  │  │ Avahi mDNS   │  │ CPU, RAM, uptime  │  │  │
│  │  │ _rtsp._tcp   │  │ Device watchdog   │  │  │
│  │  └──────────────┘  └───────────────────┘  │  │
│  │                                            │  │
│  │  ┌──────────────┐  ┌───────────────────┐  │  │
│  │  │ LED Control  │  │ ConfigManager     │  │  │
│  │  │ setup/conn/  │  │ camera.conf       │  │  │
│  │  │ error/solid  │  │ admin credentials │  │  │
│  │  └──────────────┘  └───────────────────┘  │  │
│  │                                            │  │
│  │  ┌──────────────┐  ┌───────────────────┐  │  │
│  │  │ Pairing      │  │ OTA Agent         │  │  │
│  │  │ PIN exchange │  │ HTTP :8080 (mTLS) │  │  │
│  │  │ cert storage │  │ stream-to-disk    │  │  │
│  │  └──────────────┘  └───────────────────┘  │  │
│  │                                            │  │
│  │  ┌──────────────────────────────────────┐  │  │
│  │  │ Factory Reset                        │  │  │
│  │  │ WiFi wipe + config reset + reboot    │  │  │
│  │  └──────────────────────────────────────┘  │  │
│  │                                            │  │
│  │  ┌──────────────────────────────────────┐  │  │
│  │  │ Templates (HTML)                     │  │  │
│  │  │ login.html | setup.html | status.html│  │  │
│  │  └──────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  mDNS: rpi-divinu-cam-XXXX.local                │
│  nftables: only server IP allowed                │
│  /data: LUKS encrypted                           │
└──────────────────────────────────────────────────┘
```

### 2.4 Camera Authentication

Camera nodes have their own local authentication system, independent of the server:

| Aspect | Detail |
|--------|--------|
| **Hashing** | PBKDF2-SHA256, 100k iterations, random 16-byte salt |
| **Sessions** | In-memory dict, `cam_session` HttpOnly cookie, 2-hour timeout |
| **Default user** | `admin` (set during first-boot provisioning) |
| **Storage** | `ADMIN_USERNAME` and `ADMIN_PASSWORD` (salt:hash) in `/data/config/camera.conf` |
| **Endpoints** | `/login` (GET/POST), `/logout`, `/api/status`, `/api/networks`, `/api/wifi`, `/api/password` |

This protects the camera's status page and WiFi settings from unauthorized access on the LAN.

### 2.5 mDNS Discovery

Both server and cameras advertise via Avahi/mDNS:

| Device | Hostname | URL | Service |
|--------|----------|-----|---------|
| Server | `rpi-divinu` | `https://rpi-divinu.local` | `_homemonitor._tcp`, `_https._tcp` |
| Camera | `rpi-divinu-cam-XXXX` | `https://rpi-divinu-cam-XXXX.local` | `_rtsp._tcp` |

Camera hostnames are derived from the CPU serial number: last 4 hex chars become the suffix (e.g., serial `...351ad8ee` → hostname `rpi-divinu-cam-d8ee`). This ensures uniqueness in multi-camera deployments.

The server dashboard shows clickable `.local` links for each camera's status page.

---

## 3. Security Architecture

### 3.1 Threat Model

**What we're protecting:** Live video feeds and recorded footage of a home interior/exterior. Compromise means someone can spy on the homeowner and family.

| Threat | Attack Vector | Impact | Mitigation |
|--------|--------------|--------|------------|
| WiFi eavesdropping | Sniff HTTP/RTSP traffic on same network | View live video, steal credentials | TLS on all connections (HTTPS + RTSPS) |
| Unauthorized dashboard access | Guess/brute-force login | Full system control | Auth + rate limiting + session management |
| Camera impersonation | rogue device sends fake mDNS + RTSP | Inject fake video feed | mTLS camera pairing with client certs |
| SD card theft (server) | Physical access to RPi 4B | All recordings, WiFi creds, user passwords | LUKS encryption on /data partition |
| SD card theft (camera) | Physical access to Zero 2W | WiFi password, server address | LUKS encryption on /data partition |
| Default credentials | SSH as root with no password | Full device control | No debug-tweaks in production, key-only SSH |
| Rogue firmware update | Push malicious update to device | Persistent backdoor | Signed OTA images (Ed25519) |
| Network scanning | Port scan from compromised device on LAN | Discover attack surface | nftables firewall, minimal open ports |
| Session hijacking | Steal session cookie | Access dashboard as victim | Secure/HttpOnly/SameSite cookies, HTTPS only |
| CSRF attack | Trick admin into clicking malicious link | Change settings, delete clips | CSRF tokens on all state-changing requests |

### 3.2 Trust Boundaries

```
┌──────────────────────────────────────────────────────────┐
│                    TRUSTED ZONE                           │
│                                                          │
│  ┌──────────┐    mTLS      ┌──────────┐                 │
│  │ Camera   │ ◄──────────► │ Server   │                 │
│  │ (paired) │              │          │                 │
│  └──────────┘              └────┬─────┘                 │
│                                 │                        │
│ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│─ ─ ─ ─ TRUST BOUNDARY │
│                                 │ HTTPS                  │
│                            ┌────▼─────┐                 │
│                            │ Browser  │                 │
│                            │ (authed) │                 │
│                            └──────────┘                 │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                   UNTRUSTED ZONE                          │
│                                                          │
│  • Other devices on WiFi                                 │
│  • Unauthenticated browsers                              │
│  • The internet (Phase 2)                                │
│  • Unpaired/unknown cameras                              │
│  • Physical access to hardware                           │
└──────────────────────────────────────────────────────────┘
```

### 3.3 Encryption

| What | Method | Key Management |
|------|--------|---------------|
| Browser ↔ Server | TLS 1.3 (HTTPS, nginx) | Self-signed CA (ECDSA P-256, 10-year), generated on first boot |
| Camera ↔ Server | mTLS (RTSPS) | Server CA signs camera ECDSA P-256 client certs during PIN-based pairing (ADR-0009) |
| Data at rest (server) | LUKS2 (`xchacha20,aes-adiantum-plain64`), argon2id (1 GB, 4 iter) | Passphrase set during first-boot setup; optional auto-unlock keyfile. See ADR-0010 |
| Data at rest (camera) | LUKS2 (`xchacha20,aes-adiantum-plain64`), argon2id (64 MB, 4 iter) | Key derived via HKDF-SHA256 from `pairing_secret` + CPU serial. See ADR-0010 |
| Passwords | bcrypt (cost 12) | Stored in /data/config/users.json |
| OTA images | Ed25519 signature | Build machine holds signing key, devices hold public key |
| Session tokens | cryptographically random (32 bytes) | Server-side session store |

### 3.4 Certificate Authority & Camera Pairing

> **Status: Implemented.** CA generation, PIN-based pairing, mTLS cert exchange, and cert revocation are operational. See ADR-0009.

```
FIRST BOOT (Server):
  1. Generate ECDSA P-256 CA keypair → /data/certs/ca.crt, ca.key (10-year validity)
  2. Generate server TLS cert signed by CA → /data/certs/server.crt, server.key (5-year validity)
  3. nginx configured with server cert
  4. systemd timer (cert-renewal-check.timer) checks weekly, warns 30 days before expiry

CAMERA PAIRING (PIN-based, see ADR-0009):
  1. Camera boots, advertises via mDNS (unpaired state)
  2. Server discovers camera, shows as "pending" in dashboard
  3. Admin clicks "Pair" → server generates:
     - ECDSA P-256 keypair + client cert signed by CA (5-year validity)
     - 6-digit PIN (cryptographically random, 5-minute expiry)
  4. Admin enters PIN on camera status page (/pair)
  5. Camera POSTs PIN to server (POST /api/v1/pair/exchange, rate-limited: 3 attempts/5 min)
  6. Server returns: client.crt, client.key, ca.crt, RTSPS URL, pairing_secret
  7. Camera stores certs in /data/certs/, pairing_secret for LUKS key derivation (ADR-0010)
  8. All future connections use mTLS (RTSPS, OTA push, health polling)
  9. Single pairing ceremony → mTLS identity + OTA trust + LUKS key material

CAMERA REMOVAL:
  1. Admin removes camera from dashboard
  2. Server moves cert to cameras/revoked/ (in-memory revocation set, rebuilt on startup)
  3. MediaMTX reloaded, nftables @camera_ips updated
  4. Camera can no longer connect
```

### 3.5 Firewall Rules

**Server (nftables):**
```
table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;

        # Established connections
        ct state established,related accept

        # Loopback
        iif lo accept

        # HTTPS from LAN
        tcp dport 443 ip saddr 192.168.0.0/16 accept
        tcp dport 443 ip saddr 10.0.0.0/8 accept

        # RTSPS from paired cameras only (IPs populated dynamically)
        tcp dport 8554 ip saddr @camera_ips accept

        # SSH (optional, key-only, rate-limited)
        tcp dport 22 ip saddr 192.168.0.0/16 ct state new limit rate 3/minute accept

        # mDNS
        udp dport 5353 accept

        # ICMP
        icmp type echo-request accept

        # Log + drop everything else
        log prefix "DROPPED: " drop
    }

    set camera_ips {
        type ipv4_addr
        # Populated dynamically when cameras connect
    }
}
```

**Camera (nftables):**
```
table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;

        ct state established,related accept
        iif lo accept

        # SSH from server only
        tcp dport 22 ip saddr @server_ip accept

        # OTA update endpoint from server only
        tcp dport 8080 ip saddr @server_ip accept

        # mDNS
        udp dport 5353 accept

        log prefix "DROPPED: " drop
    }

    chain output {
        type filter hook output priority 0; policy drop;

        ct state established,related accept
        oif lo accept

        # RTSPS to server only
        tcp dport 8554 ip daddr @server_ip accept

        # DNS + DHCP
        udp dport 53 accept
        udp dport 67 accept

        # mDNS
        udp dport 5353 accept

        # NTP
        udp dport 123 accept

        log prefix "DROPPED-OUT: " drop
    }

    set server_ip {
        type ipv4_addr
        # Set during pairing
    }
}
```

### 3.6 Audit Logging

Every security-relevant event is logged to `/data/logs/audit.log`:

```json
{
    "timestamp": "2026-04-09T14:32:01Z",
    "event": "LOGIN_SUCCESS",
    "user": "admin",
    "ip": "192.168.1.50",
    "detail": "session created"
}
```

**Events logged:**
- `LOGIN_SUCCESS`, `LOGIN_FAILED` — who, from where, how many attempts
- `SESSION_EXPIRED`, `SESSION_LOGOUT`
- `CAMERA_PAIRED`, `CAMERA_REMOVED`, `CAMERA_OFFLINE`, `CAMERA_ONLINE`
- `USER_CREATED`, `USER_DELETED`, `PASSWORD_CHANGED`
- `SETTINGS_CHANGED` — what changed, by whom
- `CLIP_DELETED` — which clip, by whom
- `OTA_STARTED`, `OTA_COMPLETED`, `OTA_FAILED`, `OTA_ROLLBACK`
- `FIREWALL_BLOCKED` — source IP, port (from nftables log)
- `CERT_GENERATED`, `CERT_REVOKED`

Logs are rotated (max 50MB), kept for 90 days. Viewable in admin dashboard under "Security Log".

---

## 4. Data Architecture

### 4.1 No Database — JSON Files

The system stores all state as JSON files on the encrypted `/data` partition. This is appropriate because:
- Small data volume (dozens of cameras, handful of users, one settings object)
- Simple read/write patterns (no complex queries)
- Human-inspectable and easy to backup
- No database daemon consuming RAM on a 2GB RPi

### 4.2 Data Files

**`/data/config/cameras.json`**
```json
{
    "cameras": [
        {
            "id": "cam-a1b2c3d4",
            "name": "Front Door",
            "location": "Outdoor",
            "status": "online",
            "ip": "192.168.1.101",
            "rtsp_url": "rtsps://192.168.1.101:8554/stream",
            "recording_mode": "continuous",
            "resolution": "1080p",
            "fps": 25,
            "paired_at": "2026-04-09T10:00:00Z",
            "last_seen": "2026-04-09T14:32:01Z",
            "firmware_version": "1.0.0",
            "cert_serial": "A1B2C3D4E5F6"
        }
    ]
}
```

**`/data/config/users.json`**
```json
{
    "users": [
        {
            "id": "usr-001",
            "username": "admin",
            "password_hash": "$2b$12$...",
            "role": "admin",
            "created_at": "2026-04-09T10:00:00Z",
            "last_login": "2026-04-09T14:00:00Z"
        }
    ]
}
```

**`/data/config/settings.json`**
```json
{
    "timezone": "Europe/Dublin",
    "storage_threshold_percent": 90,
    "clip_duration_seconds": 180,
    "session_timeout_minutes": 30,
    "hostname": "home-monitor",
    "setup_completed": true,
    "firmware_version": "1.0.0"
}
```

### 4.3 Recording File Layout

```
/data/recordings/
├── cam-a1b2c3d4/
│   ├── 2026-04-09/
│   │   ├── 08-00-00.mp4          # 3-min clip: 08:00:00 - 08:03:00
│   │   ├── 08-00-00.thumb.jpg    # Thumbnail extracted at 1s
│   │   ├── 08-03-00.mp4
│   │   ├── 08-03-00.thumb.jpg
│   │   └── ...
│   └── 2026-04-08/
│       └── ...
└── cam-e5f6g7h8/
    └── ...

/data/live/
├── cam-a1b2c3d4/
│   ├── stream.m3u8               # HLS playlist (rolling, 5 segments)
│   ├── segment_001.ts            # 2-second HLS segment
│   ├── segment_002.ts
│   └── ...
└── cam-e5f6g7h8/
    └── ...
```

---

## 5. Disk Partition Scheme

### 5.1 Server (RPi 4B)

```
SD Card Layout (64 GB card, minimum 32 GB):
┌───────────┬──────────┬──────────┬─────────────────────────┐
│   boot    │ rootfsA  │ rootfsB  │         data            │
│  (vfat)   │  (ext4)  │  (ext4)  │     (LUKS → ext4)      │
│  512 MB   │   8 GB   │   8 GB   │  remaining (~47 GB)     │
│           │ (active) │ (standby)│                         │
│ U-Boot    │ system   │ OTA      │ recordings, config,     │
│ kernel    │ packages │ target   │ certs, logs, OTA inbox  │
│ DTBs      │ apps     │          │                         │
│ config.txt│          │          │                         │
│ U-Boot env│          │          │                         │
└───────────┴──────────┴──────────┴─────────────────────────┘
```

> See ADR-0008 for U-Boot boot counting and A/B slot management. See ADR-0010 for LUKS encryption details.

### 5.2 Camera (Zero 2W)

```
SD Card Layout (64 GB card, minimum 32 GB):
┌───────────┬──────────┬──────────┬─────────────────────────┐
│   boot    │ rootfsA  │ rootfsB  │         data            │
│  (vfat)   │  (ext4)  │  (ext4)  │     (LUKS → ext4)      │
│  512 MB   │   8 GB   │   8 GB   │  remaining (~47 GB)     │
│           │ (active) │ (standby)│                         │
│ U-Boot    │ system   │ OTA      │ config, certs, WiFi,    │
│ kernel    │ packages │ target   │ OTA inbox               │
│ DTBs      │ apps     │          │                         │
│ config.txt│          │          │                         │
│ U-Boot env│          │          │                         │
└───────────┴──────────┴──────────┴─────────────────────────┘
```

---

## 6. Network Protocols

### 6.1 Video Pipeline

```
Camera                          Server                         Browser
┌─────────┐                    ┌──────────────┐               ┌────────┐
│ v4l2    │   RTSPS/TCP        │              │               │        │
│ H.264   │ ──────────────────>│  ffmpeg      │               │        │
│ hardware│   (mTLS)           │  receiver    │               │        │
│ encoder │                    │      │       │               │        │
└─────────┘                    │      ├──────>│ HLS segments  │ HLS.js │
                               │      │       │ (.m3u8 + .ts) │───────>│
                               │      │       │ via nginx     │        │
                               │      │       │               │        │
                               │      └──────>│ MP4 clips     │        │
                               │              │ (3-min segs)  │        │
                               │              │ via nginx     │───────>│
                               └──────────────┘               └────────┘
```

**Camera → Server:** RTSPS (RTSP over TLS with mutual authentication). H.264 video, copy codec (no re-encoding). TCP transport for reliability over WiFi.

**Server → Browser (live):** HLS. ffmpeg writes `.m3u8` playlist and 2-second `.ts` segments to disk. nginx serves them. HLS.js in the browser handles playback. Latency target: < 3 seconds.

**Server → Browser (clips):** Direct MP4 download/stream via nginx. Byte-range support for seeking. Native `<video>` tag playback.

### 6.2 Camera Discovery (mDNS/DNS-SD)

```
Camera advertises:
  Service: _rtsp._tcp.local
  Host: homecam-<serial>.local
  Port: 8554
  TXT:
    id=cam-a1b2c3d4
    version=1.0.0
    resolution=1080p
    paired=false

Server browses:
  Avahi browse _rtsp._tcp
  New camera found → add to "pending" list
  Camera confirmed → update paired=true, start RTSP connection
```

### 6.3 OTA Update Flow

> **Status: Partially implemented.** The target OTA architecture is defined and major pieces exist, but the full production path (signed bundles, full-system update, rollback, USB/import modes) is not yet fully validated on real hardware. See [update-roadmap.md](./update-roadmap.md), ADR-0008, and ADR-0014.

**Delivery modes** (all feed into single `inbox → verify → staging → install` pipeline):
```
1. USB drive     → udev auto-detect → copy *.swu / *.tar.zst to /data/ota/inbox/
2. Dashboard     → POST /api/v1/ota/server/upload (multipart file) → inbox/
3. Camera push   → POST /api/v1/ota/camera/<id>/push (HTTPS + mTLS) → camera inbox/
4. SSH/SCP       → direct copy to inbox/ (dev builds only)
5. (Future)      → Suricatta polling from repository URL
```

**Full-system update (.swu):**
```
1. Ed25519 signature verification in inbox (production target; dev builds may bypass signing per ADR-0014)
2. SWUpdate installs to inactive rootfs (A→B or B→A)
3. U-Boot env: upgrade_available=1, boot_count=0, bootlimit=3
4. Reboot into new partition
5. Health check within 90s → fw_setenv upgrade_available 0 (confirm)
6. If boot fails 3 times → U-Boot altbootcmd rolls back to previous partition
```

**App-only update (.tar.zst + .sig):**
```
1. Signature verification in production; dev builds may bypass signing per ADR-0014
2. Extract to /opt/monitor/releases/<version>/
3. Symlink swap: current → new version
4. systemctl restart <service>
5. Health check → if fail, swap symlink back (instant rollback, no reboot)
```

**Camera update:**
```
Admin triggers push → server sends artifact to camera OTA agent (port 8080, mTLS)
Camera runs same inbox → verify → install pipeline locally
```

---

## 7. Directory Structure

### 7.1 Repository Layout

```
rpi-home-monitor/
│
├── app/                               # APPLICATION CODE
│   ├── server/                        # RPi 4B server application
│   │   ├── monitor/                   # Python package
│   │   │   ├── __init__.py            # App factory: create_app()
│   │   │   ├── auth.py               # Login, sessions, CSRF, decorators
│   │   │   ├── models.py             # Data classes: Camera, User, Settings, Clip
│   │   │   ├── api/                   # Flask blueprints
│   │   │   │   ├── __init__.py
│   │   │   │   ├── cameras.py        # Camera CRUD, discovery confirmation
│   │   │   │   ├── recordings.py     # Clip listing, timeline, deletion
│   │   │   │   ├── live.py           # HLS stream endpoints, snapshots
│   │   │   │   ├── system.py         # Health, storage, server info
│   │   │   │   ├── settings.py       # Config read/write
│   │   │   │   ├── users.py          # User CRUD, password management
│   │   │   │   ├── ota.py            # OTA upload, push, status
│   │   │   │   ├── pairing.py        # PIN-based camera pairing + cert exchange
│   │   │   │   └── storage.py        # Storage management endpoints
│   │   │   ├── services/             # Background services
│   │   │   │   ├── __init__.py
│   │   │   │   ├── recorder_service.py    # ffmpeg recording manager (3-min clips)
│   │   │   │   ├── recordings_service.py  # Clip listing, timeline queries
│   │   │   │   ├── streaming_service.py   # HLS/recording pipeline orchestration
│   │   │   │   ├── storage_service.py     # Loop recording, cleanup, stats
│   │   │   │   ├── camera_service.py      # Camera CRUD + discovery confirmation
│   │   │   │   ├── pairing_service.py     # PIN-based pairing + cert lifecycle
│   │   │   │   ├── cert_service.py        # CA + certificate generation
│   │   │   │   ├── ota_service.py         # OTA verify, stage, install
│   │   │   │   ├── user_service.py        # User CRUD + password management
│   │   │   │   ├── settings_service.py    # System settings read/write
│   │   │   │   ├── factory_reset_service.py # WiFi wipe + config reset
│   │   │   │   ├── provisioning_service.py  # First-boot setup orchestration
│   │   │   │   └── tailscale_service.py   # Tailscale VPN management
│   │   │   ├── templates/            # Jinja2 HTML templates
│   │   │   │   ├── base.html         # Base layout (nav, auth check)
│   │   │   │   ├── login.html        # Login page
│   │   │   │   ├── setup.html        # First-boot wizard
│   │   │   │   ├── dashboard.html    # Camera grid, live view
│   │   │   │   ├── camera.html       # Single camera full view
│   │   │   │   ├── recordings.html   # Clip browser with timeline
│   │   │   │   ├── settings.html     # System settings (admin)
│   │   │   │   ├── users.html        # User management (admin)
│   │   │   │   └── security.html     # Audit log viewer (admin)
│   │   │   └── static/              # Frontend assets
│   │   │       ├── css/
│   │   │       │   └── style.css     # Mobile-first dark theme
│   │   │       └── js/
│   │   │           ├── app.js        # Dashboard logic
│   │   │           ├── hls.min.js    # HLS.js library
│   │   │           └── timeline.js   # Recording timeline component
│   │   ├── config/                    # Deployment configs
│   │   │   ├── monitor.service       # systemd unit
│   │   │   ├── nginx-monitor.conf    # nginx site config (TLS)
│   │   │   ├── nftables-server.conf  # Firewall rules
│   │   │   └── logrotate-monitor.conf
│   │   ├── requirements.txt          # Python dependencies
│   │   └── setup.py                  # Package definition
│   │
│   └── camera/                        # RPi Zero 2W camera application
│       ├── camera_streamer/           # Python package
│       │   ├── __init__.py
│       │   ├── main.py               # Entry point
│       │   ├── lifecycle.py          # State machine (INIT→SETUP→PAIRING→...→RUNNING)
│       │   ├── capture.py            # v4l2 capture management
│       │   ├── stream.py             # ffmpeg RTSPS streaming + reconnect
│       │   ├── discovery.py          # Avahi service advertisement
│       │   ├── config.py             # Config file management
│       │   ├── pairing.py            # Certificate exchange during pairing
│       │   ├── ota_agent.py          # Listen for OTA push from server (port 8080)
│       │   ├── factory_reset.py      # WiFi wipe + config reset
│       │   ├── status_server.py      # Post-setup status/admin server
│       │   ├── wifi.py               # WiFi connection management
│       │   └── encryption.py         # LUKS key derivation
│       ├── config/                    # Deployment configs
│       │   ├── camera-streamer.service  # systemd unit
│       │   ├── nftables-camera.conf    # Firewall rules
│       │   └── camera.conf.default     # Default config template
│       ├── requirements.txt
│       └── setup.py
│
├── meta-home-monitor/                 # CUSTOM YOCTO LAYER
│   ├── conf/
│   │   ├── layer.conf
│   │   └── distro/
│   │       └── home-monitor.conf          # Custom distro (replaces poky)
│   ├── classes/
│   │   └── monitor-image.bbclass          # Shared image logic
│   ├── recipes-core/
│   │   ├── images/
│   │   │   ├── home-monitor-image.inc     # Shared server packages
│   │   │   ├── home-monitor-image-dev.bb  # Server dev image
│   │   │   ├── home-monitor-image-prod.bb # Server prod image
│   │   │   ├── home-camera-image.inc      # Shared camera packages
│   │   │   ├── home-camera-image-dev.bb   # Camera dev image
│   │   │   └── home-camera-image-prod.bb  # Camera prod image
│   │   ├── packagegroups/
│   │   │   ├── packagegroup-monitor-base.bb       # Boot, SSH, networking
│   │   │   ├── packagegroup-monitor-video.bb      # ffmpeg, gstreamer, v4l
│   │   │   ├── packagegroup-monitor-web.bb        # nginx, flask, python
│   │   │   ├── packagegroup-monitor-security.bb   # openssl, nftables, LUKS
│   │   │   └── packagegroup-camera-video.bb       # ffmpeg, libcamera, v4l
│   │   └── base-files/
│   │       └── base-files_%.bbappend      # OS branding (/etc/os-release)
│   ├── recipes-monitor/
│   │   └── monitor-server/
│   │       └── monitor-server_1.0.bb      # Packages app/server/ into image
│   ├── recipes-camera/
│   │   └── camera-streamer/
│   │       └── camera-streamer_1.0.bb     # Packages app/camera/ into image
│   ├── recipes-security/
│   │   └── monitor-certs/
│   │       ├── monitor-certs_1.0.bb       # First-boot CA/cert generation
│   │       └── files/
│   │           └── generate-certs.sh
│   └── wic/
│       ├── home-monitor-ab.wks            # A/B partition layout (server)
│       └── home-camera-ab.wks             # A/B partition layout (camera)
│
├── config/                            # YOCTO BUILD CONFIGS
│   ├── bblayers.conf                  # Shared layer config
│   ├── rpi4b/
│   │   └── local.conf                 # Server build config
│   └── zero2w/
│       └── local.conf                 # Camera build config
│
├── scripts/                           # BUILD & UTILITY SCRIPTS
│   ├── setup-env.sh                   # One-time host dependency install
│   ├── build.sh                       # Yocto build (dev/prod × server/camera)
│   └── sign-image.sh                  # Sign .swu images for OTA
│
├── docs/                              # DOCUMENTATION
│   ├── requirements.md                # User needs + SW requirements
│   └── architecture.md                # This file
│
├── CLAUDE.md                          # Project context for development
└── README.md                          # Build instructions + quick start
```

### 7.2 Development vs. Yocto Packaging

```
DEVELOPMENT (fast iteration):                RELEASE (full image rebuild):

  Edit app/server/monitor/app.py               ./scripts/build.sh server-dev
          │                                    ./scripts/build.sh server-prod
          │ rsync                                      │
          ▼                                            │ bitbake
  RPi 4B: /opt/monitor/                                ▼
          │                                    .wic.bz2 image (dev or prod)
          │ systemctl restart monitor
          ▼
  Test in browser
```

Application code lives in `app/` and can be developed, tested, and deployed independently of Yocto. The Yocto recipes in `meta-home-monitor/recipes-*/` simply copy the app code from `app/` into the image during build.

---

## 8. Video Recording Design

### 8.1 Clip Segmentation

```
Continuous RTSP stream from camera
│
▼
ffmpeg receiver (one per camera)
│
├──> HLS output (live view)
│    Rolling 5 segments × 2s = 10s buffer
│    Old segments auto-deleted
│
└──> MP4 segment output (recording)
     New file every 3 minutes
     Filename: /data/recordings/<cam-id>/YYYY-MM-DD/HH-MM-SS.mp4
     Thumbnail: extracted at 1s mark → HH-MM-SS.thumb.jpg
     MP4 moov atom at start (faststart) for instant browser playback
```

### 8.2 Storage Management

```
Storage monitor (runs every 60 seconds):

  1. Check /data partition usage
  2. If usage > threshold (default 90%):
     a. Find oldest clip across all cameras
     b. Skip if clip is < 24 hours old (safety net)
     c. Delete clip + thumbnail
     d. Repeat until under threshold
  3. Report stats to health endpoint
```

### 8.3 Estimated Storage Requirements

| Resolution | Bitrate (est.) | Per Clip (3 min) | Per Day (1 cam) | 32GB SD | 128GB SD |
|---|---|---|---|---|---|
| 720p @ 25fps | ~2 Mbps | ~45 MB | ~21 GB | ~1.2 days | ~5 days |
| 1080p @ 25fps | ~4 Mbps | ~90 MB | ~43 GB | ~0.6 days | ~2.5 days |
| 1080p @ 15fps | ~2.5 Mbps | ~56 MB | ~27 GB | ~1 day | ~4 days |

**Recommendation:** Use a USB SSD (256GB+) for serious recording. SD cards are fine for development and short retention.

---

## 9. First-Boot Flow

### 9.1 Server First Boot

```
1. Boot into rootfsA
2. systemd starts monitor.service
3. App detects /data/config/settings.json missing → first-boot mode
4. generate-certs.sh creates CA + server TLS cert
5. LUKS: if /data not encrypted, prompt for passphrase via serial console
   (or use a default + change later via dashboard)
6. nginx starts with self-signed TLS cert
7. Browser navigates to https://<ip>/
8. Setup wizard:
   a. Create admin account (username + password)
   b. Set timezone (default: Europe/Dublin)
   c. Set hostname
   d. Configure WiFi (if not on Ethernet)
9. Settings saved → redirect to login → dashboard
```

### 9.2 Camera First Boot

```
1. Boot into rootfsA
2. No WiFi configured → start temporary AP: "HomeMonitor-Setup-XXXX"
3. User connects phone to AP, opens http://192.168.4.1/
4. Setup page: select WiFi network, enter password
5. WiFi credentials saved to /data/config/
6. Camera restarts networking, connects to home WiFi
7. Avahi advertises _rtsp._tcp (paired=false)
8. Server discovers camera → shows as "pending" in dashboard
9. Admin clicks "Pair" → pairing flow (cert exchange)
10. Camera starts streaming to server
```

---

## 10. Technology Decisions & Rationale

| Decision | Choice | Why | Alternatives Considered |
|----------|--------|-----|------------------------|
| Distro | Custom `home-monitor` | Product-specific policy, not reference distro | `poky` (reference only, not for products) |
| Image variants | dev + prod | Dev has debug; prod is hardened | Single image (either too open or too locked down) |
| Package organization | Packagegroups | Logical bundles, reusable, maintainable | Giant IMAGE_INSTALL lists (fragile) |
| App language | Python 3 | Already in Yocto, Flask mature, fast to develop | Go (too heavy for Zero 2W Yocto), Node (npm mess) |
| Web framework | Flask | Lightweight, fits on 2GB RPi, good for REST APIs | Django (overkill), FastAPI (async not needed) |
| Live streaming | HLS via nginx | Works on mobile Safari (no alternatives), low latency enough | WebRTC (complex), MPEG-DASH (poor Safari support) |
| Data storage | JSON files | Simple, tiny dataset, no daemon overhead | SQLite (marginal benefit, extra dependency) |
| Camera discovery | Avahi/mDNS | Zero-config, standard, works on LAN | UPnP (complex), manual IP (bad UX) |
| Encryption at rest | LUKS2 + Adiantum (`xchacha20,aes-adiantum-plain64`) | 2-3.5x faster than AES on ARM without hw accel (no AES instructions on RPi 4B/Zero 2W). Google mandates Adiantum for Android without AES. See ADR-0010 | AES-XTS (too slow without hw accel), dm-crypt raw (less features) |
| Boot loader | U-Boot (`u-boot-rpi`) | Boot counting (`bootlimit=3`), `fw_printenv`/`fw_setenv` for A/B slot management. See ADR-0008 | RPi tryboot (limited, no bootcount), direct kernel boot (no rollback) |
| OTA | SWUpdate A/B + app-only symlink swap | Atomic, rollback, file-level handlers for app-only updates, Yocto integration. See ADR-0008 | Mender (needs cloud server), RAUC (less community) |
| TLS | Self-signed CA | No internet dependency, full control | Let's Encrypt (needs port 80 open to internet) |
| Firewall | nftables | Modern replacement for iptables, in-kernel | iptables (legacy), ufw (wrapper, more deps) |

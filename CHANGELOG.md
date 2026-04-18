# Changelog

All notable changes to RPi Home Monitor are documented here.

## [v1.0.6-dev] — 2026-04-10

### Added
- **Camera password authentication** — Camera status page now requires login with username/password set during provisioning. PBKDF2-SHA256 hashing (100k iterations, random 16-byte salt). Session-based auth with HttpOnly cookies and 2-hour timeout.
- **Camera setup collects credentials** — First-boot wizard now asks for admin username and password. These protect the camera's status/settings page.
- **Camera `.local` URL access** — Cameras are reachable via mDNS at `http://rpi-divinu-cam-XXXX.local` (XXXX = last 4 hex of CPU serial). URL shown on:
  - Camera status page (top of page after login)
  - Camera setup wizard (in success message after provisioning)
  - Server dashboard (clickable "Settings" link on each camera card)
- **Camera system health display** — Status page shows CPU temperature, memory usage, and uptime with color-coded thresholds.
- **Camera WiFi change** — Authenticated users can change WiFi network and password from the status page.
- **Camera password change** — Authenticated users can change the camera admin password.
- **25 new tests** — 8 for password management, 17 for session management, provisioning, and system helpers.

### Fixed
- **Server settings WiFi card hidden** — Race condition where `auth.getMe()` async call hadn't completed before settings `init()` checked user role. Fixed by awaiting auth before rendering admin sections.
- **Server settings uptime "[object Object]"** — API returns `{seconds, display}` object; JS was displaying the raw object. Fixed to use `data.uptime.display`.
- **Server settings disk "0 B"** — API returns `disk.total_gb`; JS was using `data.disk.total` (undefined). Fixed to use `total_gb`/`used_gb`/`free_gb` with correct units.

### Changed
- **Camera templates extracted** — Inline HTML (login, status, setup pages) moved from `wifi_setup.py` to separate template files in `templates/` directory. Reduces `wifi_setup.py` from 1573 to 976 lines.
- Camera unique hostname set during first boot via CPU serial suffix for multi-device mDNS support.

## [Unreleased]

### Added
- **Dual-transport OTA, end-to-end validated** (ADR-0020) — three install paths now work on hardware:
  - Server self-upload + install via admin Settings → Updates tab.
  - Server → camera push via admin Cameras tab (uploads bundle to server inbox, relays over mTLS to the camera's :8080 OTAAgent).
  - Camera-direct upload via the camera's own status page (:443 Updates section) for admins on the LAN with the camera's password.
- **Privilege-separated camera installer** — a root-owned `camera-ota-installer.service` activated by a systemd `.path` unit watching `/var/lib/camera-ota/trigger`. Keeps `camera-streamer` unprivileged (`NoNewPrivileges=true`) while still letting OTA writes to `/dev/mmcblk0p2/3`, `fw_setenv`, and `/dev` symlinks happen with the permissions SWUpdate needs.
- **Shared installer module** `camera_streamer.ota_installer` consumed by both transports (`ota_agent.py` on :8080 and the new :443 status-server routes). Single source of truth for stage/trigger/poll. 15 unit tests + integration coverage.
- **Camera Updates UI** — collapsible section on the camera status page with file picker, upload progress, install-state display, and Reboot button. Streams directly to `/api/ota/upload` — the handler never buffers the 128 MB bundle in RAM.
- **Boot-time `/dev/monitor_standby` service** — creates the symlink against the current `boot_slot` at `Before=sysinit.target`, so SWUpdate's `check_free_space` sees the real 2 GiB partition instead of tmpfs and stops rejecting valid installs.
- **mDNS server discovery** — Server advertises itself as `homemonitor.local` via Avahi. Cameras auto-discover the server without needing a manual IP address. Camera setup page defaults to `homemonitor.local`.
- **Captive portal provisioning** — Both server and camera trigger the phone's "Sign in to network" popup on hotspot connect. Supports iOS, Android, Windows, Firefox, and Samsung captive portal detection. Manual fallback at `http://10.42.0.1` always works.
- **LED status feedback** — Onboard ACT LED shows device state:
  - Slow blink (1s) = setup mode, waiting for WiFi config
  - Fast blink (200ms) = connecting to WiFi
  - Very fast blink (100ms) = error, connection failed
  - Solid on = running normally
  - Off = service stopped
- **WiFi rescan button** — Camera setup page can re-scan for networks (briefly drops hotspot).
- **Avahi service file** for server — advertises `_homemonitor._tcp`, `_https._tcp`, and `_http._tcp` services.
- **First-boot hostname** — Server hostname set to `homemonitor` on first boot for mDNS reachability.

### Fixed
- **OTA: camera `sw-description` hardware key** — was `raspberrypi0-2w-64` but `/etc/hwrevision` on the camera image is `home-monitor-camera`. Mismatch caused `swupdate -c` to reject every camera bundle.
- **OTA: post-boot health check probed auth-protected URLs** — `swupdate-check.sh` used `wget` against `/api/v1/ota/status` on the server and a wrong port on the camera. Both returned 401/404, which `wget` treats as failure — so upgrade_available stayed at 1 and U-Boot eventually rolled back a working install. Replaced with a `curl`-based `http_alive()` that accepts any HTTP response (any status ≠ 000 proves the port bound), with 12×5s retry to cover Type=simple service startup race, and pointed the camera probe at the status server on :443.
- **OTA: `swupdate-check.service` stuck behind `network-online.target`** — the Yocto image ships `systemd-networkd-wait-online` enabled, but WiFi is managed by NetworkManager, so networkd has no interfaces and times out, never letting `network-online.target` reach ready. Changed the unit to `After=network.target` (localhost is enough) and bumped `TimeoutStartSec` to 180 s.
- **OTA: camera `ProtectSystem=strict` blocked the spool directory** — `camera-streamer` only had `ReadWritePaths=/data`, but the OTA spool lives at `/var/lib/camera-ota` by design (shared with the privileged installer via tmpfiles.d). Streamer got EROFS staging uploaded bundles. Added the spool dir to the unit's writable paths.
- **OTA: server-push OOM-killed the camera** — the first OTAAgent implementation held the mTLS HTTPS connection open while `swupdate` wrote 1.8 GB to the SD card. On the Pi Zero 2W (362 MB RAM) this pushed the kernel past OOM — camera-streamer, sshd, and getty were all killed and the box needed a physical power cycle. Made the upload handler return 202 Accepted as soon as the trigger file is written; the server polls `/ota/status` for progress. Matches the camera-direct GUI flow that was already stable.
- **OTA: server install picked a stale staged bundle** — `install_server_image()` used `os.listdir()[0]`, which returns filesystem order on Linux. A leftover bundle from a prior aborted install (`server-update-1.1.0-feat-ota-dual.swu`) beat the freshly uploaded one lexicographically (`'1' < 'd'`), so the user's upload was silently ignored and an old version installed. Now picks the newest `.swu` by mtime.
- **OTA: `/dev/monitor_standby` symlink missing on some boots** — the boot-time service runs `Before=sysinit.target` when `/boot` (home of `u-boot.env`) isn't reliably mounted, so `fw_printenv boot_slot` sometimes returned the wrong slot and the symlink pointed at the running partition, tripping SWUpdate's `check_free_space` on the next install. The privileged installer now refreshes the symlink itself before invoking swupdate — no longer relies on the boot service being timely.
- **Hotspot startup race condition** — `nmcli connection up` was called before wlan0 was ready at boot, causing "No suitable device found" error. Now waits for WiFi interface readiness (up to 30s) and retries activation (5 attempts). Explicit `ifname wlan0` passed to prevent NM from trying eth0.
- **NGINX HTTP redirect loop** — Setup wizard was inaccessible because HTTP 80 redirected to HTTPS 443, but TLS certs don't exist during first boot. HTTP now serves directly.
- **Camera WiFi scan** — Scan button now triggers a real WiFi rescan instead of showing cached results.

### Changed
- Server and camera systemd services now depend on `sys-subsystem-net-devices-wlan0.device` to ensure WiFi hardware is ready.
- Server hotspot service has `TimeoutStartSec=90` to allow for WiFi retry loop.
- Camera setup page server address field defaults to `homemonitor.local` instead of empty.

---

## Setup Guide

### Part 1: Server Setup (RPi 4B)

1. **Power on** — Insert SD card, plug in power, wait ~60 seconds. LED starts slow blinking (setup mode).
2. **Connect to hotspot** — On your phone, connect to WiFi `HomeMonitor-Setup` (password: `homemonitor`).
3. **Setup wizard opens automatically** — Your phone should show a "Sign in to network" popup. If not, open `http://10.42.0.1` in a browser.
4. **Configure WiFi** — Select your home WiFi network, enter password, hit Connect.
5. **Set admin password** — Change the default admin password (minimum 8 characters).
6. **Complete setup** — Hit Complete. The server stops the hotspot and joins your home WiFi. LED goes solid (connected). You will lose connection to the hotspot — this is normal.
7. **Reconnect** — Connect your phone back to your home WiFi.
8. **Open dashboard** — Go to `https://homemonitor.local` (accept the self-signed cert warning). If `.local` doesn't resolve on your network, find the server IP from your router's DHCP table.
9. **Log in** — Username: `admin`, Password: what you set in step 5.

### Part 2: Camera Setup (RPi Zero 2W)

1. **Attach camera** — Connect PiHut ZeroCam ribbon cable (blue side faces the board).
2. **Power on** — Insert SD card, plug in power, wait ~90 seconds (Zero 2W is slower). LED starts slow blinking (setup mode).
3. **Connect to hotspot** — On your phone, connect to WiFi `HomeCam-Setup` (password: `homecamera`).
4. **Setup wizard opens automatically** — Your phone shows the "Sign in to network" popup. If not, open `http://10.42.0.1` in a browser.
5. **Configure WiFi** — Select your home WiFi network, enter password.
6. **Server address** — Leave as `rpi-divinu.local` (auto-discovery). Only change this if mDNS doesn't work on your network — in that case enter the server's IP address. Port: leave as `8554`.
7. **Set camera login** — Choose a username (default: `admin`) and password (min 4 characters). You'll need these to access the camera's settings page later.
8. **Save & Connect** — LED switches to fast blink (connecting), then solid on (connected). The hotspot disappears. A `.local` URL is shown (e.g., `http://rpi-divinu-cam-d8ee.local`) — bookmark it for future access. If connection fails, LED blinks rapidly and the hotspot restarts automatically for retry.

### Part 3: Pair Camera on Server

1. **Reconnect** — Connect your phone back to your home WiFi.
2. **Open dashboard** — Go to `https://homemonitor.local` and log in.
3. **Confirm camera** — The camera appears as "pending" on the Dashboard (wait up to 30 seconds, refresh if needed). Click it and hit Confirm. Give it a name and location.
4. **Streaming starts** — HLS live view + 3-minute MP4 clips begin recording automatically.

### LED Quick Reference

| LED Pattern | Server | Camera |
|-------------|--------|--------|
| Slow blink (1s on/off) | Setup mode — hotspot active, waiting for WiFi config | Same |
| Fast blink (200ms) | — | Connecting to WiFi |
| Very fast blink (100ms) | — | WiFi connection failed, retrying |
| Solid on | Running normally | Running normally, streaming to server |
| Off | Service stopped | Service stopped |

### Troubleshooting

| Problem | Solution |
|---------|----------|
| Captive portal doesn't pop up | Open `http://10.42.0.1` manually in your browser |
| `homemonitor.local` doesn't resolve | Use the server's IP address from your router's DHCP table instead |
| Camera can't find server | Enter the server IP manually instead of `homemonitor.local` during camera setup |
| Hotspot doesn't appear | Wait 60-90 seconds after power on. Check LED — slow blink means hotspot is active |
| LED stays off after boot | Service may have failed. Connect via SSH and check `journalctl -u monitor-hotspot` (server) or `journalctl -u camera-streamer` (camera) |

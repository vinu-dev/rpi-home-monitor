# Changelog

All notable changes to RPi Home Monitor are documented here.

## [Unreleased]

### Added
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
6. **Server address** — Leave as `homemonitor.local` (auto-discovery). Only change this if mDNS doesn't work on your network — in that case enter the server's IP address. Port: leave as `8554`.
7. **Save & Connect** — LED switches to fast blink (connecting), then solid on (connected). The hotspot disappears. If connection fails, LED blinks rapidly and the hotspot restarts automatically for retry.

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

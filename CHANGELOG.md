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

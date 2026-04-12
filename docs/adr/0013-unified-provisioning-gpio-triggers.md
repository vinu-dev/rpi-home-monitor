# ADR-0013: Unified Provisioning Architecture and GPIO Triggers

## Status
Proposed

## Context
Server and camera have inconsistent first-boot provisioning implementations:

- **Server**: Systemd service (`monitor-hotspot.service`) manages the WiFi hotspot lifecycle, Flask provides the setup wizard UI. Clean separation of system and application concerns.
- **Camera**: Python application code (`wifi_setup.py`) directly manages the hotspot via nmcli calls, mixing system infrastructure with application logic.

Factory reset WiFi clearing is duplicated with different implementations in `factory_reset_service.py` (server) and `status_server.py` (camera).

There is no physical override mechanism to force provisioning mode or factory reset without software access (SSH, dashboard).

### Industry research (2026-04-12)

| Product | Provisioning Method | Hub vs Spoke | Reset Mechanism |
|---------|-------------------|--------------|-----------------|
| Home Assistant OS | Ethernet-first, no hotspot | Hub only | Re-flash SD |
| Balena wifi-connect | SoftAP + captive portal | Spoke | Auto re-enter AP on WiFi loss |
| ESPHome | SoftAP fallback after 60s WiFi failure | Spoke | Auto AP on WiFi failure |
| Tasmota | SoftAP primary on first boot | Spoke | 40s button hold |
| OpenWRT | Ethernet to 192.168.1.1 | Router | Reset button (10s hold) |
| Ring Camera | SoftAP + companion app | Spoke | 20s button hold |

**Key industry patterns:**
1. WiFi credentials first, then authentication — every product gets network connectivity before asking for passwords
2. Hub devices prefer wired setup; spoke devices use hotspot
3. Automatic re-entry to provisioning on WiFi failure (ESPHome, Tasmota, Balena)
4. Physical reset mechanism (GPIO button/jumper) is standard for embedded devices

## Decision

### 1. Unified hotspot architecture (both boards)

Both server and camera follow the same pattern:

| Layer | Responsibility |
|-------|---------------|
| `*-hotspot.service` (systemd oneshot) | Hotspot lifecycle: start/stop/status |
| `*-hotspot.sh` (shell script) | nmcli commands: AP create/destroy, WiFi connect, credential wipe |
| Application (Flask / Python HTTP) | Setup wizard UI only — no system infrastructure management |

Hotspot activation is always systemd's responsibility via `ConditionPathExists=!/data/.setup-done`. Applications never start or stop the hotspot directly — they call the shell script.

### 2. Provisioning wizard order

**Server** (the hub — has Ethernet fallback):
1. WiFi network selection + password
2. Admin password creation (NIST policy: 12+ chars)
3. Device name (optional)
4. Review and complete

**Camera** (spoke — connects to server):
1. WiFi network selection + password
2. Server address (IP or hostname)
3. Camera admin password
4. Review and complete

WiFi-first matches industry standard: the device needs network before it can do anything useful.

### 3. GPIO triggers (platform-abstracted)

Two GPIO jumper pairs provide physical override at boot:

| Purpose | BCM GPIO | Physical Pin | Adjacent GND | Default Pull |
|---------|----------|-------------|-------------|-------------|
| Force provisioning | GPIO5 | 29 | 30 | Hardware pull-UP (BCM2711/BCM2710) |
| Factory reset | GPIO27 | 13 | 14 | Requires `gpio=27=ip,pu` in config.txt |

**Detection**: An early systemd service (`gpio-trigger.service`) reads GPIO state once at boot before hotspot and application services start.

**Behavior**:
- GPIO5 LOW (jumper present): Delete `.setup-done` only → enter provisioning. Data preserved.
- GPIO27 LOW (jumper present): Full data wipe + delete `.setup-done` → enter provisioning. Equivalent to fresh flash.
- Both HIGH (no jumper): Normal boot.

**Platform abstraction**: GPIO pin numbers are not hardcoded. They are read from:
1. Environment variables (`GPIO_PROVISION_PIN`, `GPIO_RESET_PIN`) — highest priority
2. Defaults in the trigger script (GPIO5, GPIO27) — matches hardware design

Both RPi 4B and Zero 2W share the identical 40-pin header with the same BCM numbering.

### 4. Hotspot script commands

Both `monitor-hotspot.sh` and `camera-hotspot.sh` support:

| Command | Action |
|---------|--------|
| `start` | Create and activate WiFi AP |
| `stop` | Tear down WiFi AP |
| `status` | Check if AP is active |
| `connect <ssid> <password>` | Stop AP, connect to WiFi, return success/fail |
| `wipe` | Delete all NM saved connections + reset wpa_supplicant |

The `connect` command handles the AP-to-client transition atomically. The `wipe` command is called by factory reset instead of inline nmcli code.

### 5. Factory reset (unified)

Both boards use a proper `FactoryResetService` class (constructor injection, single responsibility):

1. Log audit event (server only — camera has no audit service)
2. Wipe `/data` contents (config, certs, logs, recordings, etc.)
3. Call `*-hotspot.sh wipe` to clear WiFi credentials
4. Delete `.setup-done` stamp
5. Schedule system reboot
6. On next boot: systemd `ConditionPathExists` starts hotspot → provisioning wizard

### 6. All provisioning entry points

| Trigger | Effect | Use Case |
|---------|--------|----------|
| First boot (no `.setup-done`) | Hotspot + wizard | New device |
| GPIO5 jumper at boot | Delete stamp → provisioning | Reconfigure without data loss |
| GPIO27 jumper at boot | Full wipe → provisioning | True factory reset |
| Software factory reset (API) | Full wipe → reboot → provisioning | Remote reset from dashboard |
| WiFi lost 60s (camera only) | Auto re-enter hotspot | WiFi password changed |

## Consequences

### Positive
- Consistent architecture: both boards use identical patterns for hotspot, provisioning, factory reset
- System concerns in systemd: applications don't manage infrastructure
- Physical override: no software access needed for recovery
- Industry-aligned: WiFi-first order, auto-fallback, GPIO reset match embedded Linux best practices
- Testable: service classes with constructor injection, shell scripts independently testable

### Negative
- Camera hotspot is no longer managed by the Python app — requires the systemd service to be installed (Yocto recipe change)
- GPIO requires hardware modification (jumper wires) — but this is standard for embedded development
- `config.txt` must include `gpio=27=ip,pu` for factory reset pin pull-up

### Migration
- Camera's `wifi_setup.py` is refactored to remove hotspot start/stop — only HTTP wizard remains
- Camera's inline factory reset in `status_server.py` is extracted to `FactoryResetService`
- Server's `ProvisioningService` uses hotspot script's `connect` command instead of direct nmcli
- Server's `FactoryResetService` uses hotspot script's `wipe` command instead of inline NM cleanup

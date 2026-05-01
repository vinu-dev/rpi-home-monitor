# Hardware Architecture

Status: Draft prepared to support expert regulatory review.

## Hardware Architecture Items

| ID | Item | Description | Requirements | Risks | Security controls | Tests | Source |
|---|---|---|---|---|---|---|---|
| HWA-001 | Server node | Raspberry Pi 4B-class server running the monitor image, web UI, recorder, storage, and OTA. | HWR-001, HWR-003, HWR-004 | RISK-001, RISK-003, RISK-008 | SC-004, SC-005 | TC-003, TC-018 | `docs/hardware-setup.md`, `meta-home-monitor/recipes-core/images/home-monitor-image.inc` |
| HWA-002 | Camera node | Raspberry Pi Zero 2W-class node with supported camera module, encoder constraints, status UI, and stream pipeline. | HWR-002, HWR-007 | RISK-001, RISK-007, RISK-008 | SC-002, SC-004 | TC-012, TC-018, TC-019 | `docs/hardware-setup.md`, `app/camera/` |
| HWA-003 | Local network | WiFi/Ethernet LAN with mDNS discovery, HTTPS, RTSPS, and optional VPN remote access. | HWR-003 | RISK-002, RISK-005 | SC-002, SC-004 | TC-006, TC-008 | `docs/connectivity-and-privacy-constraints.md` |
| HWA-004 | Persistent storage | SD storage and optional USB recording storage under `/data` and configured recording paths. | HWR-004 | RISK-003, RISK-006 | SC-005 | TC-003, TC-018 | `docs/architecture.md` |
| HWA-005 | Recovery inputs | Hardware reset/reflash path for recovery instead of pre-auth software backdoor recovery. | HWR-006 | RISK-006 | SC-006 | TC-015, TC-018 | ADR-0022 |
| HWA-006 | Time and power assumptions | Stable power and local time synchronization support reliable logs, clips, OTA, and alerts. | HWR-005, HWR-008 | RISK-005, RISK-008 | SC-008 | TC-017, TC-018 | ADR-0019, `docs/hardware-setup.md` |

## Hardware/Software Boundaries

- Camera sensors and encoder capabilities are discovered and filtered before
  being exposed as selectable modes.
- Hardware reset is treated as the recovery boundary for lost privileged
  access.
- Power, WiFi, SD, and USB failures are treated as foreseeable operational
  hazards requiring operator visibility.

## Open Questions

- OPEN QUESTION: Define minimum supported SD card endurance and USB storage
  requirements for production deployments.
- OPEN QUESTION: Define environmental operating limits for temperature,
  humidity, power quality, and WiFi signal strength.

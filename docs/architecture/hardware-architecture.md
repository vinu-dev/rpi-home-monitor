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
| HWA-007 | Indicators and reset controls | Camera-local LEDs/status surfaces and hardware-mediated recovery inputs support setup and troubleshooting. | HWR-010, HWR-006 | RISK-022, RISK-006 | SC-006, SC-012 | TC-039, TC-047 | ADR-0013, ADR-0022 |
| HWA-008 | Removable media boundary | Operator-provided USB media and SD storage are treated as recording and evidence boundaries with safe mount/eject assumptions. | HWR-011, HWR-014 | RISK-003, RISK-013, RISK-014 | SC-013, SC-014, SC-005 | TC-024, TC-025, TC-026 | `usb.py`, `docs/hardware-setup.md` |
| HWA-009 | Network service exposure | Server and camera roles expose only documented LAN, HTTPS/RTSPS/WebRTC/mDNS, and optional VPN services. | HWR-012, HWR-015 | RISK-017, RISK-018 | SC-004, SC-016, SC-019 | TC-010, TC-028, TC-044 | firewall configs, ADR-0005 |
| HWA-010 | Build and signing environment | Build host, release scripts, and signing-key storage are part of the artifact integrity boundary. | HWR-018 | RISK-018, RISK-019 | SC-018, SC-017, SC-019 | TC-043, TC-045 | release workflows, `docs/ota-key-management.md` |
| HWA-011 | Sensor and environmental envelope | Camera sensor, CSI cable, lens, thermal, power, bitrate, and camera-count constraints define safe operating envelopes. | HWR-009, HWR-013, HWR-017 | RISK-001, RISK-007, RISK-022 | SC-004 | TC-012, TC-018, TC-047 | `sensor_info.py`, `docs/hardware-setup.md` |
| HWA-012 | Physical deployment boundary | Enclosure, port exposure, SD/USB access, reset access, and camera placement remain deployment assumptions requiring review. | HWR-016 | RISK-006, RISK-019, RISK-020 | SC-005, SC-006, SC-017 | TC-015, TC-032, TC-047 | ADR-0010, ADR-0022 |

## Hardware/Software Boundaries

- Camera sensors and encoder capabilities are discovered and filtered before
  being exposed as selectable modes.
- Hardware reset is treated as the recovery boundary for lost privileged
  access.
- Power, WiFi, SD, and USB failures are treated as foreseeable operational
  hazards requiring operator visibility.
- Build/signing hosts and removable update media are included in the hardware
  boundary because they can affect deployed artifact integrity.
- Physical access can invalidate some confidentiality assumptions; deployment
  records must document accepted residual risk.

## Open Questions

- OPEN QUESTION: Define minimum supported SD card endurance and USB storage
  requirements for production deployments.
- OPEN QUESTION: Define environmental operating limits for temperature,
  humidity, power quality, and WiFi signal strength.
- OPEN QUESTION: Define maximum supported camera count and bitrate by server
  hardware profile.
- OPEN QUESTION: Define whether production deployments require a tamper-evident
  enclosure, locked storage, or port-blocking guidance.

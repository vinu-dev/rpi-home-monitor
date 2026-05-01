# Cybersecurity Threat Model

Status: Draft prepared to support expert regulatory review.

Method: STRIDE-style review of local-first system boundaries.

## Trust Boundaries

| Boundary | Description | Related architecture |
|---|---|---|
| Browser to server | Operator browser crosses into authenticated server UI/API. | ARCH-001, SWA-002 |
| Camera to server | Paired camera uses signed heartbeats, mTLS/control paths, RTSPS streams. | ARCH-001, SWA-003 |
| Server to storage | Application writes recordings, config, audit, certs, OTA staging. | ARCH-003, SWA-005 |
| Admin to OTA | Admin upload/install crosses into privileged update execution. | ARCH-004, SWA-006 |
| LAN and optional VPN | Local network and optional operator-managed remote access expose services. | HWA-003, SWA-010 |

## Threats and Controls

| Threat ID | STRIDE | Threat | Attack surface | Impact | Security controls | Linked requirements | Linked tests | Status |
|---|---|---|---|---|---|---|---|---|
| THREAT-001 | Spoofing/Elevation | Attacker authenticates as an operator or reuses a session. | Login, cookies, API, CSRF. | Unauthorized video/control access. | SC-001, SC-006, SC-008 | SYS-004, SWR-001, SWR-002 | TC-004, TC-011 | Draft |
| THREAT-002 | Spoofing/Tampering | Rogue device impersonates a camera or machine client. | Pairing, heartbeat, control, stream. | Fake status/video or unauthorized commands. | SC-002 | SYS-005, SWR-003, SWR-004 | TC-008, TC-012 | Draft |
| THREAT-003 | Tampering/Elevation | Malicious or corrupted update bundle is installed. | OTA upload, staging, install. | Persistent compromise or bricked device. | SC-003 | SYS-009, SWR-010 | TC-009, TC-013 | Draft |
| THREAT-004 | Information disclosure | LAN attacker or compromised device scans open ports or observes traffic. | HTTPS/RTSPS/mDNS/firewall. | Video/config exposure. | SC-004, SC-002 | SYS-001, SYS-004, SWR-020 | TC-006, TC-010, TC-016 | Draft |
| THREAT-005 | Information disclosure | Device theft exposes recordings, WiFi, certs, and settings. | SD card, USB storage, `/data`. | Privacy and credential loss. | SC-005, SC-006 | SYS-008, HWR-004, HWR-006 | TC-015, TC-018 | Draft |
| THREAT-006 | Repudiation | Security or admin action is not logged. | Auth, user management, OTA, pairing. | Incident investigation gaps. | SC-008 | SYS-010, SWR-009 | TC-017 | Draft |
| THREAT-007 | Supply chain | Dependency, workflow, or Yocto input has a known vulnerability or malicious change. | Python deps, Yocto recipes, GitHub Actions. | Compromise through build or runtime dependency. | SC-007, SC-009 | SYS-012, SWR-019 | TC-020 | Draft |
| THREAT-008 | Elevation | Recovery shortcut or debug access bypasses primary auth. | Pre-auth UI, SSH, CLI scripts, reset tools. | Persistent unauthorized admin access. | SC-006 | SYS-008, SWR-018 | TC-011, TC-015 | Draft |

## Assumptions

- ASSUMPTION: Operator-controlled LAN is hostile enough to require auth and
  transport controls.
- ASSUMPTION: Physical access can defeat some controls, but the product must
  not intentionally add software backdoors.

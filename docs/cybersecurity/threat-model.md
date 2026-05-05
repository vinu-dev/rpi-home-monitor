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
| First-run setup | Pre-auth setup and default identity cross into authenticated runtime state. | ARCH-007, SWA-011 |
| User administration | Authenticated admin actions cross into password, role, and session trust state. | ARCH-008, SWA-013 |
| Removable media | Operator USB devices and media requests cross into filesystem operations. | ARCH-009, SWA-015, SWA-016 |
| Live transport proxy | Browser WHEP/HLS/snapshot requests cross into local upstream stream services. | ARCH-010, SWA-017 |
| Public share links | Admin-issued links cross from authenticated share management into unauthenticated, token-scoped recipient media access. | ARCH-016, SWA-026 |
| Build/release pipeline | Maintainer/build host actions cross into signed deployable artifacts. | ARCH-013, SWA-024 |

## Threats and Controls

| Threat ID | STRIDE | Threat | Attack surface | Impact | Security controls | Linked requirements | Linked tests | Status |
|---|---|---|---|---|---|---|---|---|
| THREAT-001 | Spoofing/Elevation | Attacker authenticates as an operator or reuses a session. | Login, cookies, API, CSRF. | Unauthorized video/control access. | SC-001, SC-006, SC-008 | SYS-004, SWR-001, SWR-002 | TC-004, TC-011 | Draft |
| THREAT-002 | Spoofing/Tampering | Rogue device impersonates a camera or machine client. | Pairing, heartbeat, control, stream. | Fake status/video or unauthorized commands. | SC-002 (including pinned camera control cert verification) | SYS-005, SWR-003, SWR-004 | TC-008, TC-012 | Draft |
| THREAT-003 | Tampering/Elevation | Malicious or corrupted update bundle is installed. | OTA upload, staging, install. | Persistent compromise or bricked device. | SC-003 | SYS-009, SWR-010 | TC-009, TC-013 | Draft |
| THREAT-004 | Information disclosure | LAN attacker or compromised device scans open ports, observes traffic, or tries to stand in for the camera control endpoint. | HTTPS/RTSPS/mDNS/firewall/control channel. | Video/config exposure or forged control acceptance. | SC-004, SC-002 | SYS-001, SYS-004, SWR-020 | TC-006, TC-010, TC-016 | Draft |
| THREAT-005 | Information disclosure | Device theft exposes recordings, WiFi, certs, and settings. | SD card, USB storage, `/data`. | Privacy and credential loss. | SC-005, SC-006 | SYS-008, HWR-004, HWR-006 | TC-015, TC-018 | Draft |
| THREAT-006 | Repudiation | Security or admin action is not logged. | Auth, user management, OTA, pairing. | Incident investigation gaps. | SC-008 | SYS-010, SWR-009 | TC-017 | Draft |
| THREAT-007 | Supply chain | Dependency, workflow, or Yocto input has a known vulnerability or malicious change. | Python deps, Yocto recipes, GitHub Actions. | Compromise through build or runtime dependency. | SC-007, SC-009 | SYS-012, SWR-019 | TC-020 | Draft |
| THREAT-008 | Elevation | Recovery shortcut or debug access bypasses primary auth. | Pre-auth UI, SSH, CLI scripts, reset tools. | Persistent unauthorized admin access. | SC-006 | SYS-008, SWR-018 | TC-011, TC-015 | Draft |
| THREAT-009 | Spoofing/Elevation | Default credentials, incomplete setup state, or personal default identity is exposed. | First-run setup, provisioning defaults, mDNS hostname. | Unauthorized first-use access or privacy disclosure. | SC-010, SC-019 | SYS-013, SYS-024, SWR-021, SWR-054 | TC-021, TC-044 | Draft |
| THREAT-010 | Elevation/Tampering | User-management API allows privilege escalation, weak passwords, or last-admin deletion. | Users API, user store, password-change routes. | Lockout or unauthorized admin control. | SC-011, SC-001, SC-008 | SYS-014, SWR-023 | TC-022, TC-011 | Draft |
| THREAT-011 | Tampering/Information disclosure | Time, timezone, WiFi, hostname, or network settings are manipulated or leaked. | Settings API, camera WiFi setup, logs. | Broken evidence, connectivity failure, credential disclosure. | SC-012, SC-020 | SYS-015, SWR-024, SWR-035, SWR-036 | TC-023, TC-033, TC-034, TC-041 | Draft |
| THREAT-012 | Tampering/Denial | Removable storage operation targets wrong media or silently falls back. | USB scan/mount/format/eject/select. | Recording loss or unrelated media damage. | SC-013, SC-005 | SYS-016, SWR-027, SWR-028 | TC-024, TC-025 | Draft |
| THREAT-013 | Information disclosure/Tampering | Media file routes allow path traversal or overbroad deletion. | Recording delete, live playlist/segment/snapshot routes. | Sensitive file disclosure or data loss. | SC-014, SC-008 | SYS-026, SWR-029, SWR-030 | TC-026, TC-027 | Draft |
| THREAT-014 | Information disclosure | Rich motion notification includes excessive media or retains it too long. | Motion events, alert center, notification attachments. | Privacy disclosure. | SC-015, SC-020 | SYS-018, SWR-033, SWR-041 | TC-031, TC-038 | Draft |
| THREAT-015 | Tampering/Elevation | WebRTC/WHEP proxy reaches unintended upstream or bypasses auth/method limits. | WebRTC proxy, HLS fallback, optional VPN. | SSRF, unauthorized stream access, or control path exposure. | SC-016, SC-004 | SYS-019, SYS-029, SWR-031, SWR-052 | TC-028, TC-010, TC-044 | Draft |
| THREAT-016 | Information disclosure/Tampering | Certificate, pairing secret, encryption key, or OTA signing material is exposed or mishandled. | Cert store, pairing config, key files, release host. | Impersonation or malicious update. | SC-017, SC-005, SC-018 | SYS-028, SWR-034, SWR-043 | TC-032, TC-040, TC-043 | Draft |
| THREAT-017 | Supply chain/Tampering | Build workflow, dependency input, SBOM, or release artifact is compromised or unverifiable. | GitHub Actions, scripts, Yocto, Python dependencies. | Vulnerable or malicious release. | SC-018, SC-007, SC-009 | SYS-023, SWR-046, SWR-047, SWR-048 | TC-043, TC-045 | Draft |
| THREAT-018 | Elevation | Production image includes development credentials, debug paths, or weak service hardening. | Yocto configs, systemd units, firewall, default credentials. | Unauthorized access or larger blast radius. | SC-019, SC-018 | SYS-024, SYS-030, SWR-049, SWR-050 | TC-044, TC-047 | Draft |
| THREAT-019 | Information disclosure/Repudiation | Logs, faults, audit, summary records, or diagnostics bundles leak secrets or omit necessary evidence. | Runtime logs, audit store, alert/fault records, diagnostics export bundles. | Privacy leak or investigation gap. | SC-020, SC-008, SC-025 | SYS-022, SYS-020, SYS-034, SWR-044, SWR-051, SWR-068, SWR-069, SWR-070 | TC-017, TC-029, TC-041, TC-046, TC-055 | Draft |
| THREAT-020 | Tampering/Denial | Public API contract drift breaks deployed camera, browser, or automation clients. | Server/camera API schemas and routes. | Loss of monitoring, pairing, or update functions. | SC-021 | SYS-027, SWR-045 | TC-042 | Draft |
| THREAT-021 | Information disclosure/Spoofing/Denial | Share token theft, brute force, or replay reaches token-scoped media. | Public share URLs, recipient browsers, unauthenticated public routes. | Unauthorized clip/live viewing or noisy abuse against public viewers. | SC-022, SC-024 | SYS-032, SWR-058, SWR-059, SWR-060 | TC-050, TC-051, TC-052 | Draft |
| THREAT-022 | Elevation/Information disclosure | Public share routes bypass intended scope or expose dashboard state beyond the shared resource. | Public share viewer templates, token validation, media asset routing. | Recipients pivot into unrelated media, metadata, or privileged surfaces. | SC-023, SC-024 | SYS-032, SWR-059, SWR-061 | TC-051, TC-052, TC-053 | Draft |
| THREAT-023 | Supply chain/Information disclosure | Dependency resolution or release evidence allows a known-vulnerable Flask build to enter the server install path even though application code is unchanged. | `requirements.txt`, editable package installs, SBOM generation, Yocto image manifests, and release-validation workflows. | Session-cache poisoning exposure or untrustworthy vulnerability posture. | SC-026, SC-018, SC-007 | SYS-023, SYS-035, SWR-071 | TC-043, TC-056 | Draft |

## Audit Events

- `TIME_SET_MANUAL`: Admin changed the server wall clock through the settings flow. Supports THREAT-011 and THREAT-019 investigation needs.
- `TIME_RESYNC_REQUESTED`: Admin requested an NTP/time-sync restart on the server or queued a camera resync. Supports THREAT-011 and THREAT-019 investigation needs.
- `DIAGNOSTICS_EXPORTED`: Admin downloaded a diagnostics bundle. Supports THREAT-019 investigation needs for evidence handoff and export accountability.
- `DIAGNOSTICS_EXPORT_FAILED`: Diagnostics export failed before delivery. Supports THREAT-019 investigation needs for missing evidence or degraded export behavior.

## Assumptions

- ASSUMPTION: Operator-controlled LAN is hostile enough to require auth and
  transport controls.
- ASSUMPTION: Physical access can defeat some controls, but the product must
  not intentionally add software backdoors.

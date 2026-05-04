# Cybersecurity Plan

Status: Draft prepared to support expert regulatory review.

## Purpose

This plan defines how cybersecurity risks, controls, verification, SBOM, and
vulnerability handling are recorded for this repository. It does not claim
compliance with any cybersecurity regulation or standard.

## Assets

| ID | Asset | Description | Related requirements |
|---|---|---|---|
| SEC-001 | Live and recorded video | Sensitive household video streams and clips. | SYS-001, SYS-002, SYS-004 |
| SEC-002 | Operator accounts and sessions | Password hashes, roles, session cookies, CSRF tokens, TOTP secrets, and recovery-code hashes. | SYS-004, SWR-001, SWR-002 |
| SEC-003 | Camera credentials and pairing secrets | Client certs, CA cert, keys, pairing secret. | SYS-005, SWR-003, SWR-004 |
| SEC-004 | OTA signing and update path | SWUpdate bundles, signing certs, staging dirs, update status. | SYS-009, SWR-010 |
| SEC-005 | Persistent config and logs | `/data/config`, audit logs, settings, camera registry. | SYS-003, SYS-010 |
| SEC-006 | Local network and optional VPN access | LAN, mDNS, HTTPS/RTSPS, optional Tailscale. | SYS-001, SYS-004, SWR-020 |
| SEC-007 | Build and dependency inputs | Python dependencies, Yocto recipes, workflow dependencies, SBOM records. | SYS-012, SWR-019 |
| SEC-008 | Provisioning and default identity | Setup-complete state, initial admin path, default hostnames, and development credential boundaries. | SYS-013, SYS-024, SWR-021, SWR-054 |
| SEC-009 | Local settings and WiFi credentials | Time, timezone, WiFi SSID/password, hostname, and network configuration. | SYS-015, SWR-024, SWR-036 |
| SEC-010 | Removable storage and media paths | USB block devices, selected recording target, live media files, and delete operations. | SYS-016, SYS-026, SWR-027, SWR-029 |
| SEC-011 | Motion notification media | Motion-event metadata, thumbnails, clips, notification records, and read-state metadata. | SYS-018, SWR-033, SWR-041 |
| SEC-012 | Live transport proxy | WebRTC/WHEP proxy, HLS playlists, snapshots, and upstream stream endpoints. | SYS-019, SYS-029, SWR-030, SWR-031 |
| SEC-013 | Release and signing pipeline | Version files, release workflows, SBOM outputs, signing keys, update bundles, and release artifacts. | SYS-023, SYS-028, SWR-046, SWR-047 |
| SEC-014 | Runtime evidence records | Logs, audit events, faults, health telemetry, and system summaries. | SYS-020, SYS-022, SWR-044, SWR-051 |
| SEC-015 | Production/development profiles | Dev credentials, production image profile, debug paths, service hardening, and firewall/service exposure. | SYS-024, SYS-030, SWR-049, SWR-050 |
| SEC-016 | Public API contracts | Server API, camera API, machine-client schemas, and browser/API compatibility records. | SYS-027, SWR-045 |
| SEC-017 | Public share-link surfaces | Share-link tokens, recipient viewer routes, share metadata, and unauthenticated token-scoped media delivery. | SYS-032, SWR-058, SWR-059, SWR-060, SWR-061 |

## Security Objectives

- Preserve confidentiality of video, credentials, keys, and config.
- Preserve integrity of camera enrollment, commands, recordings, audit logs,
  and update bundles.
- Preserve availability of local monitoring and update recovery paths.
- Avoid software backdoors and insecure recovery shortcuts.
- Maintain traceable security requirements and controls.

## Security Review Triggers

Security review is required for changes to:

- authentication, authorization, sessions, CSRF, password policy
- pairing, certificates, HMAC, mTLS, key storage
- OTA signing, staging, install, rollback, or update secrets
- firewall, exposed ports, TLS, mDNS, Tailscale, remote access
- recovery, factory reset, debug access, developer credentials
- SBOM, dependency, or build pipeline inputs
- provisioning/setup defaults, hostnames, or device identity
- USB/media deletion, live media serving, WebRTC proxy, or notification media
- public share links, recipient viewers, or token-scoped media delivery
- production/development profile, systemd hardening, firewall, or release
  promotion behavior
- logging, fault, audit, or telemetry fields that may contain secrets or
  personal data

## Open Questions

- OPEN QUESTION: Define the responsible owner for vulnerability triage and
  security advisory publishing.
- OPEN QUESTION: Decide whether formal penetration testing is required before
  production release.

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
| SEC-002 | Operator accounts and sessions | Password hashes, roles, session cookies, CSRF tokens. | SYS-004, SWR-001, SWR-002 |
| SEC-003 | Camera credentials and pairing secrets | Client certs, CA cert, keys, pairing secret. | SYS-005, SWR-003, SWR-004 |
| SEC-004 | OTA signing and update path | SWUpdate bundles, signing certs, staging dirs, update status. | SYS-009, SWR-010 |
| SEC-005 | Persistent config and logs | `/data/config`, audit logs, settings, camera registry. | SYS-003, SYS-010 |
| SEC-006 | Local network and optional VPN access | LAN, mDNS, HTTPS/RTSPS, optional Tailscale. | SYS-001, SYS-004, SWR-020 |
| SEC-007 | Build and dependency inputs | Python dependencies, Yocto recipes, workflow dependencies, SBOM records. | SYS-012, SWR-019 |

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

## Open Questions

- OPEN QUESTION: Define the responsible owner for vulnerability triage and
  security advisory publishing.
- OPEN QUESTION: Decide whether formal penetration testing is required before
  production release.

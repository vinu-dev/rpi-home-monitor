# Security Risk Analysis

Status: Draft prepared to support expert regulatory review.

| Control ID | Control statement | Linked threats | Linked requirements | Linked architecture | Linked code | Linked tests | Residual cybersecurity risk | Status |
|---|---|---|---|---|---|---|---|---|
| SC-001 | Enforce authenticated sessions, role checks, CSRF tokens, password hashing, login rate limits, and lockout. | THREAT-001 | SYS-004, SWR-001, SWR-002 | SWA-002 | `app/server/monitor/auth.py` | TC-004, TC-011 | Medium | Draft |
| SC-002 | Use pairing, certificates, HMAC signatures, and mTLS/control endpoint validation for camera/server trust. | THREAT-002, THREAT-004 | SYS-005, SWR-003, SWR-004 | SWA-003 | `pairing_service.py`, `pairing.py`, `heartbeat.py`, `status_server.py` | TC-008, TC-012 | Medium | Draft |
| SC-003 | Require documented SWUpdate verification, staging checks, signed production OTA flow, and update status controls. | THREAT-003 | SYS-009, SWR-010 | SWA-006 | `ota_service.py`, `ota_installer.py` | TC-009, TC-013 | Medium | Draft |
| SC-004 | Minimize exposed network surfaces with local-first design, HTTPS/RTSPS, firewall policy, and optional operator VPN. | THREAT-004 | SYS-001, SYS-004, SWR-020 | HWA-003, SWA-010 | `nftables-server.conf`, `nftables-camera.conf`, `tailscale_service.py` | TC-006, TC-010, TC-016 | Medium | Draft |
| SC-005 | Protect persistent secrets and recordings through `/data` design, restricted file permissions, and key-management procedures. | THREAT-005 | SYS-003, SYS-008, HWR-004 | ARCH-003, HWA-004 | `cert_service.py`, `ota_service.py`, `pairing.py` | TC-015, TC-018 | Medium | Draft |
| SC-006 | Prohibit software backdoor recovery; use hardware-mediated reset/reflash for lost admin access. | THREAT-001, THREAT-005, THREAT-008 | SYS-008, SWR-018, HWR-006 | HWA-005 | `factory_reset.py`, `factory_reset_service.py` | TC-011, TC-015 | Medium | Draft |
| SC-007 | Maintain SBOM and vulnerability management process for runtime, build, and workflow dependencies. | THREAT-007 | SYS-012, SWR-019 | SWA-009 | `sbom/`, dependency manifests | TC-020 | Medium | Draft |
| SC-008 | Record security and administrative audit events without exposing pre-auth internals. | THREAT-001, THREAT-006 | SYS-010, SWR-009 | SWA-002, ARCH-003 | `audit.py`, `auth.py`, `user_service.py` | TC-011, TC-017 | Medium | Draft |
| SC-009 | Enforce traceability checks in CI for requirements, risks, security controls, tests, and code annotations. | THREAT-007 | SYS-012, SWR-019 | ARCH-006, SWA-009 | `tools/traceability/check_traceability.py` | TC-020 | Medium | Draft |

## Open Questions

- OPEN QUESTION: Define required response time for critical vulnerability fixes.
- OPEN QUESTION: Decide whether security controls need independent manual test
  protocols in addition to automated tests.

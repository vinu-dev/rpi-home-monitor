# System Requirements

Status: Draft prepared to support expert regulatory review.

| ID | Statement | Rationale | Source | Parent links | Risk/security links | Verification method | Linked tests | Status |
|---|---|---|---|---|---|---|---|---|
| SYS-001 | The system shall support local live video viewing from paired cameras. | Satisfies immediate home awareness. | `docs/requirements.md` | UN-001 | RISK-001, SC-002 | System and browser tests | TC-001, TC-006 | Draft |
| SYS-002 | The system shall record, index, and play back camera clips. | Enables review after events. | `docs/requirements.md` | UN-002 | RISK-001, RISK-003 | Integration tests | TC-002, TC-007 | Draft |
| SYS-003 | The system shall manage storage usage and delete oldest recordings when configured thresholds are exceeded. | Prevents uncontrolled disk exhaustion. | `docs/requirements.md` | UN-003 | RISK-003 | Unit tests | TC-003 | Draft |
| SYS-004 | The system shall require authenticated and authorized access to dashboard, API, and camera status functions. | Protects sensitive video and controls. | ADR-0011, ADR-0022 | UN-004 | RISK-002, SC-001, SC-006 | Security tests | TC-004, TC-011 | Draft |
| SYS-005 | The system shall authenticate camera-to-server machine communication after pairing. | Prevents camera impersonation and command spoofing. | ADR-0009, ADR-0016 | UN-004, UN-006 | RISK-002, SC-002 | Security and contract tests | TC-008, TC-012 | Draft |
| SYS-006 | The system shall discover, pair, name, configure, and remove cameras. | Reduces setup burden and supports multi-camera operation. | `docs/requirements.md` | UN-006 | RISK-007, SC-002 | Integration tests | TC-008, TC-012 | Draft |
| SYS-007 | The system shall detect and surface camera, stream, storage, and system faults. | Avoids silent loss of monitoring. | ADR-0023, ADR-0024 | UN-005, UN-009 | RISK-005, RISK-008 | Unit and integration tests | TC-005, TC-014 | Draft |
| SYS-008 | The system shall support operator-controlled configuration and hardware-mediated reset without software backdoors. | Recovery must not weaken security. | ADR-0022 | UN-006, UN-008 | RISK-006, SC-006 | Security and factory reset tests | TC-011, TC-015 | Draft |
| SYS-009 | The system shall verify and install updates using the documented OTA path with rollback design. | Maintains update integrity and recoverability. | ADR-0008, ADR-0014 | UN-007 | RISK-004, SC-003 | OTA unit/integration tests | TC-009, TC-013 | Draft |
| SYS-010 | The system shall log security and administrative events for operator review. | Supports accountability and incident review. | ADR-0011 | UN-004, UN-009 | RISK-002, SC-008 | Audit tests | TC-011, TC-017 | Draft |
| SYS-011 | The system shall operate on the specified Raspberry Pi server, camera, network, storage, and power hardware assumptions. | Hardware limits shape safety and performance. | `docs/hardware-setup.md` | UN-001, UN-006, UN-007 | RISK-001, RISK-008 | Hardware smoke, unit guardrails | TC-018 | Draft |
| SYS-012 | The repository shall maintain traceability among requirements, architecture, risk, security, code, and tests. | Traceability supports controlled change review. | User request 2026-05-01 | UN-010 | RISK-009, SC-009 | Automated traceability check | TC-020 | Draft |

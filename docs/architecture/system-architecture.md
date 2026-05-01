# System Architecture

Status: Draft prepared to support expert regulatory review.

## Overview

The system is a local-first distributed home monitoring product with one home
server, one or more camera nodes, optional browser clients, and optional
operator-managed VPN remote access. Existing narrative architecture remains in
`docs/architecture.md`; this file assigns traceability IDs.

## System Architecture Items

| ID | Item | Description | Requirements | Risks | Security controls | Tests | Source/modules |
|---|---|---|---|---|---|---|---|
| ARCH-001 | Local monitoring topology | Cameras push video and events to the home server on the local network; browsers access the server dashboard over HTTPS. | SYS-001, SYS-002, SYS-004 | RISK-001, RISK-002 | SC-001, SC-002, SC-004 | TC-001, TC-006 | `docs/architecture.md` |
| ARCH-002 | Server/camera split | Server owns UI, recording, storage, users, audit, OTA orchestration; cameras own capture, streaming, local status, pairing client, and motion detector. | SYS-001, SYS-006, SYS-011 | RISK-001, RISK-005 | SC-002, SC-004 | TC-005, TC-008 | `app/server/`, `app/camera/` |
| ARCH-003 | Data and recording boundary | Recordings, config, certificates, logs, and OTA state live under `/data` or configured external storage. | SYS-002, SYS-003, SYS-010 | RISK-003, RISK-006 | SC-005, SC-008 | TC-003, TC-017 | `docs/architecture.md` |
| ARCH-004 | Update boundary | Server and camera updates flow through SWUpdate bundles, staging, verification, and A/B rollback design. | SYS-009 | RISK-004 | SC-003 | TC-009, TC-013 | ADR-0008, ADR-0014 |
| ARCH-005 | Fault and alert boundary | Camera heartbeat, server health, storage monitoring, discovery state, and alert center feed operator-visible status. | SYS-007, SYS-010 | RISK-005, RISK-008 | SC-008 | TC-005, TC-014 | ADR-0023, ADR-0024 |
| ARCH-006 | Traceability boundary | Requirements, risks, controls, code annotations, and test cases are linked in matrix records checked by CI. | SYS-012 | RISK-009 | SC-009 | TC-020 | `tools/traceability/check_traceability.py` |

## Data Flows

| Flow | Description | Trust boundary | Related IDs |
|---|---|---|---|
| Camera stream | Camera H.264/RTSPS stream to server relay and recorder. | Paired camera to server. | ARCH-001, SWR-006, SC-002 |
| Heartbeat/config | Camera sends signed heartbeat; server may return pending config. | Camera/server machine API. | ARCH-005, SWR-004, SC-002 |
| Browser UI/API | Browser accesses dashboard, live view, settings, users, OTA, recordings. | Operator session to server. | ARCH-001, SWR-001, SC-001 |
| OTA upload/install | Operator uploads server bundle or server pushes camera bundle. | Admin to update service; server to camera. | ARCH-004, SWR-010, SC-003 |
| Audit/logging | Security and admin events written to local audit log. | Application to persistent record. | ARCH-003, SWR-009, SC-008 |

## Assumptions and Open Questions

- ASSUMPTION: Local LAN is not fully trusted; authentication and transport
  controls still apply.
- OPEN QUESTION: Define formal performance limits for maximum camera count,
  recording bitrate, and storage size by hardware profile.
- REGULATORY REVIEW REQUIRED: Confirm that the architecture is adequate if the
  intended use changes toward regulated medical monitoring.

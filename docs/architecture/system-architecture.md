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
| ARCH-007 | Provisioning and setup boundary | Initial setup, setup-complete state, admin creation, device identity, and production-default controls are isolated from normal operation. | SYS-013, SYS-021, SYS-024 | RISK-010, RISK-018 | SC-010, SC-019 | TC-021, TC-044 | `provisioning_service.py`, `views.py` |
| ARCH-008 | Account administration boundary | User lifecycle, password policy, roles, session state, and audit evidence form a privileged operator-management boundary. | SYS-014, SYS-004, SYS-010 | RISK-011, RISK-002 | SC-001, SC-011, SC-008 | TC-022, TC-011 | `user_service.py`, `auth.py` |
| ARCH-009 | Storage and media operation boundary | Recording storage, USB media, live media serving, deletion, and retention stay within canonical recording roots and selected devices. | SYS-016, SYS-026, SYS-003 | RISK-003, RISK-013, RISK-014 | SC-013, SC-014, SC-005 | TC-024, TC-025, TC-026, TC-027 | `usb.py`, `recordings_service.py`, `live.py` |
| ARCH-010 | Live transport and proxy boundary | Browser live access uses authenticated server endpoints, restricted WebRTC/WHEP proxying, and controlled HLS/snapshot fallback. | SYS-019, SYS-029 | RISK-017, RISK-014 | SC-016, SC-014, SC-004 | TC-001, TC-027, TC-028 | `api/webrtc.py`, `api/live.py` |
| ARCH-011 | Desired/observed camera-state boundary | Server desired configuration, camera-observed state, pending changes, stale observations, and failed application states are modeled separately. | SYS-017, SYS-020 | RISK-015, RISK-007 | SC-002 | TC-030, TC-012 | ADR-0026, `camera_service.py`, `heartbeat.py` |
| ARCH-012 | Motion notification boundary | Motion events, clip correlation, alert records, and optional rich media attachments are bounded by local retention and privacy controls. | SYS-018, SYS-020 | RISK-016, RISK-005 | SC-015, SC-020 | TC-031, TC-038, TC-014 | ADR-0027, `motion_event_store.py`, `alert_center_service.py` |
| ARCH-013 | Build and release boundary | Version sources, release workflows, OTA signing, SBOM generation, dependency review, and artifact validation are treated as supply-chain controls. | SYS-023, SYS-028 | RISK-019, RISK-004 | SC-018, SC-017 | TC-043, TC-045 | `.github/workflows/`, `scripts/`, `docs/ota-key-management.md` |
| ARCH-014 | Production/development separation boundary | Development credentials, debug conveniences, profiles, systemd hardening, firewall policy, and production image behavior are separated and reviewed. | SYS-024, SYS-030 | RISK-018, RISK-019 | SC-019, SC-018 | TC-044, TC-047 | ADR-0007, ADR-0022, Yocto/systemd configs |
| ARCH-015 | Operator evidence boundary | Logs, faults, audit records, timestamps, release versions, and system summary records form operator/maintainer evidence surfaces. | SYS-020, SYS-022, SYS-023 | RISK-020, RISK-012 | SC-020, SC-008, SC-018 | TC-017, TC-029, TC-041, TC-046 | `audit.py`, `faults.py`, `system_summary_service.py` |

## Data Flows

| Flow | Description | Trust boundary | Related IDs |
|---|---|---|---|
| Camera stream | Camera H.264/RTSPS stream to server relay and recorder. | Paired camera to server. | ARCH-001, SWR-006, SC-002 |
| Heartbeat/config | Camera sends signed heartbeat; server may return pending config. | Camera/server machine API. | ARCH-005, SWR-004, SC-002 |
| Browser UI/API | Browser accesses dashboard, live view, settings, users, OTA, recordings. | Operator session to server. | ARCH-001, SWR-001, SC-001 |
| OTA upload/install | Operator uploads server bundle or server pushes camera bundle. | Admin to update service; server to camera. | ARCH-004, SWR-010, SC-003 |
| Audit/logging | Security and admin events written to local audit log. | Application to persistent record. | ARCH-003, SWR-009, SC-008 |
| First-run setup | Operator completes setup and initial administrator creation before normal dashboard use. | Pre-auth setup to authenticated runtime. | ARCH-007, SWR-021, SC-010 |
| User administration | Admin creates, changes, or removes users and passwords. | Authenticated admin to account store. | ARCH-008, SWR-023, SC-011 |
| USB and media operations | Operator selects, formats, ejects, deletes, or serves recording media. | Admin/session to filesystem and removable media. | ARCH-009, SWR-027, SWR-029, SWR-030 |
| WebRTC proxy | Browser live client requests WHEP actions through the server proxy. | Authenticated browser to controlled local upstream. | ARCH-010, SWR-031, SC-016 |
| Motion notifications | Camera motion events are correlated with clips and local alerts. | Camera event to server alert/media record. | ARCH-012, SWR-040, SWR-041, SC-015 |
| Build/release evidence | Maintainer workflows create versioned artifacts, SBOM evidence, and signed update inputs. | Developer/build host to release artifact boundary. | ARCH-013, SWR-046, SWR-047, SC-018 |

## Assumptions and Open Questions

- ASSUMPTION: Local LAN is not fully trusted; authentication and transport
  controls still apply.
- OPEN QUESTION: Define formal performance limits for maximum camera count,
  recording bitrate, and storage size by hardware profile.
- OPEN QUESTION: Define signed-off production versus development image
  profiles and identify who approves promotion between them.
- OPEN QUESTION: Define whether rich motion notifications are required now or
  remain planned design controls until implementation is complete.
- REGULATORY REVIEW REQUIRED: Confirm that the architecture is adequate if the
  intended use changes toward regulated medical monitoring.

# Software Architecture

Status: Draft prepared to support expert regulatory review.

## Software Architecture Items

| ID | Item | Description | Requirements | Risks | Security controls | Tests | Code references |
|---|---|---|---|---|---|---|---|
| SWA-001 | Server Flask service layer | Thin API/view routes delegate business logic to services. | SWR-001, SWR-005, SWR-007 | RISK-001, RISK-003 | SC-001, SC-008 | TC-002, TC-003 | `app/server/monitor/api/`, `app/server/monitor/services/` |
| SWA-002 | Server authentication and authorization | Session auth, roles, CSRF, rate limit, password hashing, lockout, audit. | SWR-001, SWR-002 | RISK-002 | SC-001, SC-006, SC-008 | TC-004, TC-011 | `app/server/monitor/auth.py`, `user_service.py` |
| SWA-003 | Camera pairing and trust | PIN exchange, certificates, pairing secret, mTLS/control trust. | SWR-003, SWR-004 | RISK-002, RISK-005 | SC-002 | TC-008, TC-012 | `pairing_service.py`, `camera_streamer/pairing.py` |
| SWA-004 | Recording and playback services | Scheduler, streaming, recorder, clip listing, event correlation. | SWR-005, SWR-006, SWR-008 | RISK-001, RISK-005 | SC-002 | TC-001, TC-002, TC-019 | `recording_scheduler.py`, `streaming_service.py`, `recorder_service.py` |
| SWA-005 | Storage management | Background cleanup, USB/internal storage stats, FIFO clip deletion. | SWR-007 | RISK-003 | SC-005 | TC-003 | `storage_manager.py`, `storage_service.py` |
| SWA-006 | OTA services | Bundle staging, verification, update status, install command boundary. | SWR-010, SWR-016 | RISK-004 | SC-003, SC-005 | TC-009, TC-013 | `ota_service.py`, `ota_agent.py`, `ota_installer.py` |
| SWA-007 | Camera runtime | Capture, lifecycle, status server, WiFi setup, health, motion detector. | SWR-012, SWR-013, SWR-014 | RISK-001, RISK-005, RISK-008 | SC-001, SC-002 | TC-005, TC-015, TC-019 | `app/camera/camera_streamer/` |
| SWA-008 | Discovery and alerting | mDNS discovery, offline state, alert center, local fault surfaces. | SWR-015, SWR-017 | RISK-005, RISK-008 | SC-004, SC-008 | TC-008, TC-014 | `discovery.py`, `alert_center_service.py` |
| SWA-009 | Traceability automation | Markdown/matrix/code annotation parser with CI workflow. | SWR-019 | RISK-009 | SC-009 | TC-020 | `tools/traceability/check_traceability.py` |
| SWA-010 | Local-first remote access posture | Optional operator-managed remote access without mandatory cloud coupling. | SWR-020 | RISK-002 | SC-004 | TC-010, TC-016 | `tailscale_service.py`, docs |

## Failure Handling

- Authentication failures return limited pre-auth information and are audited
  where applicable.
- Camera heartbeat failures degrade to offline/fault alerts rather than
  assuming continued monitoring.
- Storage errors are handled fail-silent where possible and surfaced through
  storage stats/alerts.
- OTA busy and verification states reject conflicting operations.
- Camera capture and motion processing validate input and surface hardware
  faults.

## Open Questions

- OPEN QUESTION: Should traceability IDs be embedded in OpenAPI operation
  descriptions to extend traceability through API contracts?
- OPEN QUESTION: Should code annotation density be increased after human review
  of the initial ID map?

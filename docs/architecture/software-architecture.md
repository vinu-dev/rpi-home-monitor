# Software Architecture

Status: Draft prepared to support expert regulatory review.

## Software Architecture Items

| ID | Item | Description | Requirements | Risks | Security controls | Tests | Code references |
|---|---|---|---|---|---|---|---|
| SWA-001 | Server Flask service layer | Thin API/view routes delegate business logic to services. | SWR-001, SWR-005, SWR-007 | RISK-001, RISK-003 | SC-001, SC-008 | TC-002, TC-003 | `app/server/monitor/api/`, `app/server/monitor/services/` |
| SWA-002 | Server authentication and authorization | Session auth, roles, CSRF, rate limit, password hashing, lockout, TOTP challenge, recovery-code fallback, and audit. | SWR-001, SWR-002 | RISK-002 | SC-001, SC-006, SC-008 | TC-004, TC-011 | `app/server/monitor/auth.py`, `totp_service.py`, `api/auth_totp.py`, `user_service.py` |
| SWA-003 | Camera pairing and trust | PIN exchange, certificates, pairing secret, mTLS/control trust. | SWR-003, SWR-004 | RISK-002, RISK-005 | SC-002 | TC-008, TC-012 | `pairing_service.py`, `camera_streamer/pairing.py` |
| SWA-004 | Recording and playback services | Scheduler, streaming, recorder, clip listing, event correlation. | SWR-005, SWR-006, SWR-008 | RISK-001, RISK-005 | SC-002 | TC-001, TC-002, TC-019 | `recording_scheduler.py`, `streaming_service.py`, `recorder_service.py` |
| SWA-005 | Storage management | Background cleanup, USB/internal storage stats, FIFO clip deletion. | SWR-007 | RISK-003 | SC-005 | TC-003 | `storage_manager.py`, `storage_service.py` |
| SWA-006 | OTA services | Bundle staging, verification, update status, install command boundary. | SWR-010, SWR-016 | RISK-004 | SC-003, SC-005 | TC-009, TC-013 | `ota_service.py`, `ota_agent.py`, `ota_installer.py` |
| SWA-007 | Camera runtime | Capture, lifecycle, status server, WiFi setup, health, motion detector. | SWR-012, SWR-013, SWR-014 | RISK-001, RISK-005, RISK-008 | SC-001, SC-002 | TC-005, TC-015, TC-019 | `app/camera/camera_streamer/` |
| SWA-008 | Discovery and alerting | mDNS discovery, offline state, alert center, local fault surfaces. | SWR-015, SWR-017 | RISK-005, RISK-008 | SC-004, SC-008 | TC-008, TC-014 | `discovery.py`, `alert_center_service.py` |
| SWA-009 | Traceability automation | Markdown/matrix/code annotation parser with CI workflow. | SWR-019 | RISK-009 | SC-009 | TC-020 | `tools/traceability/check_traceability.py` |
| SWA-010 | Local-first remote access posture | Optional operator-managed remote access without mandatory cloud coupling. | SWR-020 | RISK-002 | SC-004 | TC-010, TC-016 | `tailscale_service.py`, docs |
| SWA-011 | Setup and view routing | Setup/login/dashboard/view routes enforce setup and authentication state before sensitive UI access. | SWR-021, SWR-022 | RISK-010, RISK-002 | SC-010, SC-001 | TC-021, TC-004 | `views.py`, templates |
| SWA-012 | API contract layer | Server and camera API contract tests guard routes, schemas, and machine/client expectations. | SWR-045 | RISK-021 | SC-021 | TC-042 | `app/server/tests/contracts/`, `app/camera/tests/contracts/` |
| SWA-013 | User administration | User API/service handles account lifecycle, password policy, roles, TOTP enrollment/reset status, last-admin protections, and audit. | SWR-023 | RISK-011, RISK-002 | SC-011, SC-008 | TC-022, TC-011 | `api/users.py`, `api/auth_totp.py`, `user_service.py`, `auth.py` |
| SWA-014 | Settings, time, and WiFi | Server and camera settings paths handle time, timezone, WiFi, hostname, remote 2FA policy, redaction, and validation. | SWR-024, SWR-035, SWR-036 | RISK-012, RISK-010 | SC-012, SC-020 | TC-023, TC-033, TC-034 | `api/settings.py`, `settings_service.py`, `wifi_setup.py` |
| SWA-015 | Removable storage operations | USB detection, mount, format, eject, selected target state, and safe fallback are centralized. | SWR-027, SWR-028 | RISK-013, RISK-003 | SC-013, SC-005 | TC-024, TC-025 | `usb.py`, `storage_service.py` |
| SWA-016 | Media path safety | Recording and live-media APIs canonicalize path inputs and keep file operations within media roots. | SWR-029, SWR-030 | RISK-014 | SC-014 | TC-026, TC-027 | `api/recordings.py`, `recordings_service.py`, `api/live.py` |
| SWA-017 | WebRTC/WHEP proxy | Authenticated proxy layer restricts methods and upstream paths for browser live transport. | SWR-031, SWR-052 | RISK-017 | SC-016 | TC-028 | `api/webrtc.py`, `api/on_demand.py` |
| SWA-018 | System summary model | Aggregates health, alert, storage, camera, stale-state, and action-required evidence. | SWR-032 | RISK-005, RISK-015 | SC-008, SC-020 | TC-029 | `system_summary_service.py` |
| SWA-019 | Desired/observed reconciliation | Camera service and heartbeat payloads separate requested config from observed/applied state. | SWR-025, SWR-026 | RISK-015, RISK-007 | SC-002 | TC-030, TC-012 | ADR-0026, `camera_service.py`, `heartbeat.py` |
| SWA-020 | Motion event enrichment | Motion event storage, clip correlation, alert records, and rich notification design link events to evidence. | SWR-040, SWR-041, SWR-033 | RISK-016, RISK-005 | SC-015, SC-020 | TC-031, TC-038 | `motion_event_store.py`, `motion_clip_correlator.py`, `alert_center_service.py` |
| SWA-021 | Certificate and secret lifecycle | Certificate service, pairing storage, encryption helpers, OTA keys, and file permissions protect trust anchors. | SWR-034, SWR-043 | RISK-002, RISK-019 | SC-017, SC-005 | TC-032, TC-040 | `cert_service.py`, `encryption.py`, `pairing.py` |
| SWA-022 | Camera setup/platform/health | Camera runtime validates config, WiFi setup, platform probes, health, and local status surfaces. | SWR-035, SWR-036, SWR-037 | RISK-010, RISK-012, RISK-022 | SC-010, SC-012 | TC-033, TC-034, TC-035 | `config.py`, `wifi.py`, `status_server.py`, `platform.py`, `health.py` |
| SWA-023 | Fault framework | Server and camera fault records normalize source, category, severity, timestamps, and remediation metadata. | SWR-051, SWR-037 | RISK-005, RISK-020 | SC-020, SC-008 | TC-046, TC-035 | `faults.py`, `alert_center_service.py` |
| SWA-024 | Build, release, and CI automation | Workflows and scripts execute tests, release validation, OTA signing checks, version checks, SBOM, and traceability. | SWR-046, SWR-047, SWR-048, SWR-055 | RISK-009, RISK-019 | SC-009, SC-018 | TC-043, TC-045 | `.github/workflows/`, `scripts/` |
| SWA-025 | Production/development profile separation | Runtime images and configs separate development defaults, production credentials, debug access, and hardening rules. | SWR-049, SWR-050, SWR-054 | RISK-018, RISK-010 | SC-019, SC-010 | TC-044, TC-047 | ADR-0007, Yocto/systemd/firewall configs |
| SWA-026 | Share-link service and public blueprint | One service owns token lifecycle, scope validation, first-use pinning, and audit while separate admin/public blueprints keep recipient routes decoupled from session-authenticated views. | SWR-058, SWR-059, SWR-060, SWR-061 | RISK-023, RISK-024, RISK-025 | SC-022, SC-023, SC-024 | TC-050, TC-051, TC-052, TC-053 | `share_link_service.py`, `api/share.py`, `views.py`, `templates/shared_*` |
| SWA-027 | Encoder preset catalogue and camera-settings flow | A pure server-side preset catalogue feeds an authenticated API and dashboard helper logic, while camera updates reuse the existing validated control channel and desired-versus-observed reconciliation paths. | SWR-065, SWR-066, SWR-067 | RISK-007, RISK-015, RISK-021 | SC-002, SC-020, SC-021 | TC-012, TC-042, TC-054 | `encoder_presets.py`, `camera_service.py`, `api/cameras.py`, `dashboard.html` |
| SWA-028 | Diagnostics bundle export | A thin system route delegates diagnostics collection to a service-layer assembler plus a pure redaction helper, stages the archive under `/data/config`, streams it through a cleanup-on-close file wrapper, and records export audit events. | SWR-068, SWR-069, SWR-070 | RISK-026, RISK-020 | SC-020, SC-025 | TC-055 | `diagnostics_bundle.py`, `redact.py`, `api/system.py`, `settings.html` |

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
- USB and recording deletion paths reject ambiguous or out-of-root targets.
- Desired camera state that has not been observed as applied is surfaced as
  pending, stale, or failed rather than treated as successful.
- Production/development configuration conflicts are treated as release
  blockers until reviewed.

## Open Questions

- OPEN QUESTION: Should traceability IDs be embedded in OpenAPI operation
  descriptions to extend traceability through API contracts?
- OPEN QUESTION: Should code annotation density be increased after human review
  of the initial ID map?
- OPEN QUESTION: Should every public API route include explicit requirement
  metadata in contract fixtures?
- OPEN QUESTION: Which planned rich-motion notification requirements are
  implementation-ready versus design intent only?

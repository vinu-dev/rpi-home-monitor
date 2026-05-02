# Feature Spec: Camera Offline Alerts

## Title

Camera offline alerts.

## Problem

A silent camera failure is worse than a noisy alert because the user believes
they are protected when they are not. The system already knows about heartbeats
and connectivity state, but it does not yet turn that into a clear user-facing
warning.

## User Value

This is a high-trust feature. It tells the user when coverage degraded and makes
the product feel honest rather than silently brittle.

## Scope

- detect offline or stale-heartbeat camera state suitable for alerting
- notify users when a camera transitions from healthy to offline
- notify when the camera returns, if the product chooses to expose recovery
- support per-camera enable/disable for offline alerts

## Non-Goals

- no root-cause diagnostics in the first slice
- no automatic repair/orchestration flow
- no changes to pairing or control-channel trust model

## Acceptance Criteria

- a camera that stops reporting beyond the configured threshold triggers one
  offline alert
- repeated checks do not spam duplicate alerts while the camera remains down
- the UI shows the relevant camera as offline in a way that matches the alert
  state
- recovery to online can be observed and, if enabled, reported once

## User Experience

Entry point:

- camera status surfaces and notification settings

Main flow:

- camera transitions from healthy to stale/offline
- server marks it offline
- eligible users receive an offline alert
- the app status surface reflects the same state

Success state:

- the user quickly learns which camera is down and when it happened

Failure state:

- transient heartbeat jitter should not cause flapping alerts

Edge cases:

- server restart while a camera is already offline
- Wi-Fi blips causing very short disconnects
- camera intentionally powered off for maintenance

## Architecture Fit

- server modules/services: existing heartbeat/offline detection should remain the
  source of truth
- camera modules/services: no new camera auth or command surface required
- persistence/data model: offline alert state or suppression window may need
  persistence
- frontend/templates/static code: camera health status and settings UI

## Technical Approach

- reuse the current server-side heartbeat evaluation model
- add alert-transition logic on state change rather than polling-based spam
- make alert eligibility a function of per-user preferences and per-camera rules
- ensure restart behavior does not resend stale alerts without a fresh state
  transition

## Affected Areas

- camera health/heartbeat services
- notification delivery service
- camera status UI
- settings/preferences persistence

## Security / Privacy Considerations

- do not expose internal network details in alert content
- keep the feature observational only; do not expand control powers

## Testing Requirements

- unit tests for state transitions and alert suppression
- integration tests for offline then recovery sequences
- manual verification with a real or simulated camera heartbeat loss

## Documentation Updates

- user docs for camera health alerts
- any status/health docs that need new alert semantics

## Rollout Notes

- default threshold should favor trust over aggressiveness; avoid flapping
- if recovery notifications are included, make them separately configurable later

## Open Questions

- should "camera back online" ship in the first slice or wait for later?
- should maintenance-mode suppression exist now or in a later ops pass?

## Implementation Guardrails

- preserve the modular monolith architecture
- preserve the server/camera responsibility split
- do not add new long-lived daemons unless clearly justified
- keep the product local-first by default
- do not weaken auth, OTA, or device trust boundaries
- update tests and docs together with code

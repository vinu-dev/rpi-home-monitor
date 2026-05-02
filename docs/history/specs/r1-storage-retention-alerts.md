# Feature Spec: Storage Low / Retention Risk Alerts

## Title

Storage low-space and retention-risk alerts.

## Problem

Users do not just need recordings; they need confidence that recordings are
still being retained. If storage is nearly full or retention is collapsing,
silent failure erodes trust quickly.

## User Value

This feature protects the "did it record when I needed it?" promise and gives
users a chance to act before important footage disappears.

## Scope

- alert when available storage crosses a low-space threshold
- alert when retention is at risk based on recorder state or retention policy
- expose enough UI context for the user to understand the problem
- support suppression so the same condition does not alert continuously

## Non-Goals

- no full storage analytics dashboard in this slice
- no automatic remote archival
- no changes to the recorder storage architecture

## Acceptance Criteria

- low-storage conditions produce one alert per threshold-crossing event
- retention-risk state can be surfaced distinctly from generic low disk space
- the UI shows a current warning state that matches what the alert described
- alerts are suppressed until the condition meaningfully changes

## User Experience

Entry point:

- system health/status area and notification settings

Main flow:

- storage health degrades past a configured threshold
- server classifies the state as low-space or retention-risk
- eligible users receive an alert
- user opens the app and sees a matching warning with a recommended next action

Success state:

- the user understands whether the issue is capacity pressure, retention
  pressure, or both

Failure state:

- the system must not spam repeated alerts while disk usage remains stuck near a
  threshold

Edge cases:

- threshold flapping near the limit
- temporary cleanup briefly clears the alert
- recorder disabled or intentionally paused

## Architecture Fit

- server modules/services: recorder/storage metrics remain the source of truth
- persistence/data model: may need alert suppression or last-alert state
- frontend/templates/static code: health UI and settings
- Yocto/build/deployment impact: none beyond normal app packaging

## Technical Approach

- define a small set of storage-health states instead of a raw percentage-only
  model
- classify alerts server-side from recorder metrics and retention configuration
- use the shared notification delivery abstraction from the broader Release 01
  alerting work
- present actionable but simple UI copy, not a full capacity console

## Affected Areas

- recorder/storage monitoring services
- notification delivery service
- system health UI
- settings/preferences persistence

## Security / Privacy Considerations

- do not expose unnecessary filesystem or host-path detail in alerts
- keep admin-only operational details protected where appropriate

## Testing Requirements

- unit tests for threshold and suppression logic
- integration tests for low-space and recovery transitions
- manual verification using controlled storage-pressure scenarios

## Documentation Updates

- user docs for storage health alerts
- operational docs if thresholds or retention semantics become configurable

## Rollout Notes

- start with conservative threshold count and simple warning states
- avoid adding too many tunables in the first slice

## Open Questions

- should retention-risk be derived only from free space, or also from observed
  recorder pruning behavior?
- should these alerts be admin-only in the first slice?

## Implementation Guardrails

- preserve the modular monolith architecture
- preserve the server/camera responsibility split
- do not add new long-lived daemons unless clearly justified
- keep the product local-first by default
- do not weaken auth, OTA, or device trust boundaries
- update tests and docs together with code

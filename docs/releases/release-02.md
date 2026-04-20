# Release 02 Plan

Date: 2026-04-20
Status: Planned
Theme: Make detections smarter and the system more connected
Roadmap source: [roadmap-next-2-releases.md](../roadmap-next-2-releases.md)

## Goal

Build on the Release 01 event and notification foundation to add semantic
detection value, local integrations, and stronger account/ops capabilities.

## Target Outcomes

- events become meaningfully classifiable, not just "motion happened"
- advanced users can connect the system to local automation tools
- household and operator trust improves through stronger account controls

## Planned Scope

1. person detection
2. vehicle detection
3. package detection
4. Home Assistant integration
5. MQTT event bus
6. generic webhooks
7. bulk camera settings / profile templates
8. TOTP / stronger 2FA
9. protected clips
10. diagnostic export bundle

## Dependency Assumptions

- semantic detection work depends on Release 01 event and review foundations
- Home Assistant, MQTT, and webhooks should share a common event publication
  model rather than shipping three unrelated emitters
- TOTP should respect the current "no software backdoor" recovery posture
- protected clips should layer on the existing recording/event store instead of
  inventing a second retention model

## Major Risks

- semantic detection can create false-confidence UX if precision is poor
- integrations can fragment architecture if each is implemented ad hoc
- stronger auth flows can accidentally conflict with the agreed recovery policy

## Planning Rule For This Release

Do not implement Release 02 features until the Release 01 event importance,
notification preferences, and review semantics are stable enough to reuse.

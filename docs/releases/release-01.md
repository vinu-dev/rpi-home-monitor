# Release 01 Plan

Date: 2026-04-20
Status: Planned
Theme: Make the system proactive and easier to live with
Roadmap source: [roadmap-next-2-releases.md](../roadmap-next-2-releases.md)

## Goal

Ship the first buyer-visible set of alerting and review improvements without
changing the core server/camera trust model, introducing a major platform
redesign, or requiring public internet services.

## Release Outcomes

- users receive timely, useful alerts through local and Tailscale-remote flows
- operators can see when cameras or storage health are degrading
- review gets faster after an alert arrives
- the event pipeline becomes a stable base for later semantic detection work

## In Scope

1. [Rich motion notifications](../specs/r1-rich-motion-notifications.md)
2. [Local alert center and Tailscale-remote review flow](../specs/r1-local-alert-center-and-tailscale-remote-review.md)
3. [Camera offline alerts](../specs/r1-camera-offline-alerts.md)
4. [Storage low / retention risk alerts](../specs/r1-storage-retention-alerts.md)
5. [Review queue for important events](../specs/r1-review-queue.md)

## Explicitly Not In Scope

- person / vehicle / package detection
- internet-dependent push delivery
- Home Assistant / MQTT / webhook integrations
- physical factory-reset UX work
- broad auth redesign beyond already-agreed recovery posture

## Dependency Order

1. Rich motion notifications
2. Local alert center and Tailscale-remote review flow
3. Camera offline alerts
4. Storage low / retention risk alerts
5. Review queue for important events

Notes:

- `Local alert center` depends on the event importance and deep-link model
  defined by `Rich motion notifications`.
- `Review queue` should reuse event priority and routing semantics introduced by
  the alerting work rather than creating a parallel importance model.
- `Offline alerts` and `Storage alerts` may share delivery plumbing with motion
  notifications, but should not be blocked on detection semantics.
- Remote access assumptions for this release are via Tailscale to the local UI,
  not vendor-cloud push infrastructure.

## Release Guardrails

- Preserve the modular-monolith server shape.
- Do not add a public-internet dependency.
- Keep delivery opt-in and user-configurable.
- Avoid new always-on daemons unless the current service pattern cannot support
  the need.
- Update docs and tests together with behavior changes.
- Do not weaken auth, pairing, OTA, or control-channel trust boundaries.

## Feature Readiness Checklist

A feature is ready to implement when it has:

- a feature spec under `docs/specs/`
- acceptance criteria
- dependency note
- likely modules/files to change
- testing expectations
- rollout notes
- linked implementation issue(s)

## Suggested Issue Breakdown

For each feature, create:

- one parent feature issue
- one backend/API issue
- one frontend/UI issue
- one verification/docs issue

Add an architecture issue only if the spec crosses a trust boundary, changes
event semantics, or needs an ADR.

## Exit Criteria

This release is complete when:

- all in-scope features meet spec acceptance criteria
- docs match shipped behavior
- verification is recorded for browser, server, and at least one real camera
- release notes call out every user-visible alert/review change

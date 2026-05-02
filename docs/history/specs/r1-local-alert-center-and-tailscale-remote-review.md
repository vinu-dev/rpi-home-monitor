# Feature Spec: Local Alert Center And Tailscale-Remote Review

## Title

Local alert center and Tailscale-remote review flow.

## Problem

The product needs a practical way to surface important events quickly, but the
product direction is no-internet-by-default. A notification design that depends
on browser-vendor or cloud push infrastructure would conflict with the privacy
and deployment model.

## User Value

Users get a clear, actionable alert surface without giving up local-first
operation. When they are away from home, they reach the same review experience
through Tailscale rather than through a vendor cloud.

## Scope

- create an in-app alert center for recent important activity
- surface alert badges and summary state in the local web UI
- deep-link alerts into the review queue and event/clip detail views
- define the remote-use story as access to the same UI over Tailscale
- support per-user preferences for what appears in the alert center

## Non-Goals

- no browser-vendor push infrastructure
- no email or SMS delivery
- no public cloud relay
- no mobile native app in this slice

## Acceptance Criteria

- the app exposes a visible alert center or alert inbox for important recent
  activity
- rich motion, camera offline, and storage alerts can appear in the same alert
  surface using shared event semantics
- a user can open an alert and land on the relevant review or detail page
- the remote access story works through Tailscale to the existing local UI,
  without requiring public internet services

## User Experience

Entry point:

- dashboard header, primary nav, or another always-visible alert affordance

Main flow:

- the server classifies an event as alert-worthy
- the local UI shows a badge, banner, inbox item, or alert-center entry
- the user opens the alert center and sees recent important items
- when away from home, the user connects through Tailscale and uses the same UI
  flow

Success state:

- the product feels responsive and informative while staying local-first

Failure state:

- if the user is off-LAN and not connected through Tailscale, there is no fake
  promise of remote alert delivery

Edge cases:

- many alerts in a short period should group cleanly
- stale alerts should not remain prominent forever
- multiple users should each see their own reviewed/unreviewed state if the UI
  supports it

## Architecture Fit

- server modules/services: alert state should be derived from existing event and
  health services
- persistence/data model: store per-user alert preferences and any minimal
  reviewed-state needed
- frontend/templates/static code: alert center surface, badges, summary cards,
  and deep-links
- Yocto/build/deployment impact: none beyond normal app packaging

## Technical Approach

- reuse the same event-importance model as motion, offline, and storage alerts
- keep alert generation server-side and UI rendering browser-side
- treat remote access as transport/access concern handled by Tailscale, not as a
  separate notification backend
- design the alert center so later optional delivery modes can plug into the
  same underlying alert/event model without changing product semantics

## Affected Areas

- event/alert services
- dashboard or top-level navigation UI
- review queue linking
- per-user preferences persistence

## Security / Privacy Considerations

- stay honest about remote access: no alert path should imply vendor visibility
  into home activity
- do not leak private event content outside authenticated local or Tailscale
  sessions
- preserve the local-first trust model and avoid new third-party delivery
  dependencies

## Testing Requirements

- unit tests for alert-center state derivation and grouping
- integration tests for alert creation and deep-link behavior
- UI tests for empty, unread, and mixed-alert states
- manual verification on LAN and over Tailscale remote access

## Documentation Updates

- roadmap/release/spec references if alert behavior changes
- user docs for local alert review and remote access expectations
- docs that explain remote access assumptions through Tailscale

## Rollout Notes

- ship as the primary alerting UX for the no-internet product model
- do not overpromise "push" behavior in copy or docs

## Open Questions

- should the first slice use a badge + inbox only, or also include dashboard
  banners/cards?
- should unread state be per-user only, or also have a household/global mode?

## Implementation Guardrails

- preserve the modular monolith architecture
- preserve the server/camera responsibility split
- do not add new long-lived daemons unless clearly justified
- keep the product local-first by default
- do not weaken auth, OTA, or device trust boundaries
- update tests and docs together with code

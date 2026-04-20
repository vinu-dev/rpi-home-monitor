# Feature Spec: Rich Motion Notifications

## Title

Rich motion notifications with snapshots, filters, and per-camera rules.

## Problem

The system records motion, but it does not yet turn that into a strong,
time-sensitive user experience. Users have to open the app and hunt for activity
instead of being told when something important happened.

## User Value

This is one of the clearest market-value features in the current product gap. A
useful, trustworthy alert is easier to explain and easier to buy than generic
"motion exists somewhere" behavior.

## Scope

- generate user-visible notifications for qualifying motion events
- include camera name, event time, and a representative snapshot
- support per-camera enable/disable controls
- support basic event filtering for notification noise reduction
- support severity or importance tagging that later review features can reuse

## Non-Goals

- no mobile native app delivery
- no person/vehicle/package semantics in this slice
- no cloud notification service requirement
- no cross-camera incident correlation

## Acceptance Criteria

- a user can enable notifications for one camera and disable them for another
- a qualifying motion event generates a notification payload with snapshot,
  timestamp, and camera label
- users can suppress at least one class of low-value events based on the
  filtering model chosen for this slice
- the same event is not delivered repeatedly due to trivial event churn
- every delivered notification links back to the relevant review surface

## User Experience

Entry point:

- Settings contains a notifications section with global enablement and per-camera
  rules

Main flow:

- user opts into notifications
- user chooses which cameras can notify
- a motion event occurs
- server decides whether the event is notification-worthy
- user receives a browser notification with a still image and concise text
- selecting the notification opens the app at the relevant event or clip

Success state:

- the notification arrives quickly, is understandable at a glance, and deep-links
  into review

Failure state:

- if delivery is unavailable or permission is missing, the UI explains the state
  without pretending alerts are active

Edge cases:

- bursty motion should coalesce rather than spam
- missing snapshot should fall back to text-only notification
- camera offline state should not generate false motion notifications

## Architecture Fit

- server modules/services: extend existing event ingestion and server-side event
  handling rather than building a second event pipeline
- camera modules/services: camera continues to produce motion/event artifacts;
  notification policy stays server-side
- persistence/data model: per-user notification preferences and per-camera rule
  state need persistence
- frontend/templates/static code: settings UI and browser notification enrollment
  surfaces
- Yocto/build/deployment impact: include any browser-facing assets or service
  worker support if needed; avoid platform redesign

## Technical Approach

- add a notification policy service on the server side that evaluates motion
  events against stored preferences
- represent notification eligibility as derived state from event metadata plus
  user preferences, not as a separate camera-side workflow
- store per-user preferences and per-camera overrides in the server config state
- add a shared alerting abstraction that Release 01 features can reuse for the
  local alert center, offline alerts, and storage alerts
- deep-link notifications into the existing or planned review surface by event id

## Affected Areas

- server event services and models
- user/settings persistence
- notification delivery service
- settings templates/static assets
- review/event linking routes

## Security / Privacy Considerations

- notification content may expose home activity on a shared browser, so it must
  remain opt-in
- do not place secrets or privileged routing details in the notification payload
- snapshots in notifications should follow existing retention/privacy rules

## Testing Requirements

- unit tests for filtering, deduping, and rule evaluation
- integration tests for event-to-notification generation
- browser tests for permission-denied and opted-out states
- manual verification with a real browser receiving and opening a notification

## Documentation Updates

- roadmap/spec references if behavior changes
- user-facing docs for notification setup
- any event/review docs affected by new deep-link behavior

## Rollout Notes

- ship disabled by default until browser enrollment is complete
- keep filtering simple in the first slice and expand later from real usage

## Open Questions

- what is the minimum useful filtering model for the first release:
  duration-based, activity-zone-based, or priority-tag-based?
- should notification coalescing be time-window-based or event-group-based?

## Implementation Guardrails

- preserve the modular monolith architecture
- preserve the server/camera responsibility split
- do not add new long-lived daemons unless clearly justified
- keep the product local-first by default
- do not weaken auth, OTA, or device trust boundaries
- update tests and docs together with code

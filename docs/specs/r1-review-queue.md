# Feature Spec: Review Queue For Important Events

## Title

Review queue for important events.

## Problem

After an alert arrives, the user still needs a fast way to answer "what should I
look at first?" Today that workflow is too manual and forces the user to browse
recordings without enough prioritization.

## User Value

This turns alerting into a usable review loop. It reduces time-to-understanding
after motion and creates a better day-to-day product experience.

## Scope

- create a queue or feed of important recent events
- rank or group events using the same importance model used for notifications
- provide a review surface optimized for triage, not raw archive browsing
- allow users to move quickly from queue item to detailed event/clip view

## Non-Goals

- no full semantic search
- no face recognition or identity features
- no cross-camera incident stitching in this slice
- no new retention/export model

## Acceptance Criteria

- the app exposes a dedicated review surface for important events
- items in the review queue are ordered or grouped consistently using shared
  importance rules
- selecting an item opens the relevant event or recording context
- reviewed/dismissed state behaves predictably for the current user

## User Experience

Entry point:

- dashboard or primary navigation entry labeled for review/inbox/recent
  important activity

Main flow:

- event importance is assigned as events are processed
- user opens the review queue
- user scans the most important recent items first
- user opens an item, confirms it is relevant or not, and returns to the queue

Success state:

- users can clear meaningful activity quickly without scrubbing the full archive

Failure state:

- if importance signals are weak, the queue should still behave like a useful
  recent-activity list rather than a random sort order

Edge cases:

- many similar events from one burst
- queue item points to a clip that has already expired
- multiple users reviewing on different browsers

## Architecture Fit

- server modules/services: reuse event metadata and alert importance semantics
- persistence/data model: may need reviewed/dismissed state per user
- frontend/templates/static code: new review surface and event detail handoff
- Yocto/build/deployment impact: none beyond app UI assets

## Technical Approach

- define a shared importance model that both notifications and review use
- persist only the minimum additional user-state needed for triage
- keep the queue server-driven so the browser does not invent ranking logic
- deep-link into existing event/clip views rather than duplicating playback code

## Affected Areas

- event services and metadata
- notification/event importance service
- review queue UI
- per-user review state persistence

## Security / Privacy Considerations

- reviewed state is user-specific and should not leak across accounts unless
  explicitly designed that way
- queue access should follow the same auth rules as recordings/events

## Testing Requirements

- unit tests for ranking/grouping and reviewed-state behavior
- integration tests for queue population and item navigation
- UI tests for empty state, busy state, and mixed-priority lists
- manual verification with real event data

## Documentation Updates

- user docs for reviewing important activity
- docs that describe event semantics if the shared importance model becomes part
  of product language

## Rollout Notes

- ship as a focused triage surface, not a full new archive subsystem
- prioritize clarity and speed over extensive filtering in the first slice

## Open Questions

- should the first slice support "mark all reviewed"?
- should review state expire automatically after a retention window?

## Implementation Guardrails

- preserve the modular monolith architecture
- preserve the server/camera responsibility split
- do not add new long-lived daemons unless clearly justified
- keep the product local-first by default
- do not weaken auth, OTA, or device trust boundaries
- update tests and docs together with code

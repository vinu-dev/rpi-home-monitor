# Feature Spec: Rich Motion Notifications

Status: Ready for AI implementation planning
Priority: P0
Roadmap Slot: Release Next
Backlog Source: [market-feature-backlog-100.md](C:/Users/vinun/codex/rpi-home-monitor/docs/market-feature-backlog-100.md)
Related Issue: [#121](https://github.com/vinu-dev/rpi-home-monitor/issues/121)

## Problem

Today the product records motion events and shows them in the dashboard, but it does not proactively notify the user. That means users only discover important events if they remember to open the UI and check the event feed.

Competitors across open-source and commercial surveillance products treat notifications as a core part of the experience, not an optional afterthought.

## User Value

- Makes the system feel alive and trustworthy.
- Reduces the gap versus Ring, Arlo, Tapo, UniFi Protect, Synology, Frigate, and Scrypted.
- Increases the value of the existing motion-event pipeline without requiring a mobile app.
- Strengthens the local-first story by delivering useful alerts without introducing a cloud dependency.

## Scope

This slice should deliver browser-based notifications for motion events with useful controls and deep links.

Included:

- browser notification enrollment
- local alert-center state and per-user alert preferences
- notification delivery for motion events
- camera name and event timestamp in payload
- snapshot thumbnail when available
- per-camera enable/disable
- cooldown / anti-spam behavior
- deep link to `/events/<id>`

## Non-Goals

Not in this slice:

- native mobile app push
- email / Telegram / Slack / Teams channels
- package / person / vehicle-specific notifications
- geofencing
- multi-user escalation rules
- alarm severity model

## Acceptance Criteria

- An authenticated user can opt into browser notifications from the web UI.
- When a motion event is created, a subscribed user receives a notification within a few seconds.
- The notification clearly identifies the camera and time of the event.
- If a relevant snapshot is available, the notification includes it.
- Clicking the notification opens the matching event route in the product.
- User can disable notifications per camera.
- The system suppresses rapid notification spam using a configurable cooldown window.
- If the browser does not support notifications or permission is denied, the UI explains that clearly.

## User Experience

### Entry point

User opens Settings and finds a Notifications section.

### Enrollment flow

1. User clicks `Enable browser notifications`.
2. Browser permission prompt appears.
3. On success, the server stores the push subscription for that user/browser.
4. UI shows enabled state and available camera filters.

### Camera filter flow

1. User sees a list of cameras with toggles.
2. User enables alerts for selected cameras only.
3. User can set a cooldown such as 30s, 60s, or 5m.

### Delivery flow

1. Camera posts a motion event to the server.
2. Server persists the event as it does today.
3. Alert service evaluates user preferences.
4. Matching users see a local alert entry or unread alert state in the UI.
5. Alert shows:
   - title: `Motion detected`
   - body: `<camera-name> · 14:23`
   - icon/badge: product icon
   - image: snapshot thumbnail if available
6. Opening the alert lands on `/events/<id>`.

### Failure states

- If alert preferences fail to save, UI shows a save error and does not pretend alerts are active.
- If the user is remote without Tailscale access, the product does not pretend alerts are reachable off-LAN.
- If thumbnail generation is unavailable, alert entry still appears without image.

## Architecture Fit

This feature fits the current architecture well.

Already present:

- `MotionEventStore` and motion-event ingestion
- event routing via `/events/<id>`
- authenticated web UI
- per-user model and settings persistence patterns
- snapshot generation in the streaming service

Likely architectural pieces:

- Server:
  - `app/server/monitor/services/motion_event_store.py`
  - `app/server/monitor/api/motion_events.py`
  - `app/server/monitor/views.py`
  - `app/server/monitor/models.py`
  - `app/server/monitor/store.py`
- Frontend:
  - `app/server/monitor/templates/settings.html`
  - `app/server/monitor/static/js/app.js`
- New server-side service:
  - alert derivation and dispatch logic

## Technical Approach

### Data model

Add alert preference storage.

Possible shape:

- extend `User` or `Settings` with notification preferences
- add a new JSON file such as `notifications.json`

Preferred shape:

- keep alert preferences and alert-center state separate from `users.json`
- add server-managed alert records keyed by user id and event id where needed

Rationale:

- alert state is product-specific and should not be hidden inside account data
- easier to reuse the same state model for motion, offline, and storage alerts

### API

Add endpoints like:

- `GET /api/v1/alerts`
- `POST /api/v1/alerts/<id>/read`
- `GET /api/v1/notifications/preferences`
- `PUT /api/v1/notifications/preferences`

### Dispatch

On motion-event creation:

1. event is stored normally
2. alert dispatcher receives the created event
3. dispatcher checks:
   - eligible users
   - per-camera filters
   - cooldown state
4. build alert payload
5. persist local alert / unread state

### Snapshot behavior

Preferred order:

1. use existing last snapshot for the camera
2. if none exists, send notification without image

Avoid blocking event creation on snapshot generation.

### Cooldown behavior

Cooldown should apply per user per camera.

Example:

- if camera A fires five times in 20 seconds
- one notification is sent
- subsequent ones inside cooldown are suppressed

Potential enhancement later:

- coalesced summary notification

## Affected Areas

Likely code areas:

- `app/server/monitor/models.py`
- `app/server/monitor/store.py`
- `app/server/monitor/__init__.py`
- `app/server/monitor/api/`
- `app/server/monitor/services/`
- `app/server/monitor/templates/settings.html`
- `app/server/monitor/static/js/app.js`
- tests under `app/server/tests/unit`, `integration`, and possibly Playwright

## Security / Privacy Considerations

- Notification content must not leak to unauthenticated channels.
- Alert preferences must be stored as authenticated user-owned resources.
- Alert state changes must require normal auth and CSRF protection.
- Snapshot thumbnails in alerts should be shown only to authenticated, authorized users.
- No external cloud dependency should be required for the first slice.

## Testing Requirements

### Unit

- preference validation
- cooldown logic
- dispatch filtering by camera
- unread/read state transitions

### Integration

- alert preference APIs
- motion event triggers alert creation
- cooldown suppresses duplicates
- deep-link payload points to the right event

### End-to-end

- UI flow for enabling notifications
- camera filter changes persist

### Manual

- local LAN browser session
- remote browser session over Tailscale

## Documentation Updates

Update if shipped:

- `README.md`
- `docs/requirements.md`
- `docs/architecture.md`
- feature backlog / roadmap docs as needed

## Rollout Notes

- ship as local alert-center behavior first
- keep the scope narrow and robust
- use a feature flag if needed during development
- do not block event ingestion on alert creation success/failure

## Open Questions

- Do we want unread/read state stored per user only, or should we also support household-level visibility later?
- Should the first version include dashboard cards in addition to an alert center/inbox?
- How much alert history should remain visible before it rolls into general event history?

## Implementation Guardrails

- Reuse the existing motion-event model rather than creating a parallel alert pipeline.
- Keep dispatch asynchronous/non-blocking relative to event ingestion.
- Do not introduce a public-internet delivery dependency as a product requirement.
- Preserve local-first behavior and server ownership of policy.

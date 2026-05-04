# ADR-0027: Rich Motion Notifications

**Status**: Proposed — 2026-05-01
**Resolves**: the two open questions in
[`docs/history/specs/r1-rich-motion-notifications.md`](../specs/r1-rich-motion-notifications.md)
**Unblocks**: #121 (umbrella), #127 (Release 01), #128 (Backend/API),
#129 (Frontend/UI), #130 (Verification/Docs)
**Relates to**: ADR-0021 (camera-side motion detection), ADR-0024
(local alert center), ADR-0023 (fault framework)

## Context

The spec asks for "useful, trustworthy" browser notifications for
qualifying motion events with snapshots, per-camera rules, and basic
filtering. It lists two open questions and several explicit
non-goals (no native mobile, no cloud relay, no person/vehicle
semantics, no zones).

The system already has:

- `MotionEventStore` (ADR-0021) — typed motion events with start/end
  + clip correlation.
- `AlertCenterService` (ADR-0024) — derive-on-read inbox over fault
  + motion + audit, with per-user read state. Motion threshold
  already filters alert-worthy events.
- The dashboard's bell badge polls `/api/v1/alerts/unread-count`
  every 30 s — a polling delivery channel that already exists.
- Recordings are stored as MP4 segments under
  `/data/recordings/<camera>/<date>/`, with a clip-correlator that
  maps motion events to clip + offset.

Three things are missing:

1. **No browser-level notification surface.** Operators only see
   the bell badge if they're already on a Home Monitor tab.
2. **No per-user / per-camera notification preferences.** The
   alert center's read state is per-user; notification *opt-in*
   isn't.
3. **No snapshot pipeline.** Motion events have a `clip_ref`
   eventually, but no still-image extraction.

## Decision

**Layer browser notifications on top of the alert center,
server-side polling, no service worker, no cloud relay.**
**Filter by minimum event duration. Coalesce by time window.**

### Resolved open questions

#### Q1 — minimum useful filtering model for v1: **duration-based**

Spec asks: duration / activity-zone / priority-tag — pick one.

Choose **duration-based** because:

- It's the simplest signal that's already on every motion event
  (`duration_seconds` lives on `MotionEvent` per ADR-0021).
- Activity zones require a polygon editor on a snapshot — that's
  a UX surface bigger than this entire feature.
- Priority tags require operator input on every camera + a tagging
  taxonomy — also large.
- Empirically: most false motion (a wind-moved leaf, a passing
  shadow) is sub-3-second. A small minimum-duration cut handles
  ~80 % of the noise without needing to draw zones.

Per-camera setting `notification_min_duration_seconds` (default 3,
range 1–60). 1 s preserves "notify me on any flicker"; 60 s
notifies only for sustained activity.

#### Q2 — coalescing model: **time-window per-camera**

Spec asks: time-window / event-group — pick one.

Choose **time-window per-camera** because:

- Event-group requires defining "what's an event group?" — a
  meta-event concept the system doesn't have yet. Doable but
  another moving part.
- A simple per-camera window — *"don't fire a second notification
  for this camera within N seconds of the last one"* — handles
  the bursty-motion case at the cost of one persisted timestamp
  per camera. Tiny.
- The same pattern is already in use for the camera-offline
  alerts (#136 — `last_offline_alert_at` + 5-min cooldown). We
  reuse the discipline.

Per-camera setting `notification_coalesce_seconds` (default 60).
Within the window, additional motion events are still recorded in
the alert center inbox; only the *browser notification* is
suppressed.

### Architecture

```
┌── Camera ─────────────┐    ┌── Server ─────────────────────────┐
│                       │    │                                   │
│ Picamera2 + detector  │    │  Existing pieces:                 │
│   │ (ADR-0021)        │    │    AlertCenterService             │
│   ▼                   │    │    MotionEventStore               │
│ MotionEvent {start,   │HTTP│    MotionClipCorrelator           │
│  end, duration,       │───▶│                                   │
│  peak_score}          │    │  New pieces (this ADR):           │
│                       │    │    NotificationPolicyService      │
└───────────────────────┘    │    SnapshotExtractor              │
                             │    GET /api/v1/notifications/...  │
                             │                                   │
                             └────────┬──────────────────────────┘
                                      │
                                      ▼ (polled every 30 s by the
                                         existing dashboard JS)
                             ┌─────────────────────────────────┐
                             │ Browser (open tab)              │
                             │   Web Notifications API         │
                             │   new Notification(title,       │
                             │     {body, icon: snapshotURL})  │
                             └─────────────────────────────────┘
```

#### NotificationPolicyService (server)

New service. Single public method:

```python
def select_for_user(self, user: str, since: str) -> list[dict]:
    """Return motion alerts the user opted into and that pass
    every filter (duration, coalesce, per-camera enable). One
    entry per surfaceable notification."""
```

Decision tree per motion event (called from `MotionEventStore`'s
phase-end hook):

1. `event.duration < camera.notification_min_duration_seconds`?
   → **drop**.
2. `event.peak_score < MOTION_NOTIFICATION_THRESHOLD`?
   → already filtered upstream by `AlertCenterService`; no
   extra check needed.
3. `time_since(camera.last_notification_at) <
   camera.notification_coalesce_seconds`?
   → **suppress** (event still lands in the alert-center inbox).
4. `camera.notification_rule.enabled is False`?
   → **drop** (per-camera mute).
5. Otherwise → eligible. Stamp
   `camera.last_notification_at = now`. Return.

#### Wire / API

```
GET  /api/v1/notifications/pending?since=<iso>
  Returns: [{
    alert_id, camera_id, camera_name, started_at,
    duration_seconds, snapshot_url|null, deep_link
  }, ...]
  Empty list when nothing surfaceable.

POST /api/v1/notifications/seen body: {alert_ids: [...]}
  Marks notifications as delivered to this browser session so
  subsequent polls don't re-surface them. Per-user, per-session
  (in-memory plus per-user persisted "last_seen" timestamp for
  cross-session continuity).

GET  /api/v1/users/<id>/notification-prefs
PUT  /api/v1/users/<id>/notification-prefs
  Body: {
    enabled: bool,
    cameras: {<cam_id>: {enabled: bool|null,
                          min_duration: int|null,
                          coalesce: int|null}}
    // null inherits the camera-level default below.
  }
```

#### Per-camera defaults (Camera model additions)

```python
# Added to monitor/models.py Camera dataclass:
notification_rule: dict = field(default_factory=lambda: {
    "enabled": True,                       # opt-in cameras notify
    "min_duration_seconds": 3,             # filter
    "coalesce_seconds": 60,                # window
})
last_notification_at: str = ""             # ISO-8601 UTC, "" when never
```

#### Per-user preferences (User model additions)

```python
notification_prefs: dict = field(default_factory=lambda: {
    "enabled": False,                      # global on/off (default OFF
                                           # per spec: "ship disabled
                                           # by default until browser
                                           # enrollment is complete")
    "cameras": {},                         # per-camera overrides
                                           # keyed by cam_id; values
                                           # are partial dicts that
                                           # override the camera-level
                                           # default
})
last_notification_seen_at: str = ""        # cross-session continuity
```

#### Snapshot pipeline

A still image extracted from the correlated MP4 clip:

1. On motion `phase=end`, if the clip-correlator finds a clip,
   extract a frame at `started_at + 1.0 s` (well past encoder
   warm-up, well before the action ends).
2. Store as `<clip_path>.jpg` (sibling to `.mp4`).
3. Wire format: `snapshot_url = "/api/v1/recordings/<cam>/<date>/<file>.jpg"`.
4. Falls back to `null` when the clip isn't on disk yet (motion
   mode pre-roll race, network blip during clip write). The
   notification still fires — text-only — per the spec's
   "missing snapshot should fall back to text-only notification."

Extraction tool: `ffmpeg -ss 1 -i <clip>.mp4 -frames:v 1 -q:v 4 <clip>.jpg`.

Synchronous in the phase-end handler. Bounded: one ffmpeg per
motion event. The recordings filesystem is already on the SSD-
mounted `/data` per ADR-0017; this adds ~50 KB per motion event
to disk, negligible.

#### Browser delivery (no service worker)

Two reasons for not using Web Push + service worker:

1. **Cloud dependency.** Web Push subscriptions go through the
   browser vendor's push relay (Mozilla, Google, Apple). That's
   exactly the cloud touch the spec's non-goal "no cloud
   notification service requirement" rules out.
2. **Persistence cost.** Service workers + push subscriptions add
   maintenance surface (VAPID keys, subscription lifecycle, expiry)
   for a feature that's expected to fire when the operator's
   dashboard tab is *open* — which is the normal case for an
   indoor monitoring product.

Implementation in `base.html`'s polling:

```js
// On dashboard load, request permission once if user has
// notifications enabled.
if (notificationPrefs.enabled && Notification.permission === 'default') {
    await Notification.requestPermission();
}
// Existing 30s poll already fetches /unread-count; extend to also
// fetch /notifications/pending if permission is granted.
const pending = await fetch('/api/v1/notifications/pending?since=' + lastSeenAt)...;
for (const n of pending.alerts) {
    new Notification('Motion: ' + n.camera_name, {
        body: humanise(n.started_at) + ' · ' +
              (n.duration_seconds || '?') + 's',
        icon: n.snapshot_url || '/static/images/logo.svg',
        tag: n.alert_id,             // dedupe at OS level too
        data: {deep_link: n.deep_link},
    });
}
// Mark as seen so next poll doesn't re-surface.
await fetch('/api/v1/notifications/seen', {method: 'POST',
    body: JSON.stringify({alert_ids: pending.alerts.map(n => n.alert_id)})});
```

`tag: n.alert_id` makes the OS dedupe within the *same* browser
process even if our polling has a hiccup; double protection
against the spec's "the same event is not delivered repeatedly
due to trivial event churn."

### Settings UI

New "Notifications" tab in Settings (admin sees full controls,
viewer sees their own per-user toggles only):

- Global on/off (per-user; the per-camera UI only renders when
  global is on).
- Permission state ("Browser notifications: granted / denied /
  not requested" with a request button when applicable).
- Per-camera toggle list. Each row shows camera name, enable
  switch, optional override of `min_duration_seconds` and
  `coalesce_seconds` (collapsed by default to keep the list
  scannable).
- "Test notification" button that fires a synthetic notification
  so the user can verify their browser permission + their
  expectations of look-and-feel.

### Follow-up: quiet hours (#245)

Issue #245 extends this ADR's decision tree with one more delivery-side
gate: a per-user quiet-hours schedule, plus per-camera tri-state
overrides inside the existing `notification_prefs.cameras` map. The
alert-center inbox remains the source-of-truth surface, while
`NotificationPolicyService` now suppresses only the active browser
notification path when an event's local `ended_at` falls inside a quiet
window. Suppressed events deliberately do not stamp
`last_notification_at`, and a rate-limited `NOTIFICATION_QUIETED` audit
entry makes the divergence observable without leaking the full schedule
body into the audit log. See
[`docs/history/specs/245-quiet-hours.md`](../specs/245-quiet-hours.md)
for the detailed acceptance criteria and risk analysis.

## Alternatives considered

### A. Web Push + service worker (true server-pushed even when tab is closed)

Rejected per "no cloud notification service requirement" non-goal.
Web Push subscriptions route through vendor push relays.

### B. Activity-zone filtering for v1

Rejected per Q1 above: needs a polygon editor + snapshot frame +
persistence + per-zone rules — that's a feature on its own, not
a filtering model for *this* feature.

### C. Priority-tag filtering for v1

Rejected: requires operator input per camera + a taxonomy. Same
size-of-feature concern as zones.

### D. Event-group coalescing

Rejected: requires defining an "event group" concept the system
doesn't have. Time-window-per-camera is empirically equivalent
for the burst case and is one persisted timestamp.

### E. Native mobile delivery

Rejected per spec non-goal.

### F. Email / SMS delivery

Rejected per spec ("no cloud relay") and ADR-0024's
"local-first" rule.

### G. Notification via the existing alert center inbox only

Rejected as a v1 — that's what the bell badge already gives;
the *spec* asks specifically for browser notifications with a
snapshot. Inbox stays as the persistent triage surface; this ADR
is the timely-delivery surface.

## Consequences

### Positive

- Operators learn about motion within ~30 s of it ending (poll
  cadence) without having to be looking at the dashboard tab —
  as long as they have *a* tab open somewhere.
- No cloud dependency, no service worker maintenance surface, no
  VAPID keys.
- Per-user opt-in matches the spec's "must remain opt-in"
  privacy requirement.
- Per-camera rules + duration filter + time-window coalesce are
  the simplest filters that give operators meaningful noise
  reduction.
- Snapshot pipeline reuses the existing recordings store; no
  new persistent surface.
- Builds on (not parallel to) the alert center — every notification
  IS already an inbox row; the notification just lights up the
  OS-level surface for the timely subset.

### Negative

- Notifications fire only while a dashboard tab is open
  somewhere. Closed-tab notifications would need Web Push.
  Acceptable for a home monitoring product; the bell badge
  catches you up on next open.
- Each motion `phase=end` adds one synchronous ffmpeg invocation
  for the snapshot. At typical home-camera motion rates
  (~10 events/day across the fleet), this is a non-issue. A
  pathological "every 2 seconds for an hour" noise scenario
  would queue ffmpeg invocations; deferred per the
  duration-filter (sub-3s events drop before extraction).
- Per-user preferences add ~2 KB to `users.json`. Per-camera
  rules add ~100 bytes to `cameras.json`. Both negligible.

### Neutral

- The `last_notification_at` cooldown is per-camera, not
  per-(camera, user). If two operators are watching, both get
  the burst-coalescing. That's the intended behaviour — the
  cooldown lives on the *event* axis, not the *audience* axis.

## Implementation outline

This ADR is the contract. Real PRs land later in this order:

1. **#128 Backend/API: NotificationPolicyService** + new routes +
   per-camera/per-user model fields + snapshot extractor +
   contract tests. Server-only Python; fully unit-testable on
   this Windows box.
2. **#129 Frontend/UI: Settings → Notifications tab** + the
   polling-and-fire-Notification logic in `base.html` +
   permission-request flow.
3. **#130 Verification/Docs**: structural anchor tests for the
   Settings UI; manual cross-browser permission flow on Chrome,
   Firefox, Safari.
4. **#127 Release 01 umbrella close** when 1–3 ship.

Steps 1 and 2 are server-only/web-only; no camera firmware
change, no hardware verification needed. Step 3's cross-browser
permission flow needs a real browser with the dashboard open and
a real motion event — the closest existing analogue is hardware
verification, but it's much lighter (a single tab, a hand-wave
in front of `.148`'s ZeroCam).

## Validation

- **Unit**: `NotificationPolicyService` — duration filter,
  coalesce window, per-camera enable, per-user enable, snapshot
  fallback, last_notification_at stamping.
- **API contract**: `/notifications/pending` shape + the new
  fields on Camera/User models.
- **Snapshot**: ffmpeg extraction error handling (clip missing,
  ffmpeg not in PATH, disk full).
- **Browser test (manual)**: dashboard tab open, opt in, hand-
  wave at a paired camera, verify a notification fires within
  ~35 s with the right snapshot, click → opens `/events/<id>`.

## Risks

| Risk | Mitigation |
|---|---|
| Clip not yet on disk when phase-end fires (motion mode pre-roll race) | Snapshot extractor returns null; notification fires with text-only icon (the project logo) per the spec's stated fallback |
| Per-camera `last_notification_at` skipping a notification the user actually wanted | Documented in the Settings UI: "Cooldown: notify at most every N seconds." Not a bug; it's the coalesce contract. Operators can shrink the window if they want every event. |
| Operators denying browser permission then never seeing the request again | Settings UI shows current permission state explicitly. If denied, the panel says "Notifications denied — re-enable in your browser's site settings" with a help link to the per-browser instructions. |
| Notifications fire repeatedly during a single sustained-action event with multiple motion sub-events | The coalesce window plus the alert-id `tag` deduplicates at both server and OS levels. |
| ffmpeg invocation cost on a Pi 4B server during a motion storm | Duration filter drops sub-3s events *before* extraction. Sustained 10+ events/min would be unusual on a home setup; if it happens, the right answer is to tune the duration threshold per camera. |

## Completion Criteria

- [ ] `NotificationPolicyService` ships + has full test coverage.
- [ ] `/api/v1/notifications/pending` and `/seen` routes ship.
- [ ] Per-camera `notification_rule` + `last_notification_at`
      added to `Camera`; per-user `notification_prefs` +
      `last_notification_seen_at` added to `User`.
- [ ] Settings → Notifications tab renders for both admins +
      viewers (admins see all cameras' rules; viewers see only
      their own per-user toggle).
- [ ] Snapshot pipeline extracts a frame on motion `phase=end`;
      404 fallback for not-yet-on-disk; verified via integration
      test with a fixture clip.
- [ ] Polling loop in `base.html` fires `new Notification(...)`
      for new pending alerts when permission is granted.
- [ ] Hardware verification: hand-wave at a camera, see the OS
      notification appear within ~35 s, click → land on the
      event detail.
- [ ] CHANGELOG entry.
- [ ] #121, #127, #128, #129, #130 all closed by the merging
      PRs.

## References

- Issue #121 (umbrella) + sub-issues #127, #128, #129, #130
- Spec: [`docs/history/specs/r1-rich-motion-notifications.md`](../specs/r1-rich-motion-notifications.md)
- ADR-0021 (motion detection — events this consumes)
- ADR-0024 (alert center — the persistent surface this layers on top of)
- ADR-0023 (fault framework — referenced for "shared alerting abstraction" intent)
- #136 (camera offline alerts — same `last_*_at` cooldown pattern this re-uses)

# ADR-0018: Dashboard Information Architecture

**Status:** Proposed
**Date:** 2026-04-17
**Deciders:** vinu-dev

---

## Context

### Problem

The current `/` dashboard is a grid of live-video tiles — one tile per
paired camera, each running a WebRTC/HLS player on mount. That is a
**wall display**, not a dashboard:

1. It answers none of the four questions a homeowner actually has when
   they open the app:
   - "Is everything OK right now?" (glance)
   - "Did anything happen while I was away?" (investigate)
   - "Is the system healthy?" (health-check, e.g. after a power cut)
   - "Where do I change X?" (configure — already served by the nav bar)
2. It burns bandwidth and camera CPU to render live tiles the user is
   not watching — directly at odds with ADR-0017's on-demand streaming
   goals. Opening the dashboard starts every camera.
3. It surfaces no system state: a camera can be offline, the recorder
   disk can be 98 % full, the audit log can be full of errors, and the
   dashboard shows none of it until the user opens Settings.
4. Raw telemetry (CPU %, RAM, temperature) is exposed nowhere — or, if
   we simply add it, it becomes decoration because there are no
   thresholds that turn numbers into states.

### Goal

- A user who opens the app and sees green closes the tab in under five
  seconds and feels confident — the dashboard's primary job.
- A user who sees amber or red knows **what** is wrong and **where to
  go** to fix it, without scrolling.
- Live video stays on `/live` where it belongs; the dashboard does
  **not** start camera streams.
- Raw metrics live on a future `/diagnostics` page; the dashboard only
  shows **derived state** (healthy / warning / critical).
- The design survives feature growth: motion events, multiple recorder
  hosts, off-LAN access — each should slot in without a re-layout.

### Non-Goals

- User-customisable widgets / drag-resize. Homeowners do not customise
  dashboards; they expect the vendor to have thought about it.
- Long-range graphs (7 d / 30 d). Time-series belongs on
  `/diagnostics` and needs a time-series store (deferred — see
  "Deferred" below).
- Weather, clock, welcome banner, or any non-product widget.
- Replacing `/live`. The live grid remains, unchanged, on its own tab.
- Motion detection. Surfaced as "last activity = last clip recorded"
  until a motion ADR lands; the API shape already accommodates
  `event_type = motion` for the future.

---

## Decision

Adopt a **three-tier information architecture** for the dashboard:

```
┌──────────────────────────────────────────────────────────┐
│  Tier 1 — status strip (always visible, one line)        │
│  ● All systems normal · 3 cams online · 68 % disk        │
├─────────────┬──────────────────────┬─────────────────────┤
│  Tier 2 — four summary tiles (above the fold)            │
│  Cameras    │ Last activity        │ Storage │ Recorder  │
├─────────────┴──────────────────────┴─────────────────────┤
│  Tier 3 — on-demand detail (scroll)                      │
│  Recent events (last 10) · Camera roll-call · Log teaser │
└──────────────────────────────────────────────────────────┘
```

### Tier 1 — System health strip

One sentence, one colour, rendered from a single
`GET /api/v1/system/health` call. Three states, derived by the server
from existing signals (no new metrics collection needed):

| State | Condition |
|---|---|
| **Green** — "All systems normal" | All paired cameras online; recorder disk < 70 %; no error-level audit entries in the last hour |
| **Amber** — "{what} needs attention" | Any one of: a camera offline < 1 h; disk 70–90 %; any warn-level audit entry in the last hour; recorder CPU > 85 % sustained 5 min |
| **Red** — "{what} requires action" | Any one of: a camera offline ≥ 1 h; disk > 90 %; any error-level audit entry in the last hour; recorder unreachable from the server's own health probe |

The strip is a link — clicking it deep-links to the worst offender
(the offline camera's detail, the Settings > Storage page, etc.).
Thresholds are constants in `monitor/services/health_service.py`,
not user-configurable in slice 1.

### Tier 2 — Four summary tiles

Each tile answers exactly one question in one glance. No tile starts a
video stream.

1. **Cameras** — `3 / 4 online`. Subtitle: name of the offline one, if
   any. Click → `/live`.
2. **Last activity** — latest clip across all cameras. Thumbnail
   (existing snapshot endpoint), camera name, "12 min ago", duration.
   Click → inline plays on the dashboard (reusing the Recordings
   sticky player pattern from ADR-0017 work), **not** a page jump.
3. **Storage** — ring gauge `68 %` with subtitle "~12 days retention at
   current rate." Retention estimate = `free_bytes /
   bytes_per_day_7d_trailing_avg` (falls back to "—" if < 1 day of
   data). Click → Settings > Storage.
4. **Recorder host** — single-line mini-status: `Healthy` (green dot),
   `Warm — 72 °C` (amber, only shown when thresholds crossed), or
   `Unreachable` (red). No raw CPU/RAM numbers on the dashboard. Click
   → `/diagnostics` (future; for slice 1, → Settings > System).

### Tier 3 — On-demand detail

Below the fold. Three sections:

- **Recent events (last 10)** — flat list across all cameras, reverse
  chronological. Row: thumbnail, camera, time, duration, click-to-play
  inline. "View all →" links to `/recordings`.
- **Camera roll-call** — one row per camera: name, status dot,
  last-seen timestamp, last-clip timestamp. No live preview, no
  per-camera CPU/temp on the dashboard — those live on the camera's
  detail page.
- **System log teaser** — last 5 warn-or-error entries from the audit
  log. "View all →" links to Settings > Audit.

### Information classification rule

This is the rule that governs *every* future dashboard decision:

> **Raw metrics belong on `/diagnostics`. Derived state belongs on
> the dashboard.**

A number without a threshold is decoration. If we cannot articulate
"below X this is green, above X it is amber," the number does not go
on the dashboard — it goes on the diagnostics page for engineers.

---

## Rationale

### Why three tiers and not a single grid?

The four user intents have different latency budgets. The glance
("is it OK?") must resolve in under a second — that forces a single
summary line at the top. Investigation ("what happened?") is visual
and wants thumbnails — that fits tiles. Health-check is comparative
across components — that wants a list. A single grid would compromise
all three.

### Why no live video on the dashboard?

Two reasons. First, ADR-0017 pivoted the whole system to on-demand
streaming precisely to stop 24 × 7 camera CPU / Wi-Fi burn; auto-
starting streams on dashboard load would undo that. Second, live
video is the highest-attention UI element on any page — putting it on
the dashboard means the user watches video instead of scanning state,
which defeats the dashboard's purpose. Frigate NVR and UniFi Protect
both separate the two; Home Assistant does not and is widely
criticised for it.

### Why derived state over raw metrics?

Homeowners are not SREs. "Recorder CPU 87 %" is meaningless to them;
"Recorder is working harder than usual" is actionable. Raw numbers are
still collected and exposed — just on `/diagnostics`, where an
engineer (or a support session) can read them. This also future-proofs
threshold tuning: we can change "amber at 85 % CPU" to "amber at 90 %"
without touching the UI.

### Why status-strip colours instead of numbers?

Users develop muscle memory around the colour of the top bar after
two or three logins. That muscle memory is the single most valuable
UX asset the dashboard can build — so we must (a) pick thresholds
conservatively so green really means green, and (b) never change the
thresholds once users rely on them. This ADR locks the thresholds
above for exactly that reason.

---

## Alternatives Considered

### A. Keep the live-tile grid, add a status banner on top

Cheaper change, but leaves the "dashboard auto-starts every camera"
problem in place, continues to bury system state, and the banner on
top of a wall of video just gets ignored. Rejected.

### B. User-configurable widgets (Home Assistant / Grafana style)

Maximum flexibility. Rejected because (1) the target user is a
homeowner, not an integrator; (2) every support call becomes "what
does your dashboard look like?"; (3) widget-config UIs are
disproportionately expensive to build and test well.

### C. Firehose dashboard — every metric we can collect

Temperature gauges, RAM bars, network graphs, event heatmaps.
Rejected on the "numbers without thresholds are decoration" rule.
This *is* the right UI for `/diagnostics`, not for the dashboard.

### D. Mobile-first single-column list (no tiles at all)

Simpler, and the dashboard already must work on phones. Rejected
because on ≥ 1024 px screens four tiles side-by-side are noticeably
faster to scan than a list; the responsive grid already collapses to
one column below 1024 px, so we get the mobile-list experience for
free without sacrificing desktop density.

---

## Consequences

### Positive

- Glance-in-five-seconds UX; green tab closes fast, amber/red
  surfaces exactly one actionable thing.
- Dashboard load no longer starts any camera stream → ADR-0017
  savings preserved.
- New signals (motion events, multi-recorder, off-LAN state) each
  have an obvious home in the existing three-tier structure.
- `/diagnostics` becomes a natural place for the "power user" page
  without cluttering the landing.

### Negative / costs

- Requires a new aggregator endpoint (`/api/v1/system/health`) that
  joins signals from the camera registry, the recordings filesystem,
  the disk, and the audit log. Tested as one unit — any one signal
  missing must degrade gracefully, never 500.
- Retention-days estimate requires a 7-day trailing write-rate — for
  slice 1 we compute it from directory `stat()` at request time
  (acceptable up to ~hundreds of clips); it will need a cached
  rollup once clip counts grow. Flagged for the SQLite ADR.
- Thumbnail generation for "last activity" and "recent events" reuses
  the existing snapshot endpoint; cold-cache cost is one ffmpeg
  invocation per row, one-time per clip. Acceptable for slice 1;
  revisit if we add motion events at higher rate.
- Locks the status-strip thresholds (see table above). Changing them
  later costs user muscle-memory trust and must go through a new ADR.

### Deferred (explicit non-decisions)

- **Per-camera sparklines** (24 h uptime, frame-drop rate). Need
  time-series storage → waits for the SQLite ADR.
- **Thermal push from cameras.** `camera_streamer` currently does not
  push its Pi temperature to the server. Once it does (small protocol
  extension on the health-heartbeat channel — ADR-0016), the Tier 2
  "Recorder host" tile gains a sibling per-camera thermal view in
  Tier 3.
- **Long-range history charts.** `/diagnostics` page, separate ADR.
- **Multi-recorder topology.** Single-recorder assumption throughout;
  the status strip and storage tile will need to aggregate once we
  support more than one recorder host.

---

## Rollout

Three independently-shippable slices, each behind the existing auth
decorators, no DB migrations in any of them.

### Slice 1 — Status strip + four tiles (≈ 2 days)

- `GET /api/v1/system/health` — aggregator: paired cameras + online
  count, disk %, retention-days estimate, recorder CPU/temp snapshot,
  last-error-in-1 h flag. Returns `{state: green|amber|red,
  summary: "...", details: {...}}`.
- `GET /api/v1/recordings/latest` — latest clip across all cameras
  (extends the existing per-camera `latest_clip`).
- Template rewrite of `dashboard.html`: status strip + four tiles,
  no live players.
- Contract tests for both endpoints; unit tests for the health
  aggregator's state transitions at each threshold boundary.

### Slice 2 — Recent events + inline player (≈ 1 day)

- `GET /api/v1/recordings/recent?limit=10` — flat list across
  cameras.
- Reuse the Recordings tab's sticky-player component.
- Hooks for future `event_type` filter (motion) — accepted as a
  query param, ignored until the motion ADR lands.

### Slice 3 — Camera roll-call + log teaser (≈ 1 day)

- Camera roll-call reuses `/api/v1/cameras` + per-camera
  `last_seen` (already present).
- Log teaser reuses `/api/v1/audit?level=warn&limit=5`.
- No new endpoints.

### Out-of-scope for this ADR's rollout

- `/diagnostics` page (own ADR when raw-metrics exposure becomes
  necessary).
- Dashboard internationalisation (existing app is English-only;
  dashboard does not regress this).
- Any mobile-app / PWA work.

---

## Open Questions

1. **Inline player on the dashboard vs. deep-link to `/recordings`.**
   This ADR chooses inline (one click, player pops in-place) because
   the glance-then-investigate pattern is the high-traffic path. If
   telemetry later shows most investigations end with the user
   navigating to `/recordings` for the clip list, collapse Tier 2's
   "Last activity" into a link-only tile in a follow-up.
2. **Is "recorder host" the right fourth tile?** For a
   single-recorder home, yes — disk already covers the main concern.
   If a home ever runs multiple recorders, the tile grows into a
   per-recorder list and may need its own page. Revisit under the
   multi-recorder ADR.
3. **How loud is amber?** No push / email / toast in slice 1 — amber
   is a visual cue, nothing more. Notification channels belong in
   their own ADR.

---

## References

- ADR-0012: UI Architecture (HTMX + Alpine.js patterns, dark-first)
- ADR-0016: Camera Health Heartbeat Protocol (source of per-camera
  online / last-seen signal)
- ADR-0017: On-Demand Viewer-Driven Streaming (why the dashboard
  must not auto-start streams)
- Frigate NVR dashboard (status-first, video on its own tab)
- UniFi Protect "Protect Home" screen (summary tiles + recent events)

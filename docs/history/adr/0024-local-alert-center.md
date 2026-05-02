# ADR-0024: Local Alert Center

**Status**: Proposed — 2026-04-30
**Relates to**: ADR-0003 (service-layer), ADR-0008 (SWUpdate A/B), ADR-0017 (recording modes), ADR-0018 (dashboard IA), ADR-0022 (no backdoors), ADR-0023 (unified fault framework)
**Resolves**: the two open design questions in
[`docs/history/specs/r1-local-alert-center-and-tailscale-remote-review.md`](../specs/r1-local-alert-center-and-tailscale-remote-review.md)
— "badge + inbox vs banners/cards" and "per-user vs per-household unread state."
**Unblocks**: #131–#134 (Local alert center work-streams), and the
"shared notification delivery abstraction" referenced by
#135–#138 (camera offline alerts), #139–#142 (storage low alerts),
#143–#146 (review queue), and #127–#130 (rich motion notifications).

## Context

R1 ships five alert-shaped features: rich motion notifications,
camera offline alerts, storage low / retention alerts, review queue,
and a local alert center umbrella. Each spec defers to "the shared
notification delivery abstraction from the broader Release 01
alerting work" without ever defining what that abstraction is. Until
this is decided, all five are blocked from implementation.

The repo already has three independent "something happened"
surfaces:

1. **AuditLogger** (`monitor/services/audit.py`) — append-only JSON log of
   security-relevant events: `LOGIN_FAILED`, `OTA_COMPLETED`,
   `CAMERA_OFFLINE`, `CAMERA_ONLINE`, `OTA_FAILED`, `OTA_ROLLBACK`,
   `FAULT_OPENED`, `FAULT_RESOLVED`, etc.
2. **MotionEventStore** (`monitor/services/motion_event_store.py`) — typed
   motion events with start/end lifecycle and clip correlation
   (ADR-0021).
3. **FaultService** (ADR-0023) — first-class active-fault registry on
   `/data/faults.json`. Faults open and close; opens emit
   `FAULT_OPENED` audit events.

ADR-0018 already defines the dashboard's information architecture:

- **Tier 1**: one status strip (severity-coloured banner + one
  sentence + deep link). Green is quiet.
- **Tier 2**: four summary tiles.
- **Tier 3**: recent events feed + audit log teaser.

The R1 alerting features collectively need:

- a way for users to **see recent alert-worthy state changes** in one
  place;
- per-user **unread / read state** so a "you have N new alerts"
  affordance is honest;
- a **deep-link** path so a user can tap a status-strip banner
  on the dashboard and land in the alert center filtered to the
  source of the warning;
- a **remote story** that does not require browser-vendor push,
  email, SMS, or any cloud relay — Tailscale to the local UI is the
  remote story.

## Decision

**The alert center is a derived view over the existing event sources, not a new persistent store of alerts. Per-user unread state is the only new persisted concept.**

Two design calls, both flagged as open questions in the feature
spec, are resolved here:

### Q1: badge + inbox in v1, no new banners or cards.

ADR-0018 says **"One banner. State colour + one sentence. Green is
quiet."** Adding alert banners or cards above / beside the existing
status strip violates that discipline and creates "banner soup"
where the user has to read three or four overlapping pieces of
chrome to understand what's wrong.

The alert center surface in v1 is exactly two things:

1. A **count badge** in the primary nav (red dot + integer when
   unread > 0; absent when 0).
2. A dedicated **`/alerts` inbox page** — table view, severity-
   coloured rows, source-type filter chips, per-row "mark read"
   button, panel-level "Mark all read."

The existing Tier-1 status strip is the only dashboard banner.
Its `deep_link` field — already in the payload — routes to
`/alerts?source=<dominant-source>` whenever state ≠ green, so
tapping the banner takes you straight to the relevant inbox row.

Future slices may add density: dashboard cards, mobile push, email
delivery. Those are explicit non-goals for v1 (per the spec); when
they land they plug into the same alert model documented below
without changing semantics.

### Q2: per-user unread state.

The codebase already has user accounts (`UserService`,
`/api/v1/auth/me`, role gating) and admin-vs-viewer is enforced.
A two-admin household where one admin clears an alert and the
other admin's nav badge silently empties is a bad surprise — the
second admin has no idea anything happened. Per-user read state
matches the existing role-aware UI surfaces (admin click-to-copy
camera IP, admin-only audit teaser, etc.).

Per-household / global unread state is a non-goal. If a future
release needs a "this alert was triaged by Alice 3 hours ago"
affordance, that's a separate per-alert-per-user state field — not
a shared global state.

### Architecture

```
┌─────────────────────  Source layer (unchanged)  ────────────────────┐
│                                                                     │
│  AuditLogger             MotionEventStore         FaultService      │
│  (append-only JSON)      (motion events,          (active faults +  │
│                           clip correlation)        opens / closes)  │
│      │                          │                       │           │
│      └──────────────┬───────────┴────────────┬──────────┘           │
│                     │                        │                      │
└─────────────────────┼────────────────────────┼──────────────────────┘
                      ▼                        ▼
              ┌───────────────────────────────────────┐
              │  AlertCenterService  (new, derive-only)│
              │                                       │
              │  - reads existing sources             │
              │  - filters to alert-worthy events     │
              │  - joins per-user read state          │
              │  - returns ordered, paginated list    │
              └───────────────────────────────────────┘
                            │
                            ▼  Per-user read state (new, persisted)
              ┌─────────────────────────────────────┐
              │  /data/config/alert_read_state.json │
              │  { user_id: { alert_id: ts_read }} │
              └─────────────────────────────────────┘
```

`AlertCenterService` is **stateless** with respect to alerts
themselves — every call walks the source stores and joins read
state. Cardinality is small (audit log rotates at 50 MB, motion
events capped at 5000 globally, faults at most tens), so the
read-side cost is fine.

The only new persistent state is the per-user read map, which is
tiny: `O(users × alerts_user_has_read)`.

### Alert identity

Alerts are derived but their identifiers must be **stable across
reads** so per-user read state survives. Use the source's natural
identifier with a typed prefix:

| Source | Alert ID |
|---|---|
| Fault (active) | `fault:<fault_uuid>` |
| Audit event | `audit:<sha256(timestamp,event,user,detail)[:16]>` |
| Motion event | `motion:<motion_event_id>` |

`audit:` IDs are content-hashed because the audit log doesn't have
its own UUID — but every audit line is unique by `(timestamp, event,
user, detail)` so a 16-char SHA-256 prefix is collision-safe at the
log's cardinality.

Renaming an alert ID scheme is a breaking change — we'd lose all
existing per-user read state. Lock the prefixes now and add new
sources with new prefixes.

### What counts as an alert (v1 catalogue)

Server-side filter applied by `AlertCenterService` against the
source streams:

| Source | Inclusion rule |
|---|---|
| FaultService | All currently-open faults of severity `warning` / `error` / `critical`. `info` faults excluded. |
| AuditLogger | `OTA_FAILED`, `OTA_ROLLBACK`, `CAMERA_OFFLINE`, `CERT_REVOKED`, `FIREWALL_BLOCKED`. **Not** `LOGIN_FAILED` (audit teaser is the right surface for that — drowns the alert center). **Not** `LOGIN_SUCCESS`, `OTA_COMPLETED`, `CAMERA_ONLINE` (these are good news, not alerts). |
| MotionEventStore | Closed motion events with `peak_score >= notification_threshold`, where the threshold is per-camera and configured separately by #121 (rich motion notifications). |

Storage low / retention alerts (#139–#142) and the review queue
(#143–#146) plug in by emitting **new audit codes** —
`STORAGE_LOW`, `RETENTION_RISK`, `REVIEW_FLAGGED`. The catalogue
above grows; the architecture above does not change.

### Suppression

**Suppression is a property of the source, not the alert center.**

- AuditLogger already emits one event per transition
  (`CAMERA_OFFLINE` fires once when the heartbeat goes stale, not
  on every poll). Existing behaviour, no change.
- FaultService natively de-duplicates (a fault is open or it isn't,
  no stream of repeats).
- MotionEventStore caps at 5000 with drop-oldest compaction.
- New alert codes (storage low, retention risk) MUST emit on
  threshold *crossing* with hysteresis — not on every metrics tick.
  This is documented as an implementation guardrail in each child
  spec; the alert center does not de-duplicate redundant inputs.

If two sources fire near-simultaneously about the same underlying
condition (rare but possible — e.g. `CAMERA_OFFLINE` audit + a
fault from the same heartbeat), they each get their own row. Users
can mark them read together; we don't pre-merge.

### API

All routes admin-or-viewer with role-aware filtering applied
server-side:

```
GET  /api/v1/alerts?source=&severity=&unread_only=&limit=&before=
     → {alerts: [...], unread_count: N, total: M}
POST /api/v1/alerts/<alert_id>/read
     → {ok: true}
POST /api/v1/alerts/mark-all-read
     body: {source?, severity?, before?}        # same filters as GET
     → {marked: N}
GET  /api/v1/alerts/unread-count
     → {count: N}                                # for the nav badge
```

`/alerts/<alert_id>/read` is **idempotent** — re-marking a
read alert is a no-op. There is no "mark unread" in v1.

CSRF protection on the POST routes (existing
`@csrf_protect` decorator from `monitor/auth.py`).

### Permission model

- **Viewers** see only alerts whose source data they're authorised
  to see today. Audit-derived alerts are admin-only (matches the
  existing audit teaser gating from #148). Fault-derived and
  motion-derived alerts are visible to viewers (cameras and motion
  events are already viewer-visible on the dashboard).
- **Admins** see everything.
- The `/alerts` page checks role and filters before rendering.
  Server-side filter is the source of truth; the UI doesn't render
  stale-but-ungated rows. (Same defence-in-depth pattern as the
  audit teaser fix in #148.)

### Storage

`/data/config/alert_read_state.json`. Same atomic-write pattern as
the rest of the codebase (`tempfile.mkstemp` + `os.replace`).
Schema:

```json
{
  "schema_version": 1,
  "users": {
    "alice": {
      "fault:550e8400-e29b-41d4-a716-446655440000": "2026-04-30T08:14:02Z",
      "audit:8a3f9b2c1e4d5f6a": "2026-04-30T07:55:12Z"
    },
    "bob": {
      "motion:mot-20260430T071122Z-cam-d8ee": "2026-04-30T07:14:00Z"
    }
  }
}
```

A user being deleted (via `UserService.delete`) cascades a delete
of their entry in this file — handled by `AlertCenterService` via
a `UserService` callback hook (one new callback registration in
the existing user-deletion path; no new lifecycle).

Compaction: when an alert source ages out (audit log rotates,
motion event drops, fault resolves and is purged after retention),
the corresponding read-state entry becomes orphaned but harmless.
A monthly sweeper drops orphans whose alert no longer exists. Not
v1-blocking; tiny disk footprint either way.

### Wire / UI surfaces

- **Primary nav badge.** Polls `/api/v1/alerts/unread-count` every
  30 s. Red dot + integer when count > 0. No notification sounds
  in v1 (deferred — sound on a status surface is a UX research
  question, not just a config flag).
- **`/alerts` page.** Table: timestamp, source-type pill,
  severity-coloured bar, message, "mark read" action,
  drill-in link. Filter chips: source type, severity, unread-only.
  Pagination via `before=` cursor.
- **Status-strip integration (existing dashboard).** When
  `summary_state ≠ green`, the strip's `deep_link` field points at
  `/alerts?severity=error`. No new chrome on the dashboard itself.
- **Mobile.** Existing responsive CSS already handles the
  primary nav; the `/alerts` page is built mobile-first like the
  rest of the dashboard. No native app, no PWA, no push — per the
  feature spec's non-goals.
- **Tailscale.** Identical UI served over the existing Tailscale
  link (ADR-0017, ADR-0018). No alert-center-specific remote
  delivery code.

## Alternatives considered

### Persistent alert table

Store alerts as their own first-class records, separate from
audit / motion / fault sources. Rejected:

- Doubles the storage cost for no behavioural gain — every alert
  already lives in one of the three source stores.
- Creates two sources of truth that can drift (alert says
  unresolved, fault says resolved).
- Forces a write path on every alert-worthy event, increasing the
  surface area for partial-failure modes.
- The "Decision" section's derive-on-read approach is exactly the
  pattern `SystemSummaryService` already uses for the Tier-1
  status strip, so we're not inventing anything.

### Per-household global unread state

One read-flag per alert; clearing affects every user. Rejected:

- Surprising in any multi-admin household — one admin's triage
  silently changes another admin's nav badge.
- Inconsistent with existing per-user surfaces in the dashboard
  (settings, role-gated controls).
- The implementation cost of per-user state is nominal and small.

### Banners + cards on the dashboard for active alerts

Add severity-coloured banners or summary cards directly on the
dashboard for the top N active alerts. Rejected:

- Conflicts with ADR-0018 §Tier 1 ("One banner. State colour +
  one sentence. Green is quiet.").
- Creates redundant chrome — the status strip already conveys the
  "something is wrong" message; banners restating it twice is
  noise, not signal.
- The deep-link from the strip into `/alerts` gives the user the
  same one-tap path with less visual real estate.

### Browser-vendor push / email / SMS / cloud relay

Forward alerts to vendor delivery infrastructure. Rejected:

- Explicitly listed as non-goals in the feature spec.
- Conflicts with the local-first product direction (ADR-0001 / the
  no-internet-by-default execution rule).
- The Tailscale story is the remote story.

### Polling vs server-sent events vs websockets for the nav badge

Considered SSE / websockets to make the badge instantaneous.
Rejected for v1: a 30 s poll is operationally indistinguishable
from real-time for an alert UI on a home-security product, and
adding a long-lived connection requires reverse-proxy work in
nginx that isn't worth the deferred latency saving. SSE is the
upgrade path if v2 needs it.

## Consequences

### Positive

- One implementation surface unblocks five feature buckets (#131–
  #134, #135–#138, #139–#142, #143–#146, #127–#130).
- No new persistent stores beyond a small per-user JSON file.
- Source storage (audit, motion, faults) stays exactly as ADR-0023
  / ADR-0021 / existing AuditLogger left it.
- Per-user read state matches the existing role-aware UI patterns,
  no behavioural surprises.
- ADR-0018's banner-discipline rule is honoured.

### Negative

- Derive-on-read means every `/api/v1/alerts` request walks the
  audit log + motion store + fault registry. At current cardinality
  (~5000 events × 3 sources = small) this is fine, but if any
  source grows by 100× we'll need an in-memory index. Mitigation:
  pagination via `before=` cursor caps per-call work; an in-memory
  index can be added to `AlertCenterService` without changing the
  API.
- The `audit:` SHA-256-prefix alert ID is opaque to the user. We
  show the underlying audit-event metadata in the row, so the ID
  itself never appears in UI; it's just a stable per-user-read-
  state key. Operators inspecting the JSON file directly will see
  it — documented in `docs/architecture/`.
- Per-user read state on a deleted user must be cleaned up. Adds
  one callback registration to the user-deletion path. Documented
  but a real coupling.

### Neutral

- Existing audit/motion/fault behaviours don't change. Backwards
  compat is structural — pre-this-ADR audit lines and motion
  events are read by the new service exactly as they're read by
  the old surfaces today.

## Implementation outline (informational — actual PRs land later)

This ADR is the contract. Real PRs will be:

1. **Backend/API: Local alert center (#132)** —
   `AlertCenterService`, `/api/v1/alerts/*` routes, the JSON
   read-state store, the user-deletion cascade hook, contract
   tests. Pure server Python, fully unit-testable.
2. **Frontend/UI: Local alert center (#133)** — nav badge, `/alerts`
   page template + Alpine state, filter chips, mark-read flow.
   Browser-level smoke + manual viewer-vs-admin verification.
3. **Verification/Docs: Local alert center (#134)** — user docs,
   admin runbook for the read-state JSON, CHANGELOG.
4. **Release 01: Local alert center (#131)** — closes when 1–3
   ship.

After (1)–(4) merge, the dependent feature buckets become
implementable: each adds its own source-side rules (storage
hysteresis, camera offline transitions, etc.) and emits the new
audit codes the catalogue above mentions. The alert center itself
doesn't need to change.

## References

- Feature spec: [`docs/history/specs/r1-local-alert-center-and-tailscale-remote-review.md`](../specs/r1-local-alert-center-and-tailscale-remote-review.md)
- ADR-0018 (dashboard IA — banner discipline)
- ADR-0023 (fault framework — one of the three alert sources)
- ADR-0021 (camera-side motion detection — motion events)
- `app/server/monitor/services/audit.py` — AuditLogger
- `app/server/monitor/services/motion_event_store.py` — MotionEventStore
- `app/server/monitor/services/system_summary_service.py` — derive-on-read precedent
- Issue #148 — viewer-role flash fix; same defence-in-depth pattern applies here

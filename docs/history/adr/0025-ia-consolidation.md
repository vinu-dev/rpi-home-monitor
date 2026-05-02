# ADR-0025: Information Architecture Consolidation

**Status**: Proposed — 2026-05-01
**Supersedes (in part)**: ADR-0018 §"Tier 3 (slice 3) — Audit log teaser"
**Relates to**: ADR-0018 (dashboard IA), ADR-0024 (local alert center)

## Context

Three releases of incremental work — ADR-0018 (dashboard tiers,
2026-04), Settings → Security tab (#147, 2026-04), and ADR-0024 +
its #208/#133 implementations (alert center, 2026-04-29) — were
each defensible in isolation but together produced a user-facing
surface area where the same data appears in three places:

| Data | Surface 1 | Surface 2 | Surface 3 |
|---|---|---|---|
| Motion events | Dashboard "Recent events" feed (Tier-3) | `/alerts` page (motion source) | `/events` page (full archive) |
| Audit events | Dashboard "Recent activity" teaser (admin-only) | `/alerts` page (audit source, admin) | `/logs` + Settings → Security tab (admin) |
| Faults | Dashboard camera-card chips | `/alerts` page (fault source) | (also visible in camera details panel) |

This is bad UX:

- Operators have three candidate answers to *"did anything happen?"*
- Marking an alert read on `/alerts` doesn't propagate to the
  dashboard teaser, which keeps showing the same row → "I cleared
  it, why is it still here?"
- New alert sources land in three implementations instead of one.

ADR-0018 didn't anticipate ADR-0024. The audit-teaser slice (Tier 3
slice 3) was layered on the dashboard *because* there was no
unified inbox. Once the inbox shipped, the teaser became a
duplicate. Same story for Settings → Security tab embedding the
audit log table — that table is `/logs`'s job.

## Decision

**One job per surface. Where two surfaces overlap, the more
specific (read-state-aware) one wins.**

| Surface | Single job | Role gates |
|---|---|---|
| Dashboard | "Is the system OK right now?" — Tier-1 status strip + Tier-2 tiles + Tier-3 motion events feed (inline playback). **No** audit teaser, **no** alert mini-list. | Viewer + admin. Admin-only chrome (Scan/Add/Pair/Delete/IP/health metrics) is gated via `isAdmin`. |
| `/alerts` | "What needs my attention?" — derive-on-read inbox over fault + motion + audit sources, with per-user read state, filters, and importance sort. | Viewer + admin (server-side filter applies — viewers see fault+motion only; admins see audit too). |
| Top-bar bell badge | "Did anything happen since I last looked?" — count of unread alerts, hidden when zero. | Viewer + admin (count reflects role-aware filter). |
| `/events` | "Show me the motion archive." | Viewer + admin. |
| `/recordings` | "Show me clips." | Viewer + admin. |
| `/logs` | "Investigate the audit trail." Includes the admin-only "Clear all entries" affordance — destructive action lives where the data lives, not in a separate settings tab. | Admin only (route + content). |
| Settings | "Change settings." Genuine configuration only. | Admin only. |
| `/live` | "Show the camera now." | Viewer + admin. |

### What this changes

1. The dashboard's **"Recent activity" audit teaser** (the 5-row
   admin-only mini-log under the events feed) is removed. The bell
   badge in the top bar handles the *"did anything happen?"*
   affordance abstractly; `/alerts` handles the triage detail.

2. **The Settings → Security tab is removed entirely.** Settings
   is for things you configure; an audit log is a viewer, not a
   setting. Putting log-management in Settings was a category
   error inherited from the original `#147` work. The clear-log
   admin action moves to **`/logs` itself** — small text-button
   in the page-section header, two-step confirm, gated by
   `isAdmin` resolved from `/auth/me`. Destructive action lives
   where the data lives.

3. The dashboard's **"Recent events" motion feed (Tier-3)** is
   **kept**. It does a different job: inline H.264 playback of
   the latest 5 motion events without navigating away. The bell
   badge gives the headline; the events feed gives the preview.
   These compose; they don't duplicate.

### What this does *not* change

- Per-user read state, alert filtering rules, the catalogue of
  "what counts as an alert," and ADR-0024's design otherwise —
  unchanged.
- ADR-0018's Tier-1 status strip + Tier-2 tiles — unchanged.
- `/events`, `/recordings`, `/logs`, `/live` — unchanged.
- The Settings → Security tab's "Clear log" admin action and its
  audit semantics — unchanged. Only the inline viewer is removed.

## Role separation (explicit)

The user-visible surfaces split cleanly along role:

**Viewer** sees:
- Dashboard (status strip + tiles + motion events feed, all
  isAdmin-gated chrome hidden)
- `/alerts` filtered to fault + motion only (defence-in-depth in
  `AlertCenterService._compute_alerts()`)
- Bell badge — count reflects viewer-visible alerts
- `/events`, `/recordings`, `/live`
- Settings landing page with the "viewers cannot change settings"
  panel (existing `x-show="!isAdmin"` block)

**Admin** sees everything the viewer sees, **plus**:
- Admin-only chrome on the dashboard (Scan / Add Camera /
  Pair / Delete / IP / health metrics on camera cards)
- Audit alerts in `/alerts`
- `/logs` audit log archive (admin-only at the route + content
  layers)
- Settings → all tabs including Security (audit-log management)

If you find yourself adding admin-only chrome to a viewer-visible
surface, stop and ask whether the chrome belongs on a different
surface. The audit teaser was an example of getting this wrong:
admin chrome on the viewer-visible dashboard, redundant with the
dedicated triage surface that already had its own admin filter.

## Alternatives considered

### Option A — Status quo (three surfaces, deliberately overlapping)

Rejected. The cost — operator confusion, stale-state mismatches
between teaser and `/alerts`, three implementations to maintain —
outweighs the marginal benefit of "you can see audit events
without leaving the dashboard."

### Option B — Collapse `/alerts` into the existing surfaces

Add unread state and severity sort to `/events` and `/logs`
respectively, drop `/alerts`. Rejected. Loses the *unified*
cross-source inbox, which is exactly what makes the alert center
useful for triage. An incident often spans audit + fault + motion
(e.g. camera goes offline → CAMERA_OFFLINE audit + sensor_missing
fault + missing motion); a unified view tells the story.

### Option C — Keep all three surfaces, rename clearly

Rename the dashboard audit teaser to *"Recent admin activity"* and
the alert center to *"Triage queue."* Rejected. Renaming doesn't
remove the duplication — the same audit row would still appear
twice with two different titles. UX writing on top of structural
overlap is lipstick.

### Option D (chosen) — Retire the duplicate

Specifically: remove the dashboard audit teaser (admin-only, strict
duplicate of `/alerts`'s admin view) and trim Settings → Security
to a settings panel only.

The dashboard's motion events feed survives because it does a
different job (inline preview). Apple's design language doesn't
say "remove everything" — it says *"let each surface do one
thing well."* The motion feed and `/alerts` do two things; the
audit teaser and `/alerts` did one thing in two places.

## Consequences

### Positive

- One canonical answer to "did anything happen?" — bell badge.
- One canonical answer to "let me triage" — `/alerts`.
- One canonical answer to "show me the audit archive" — `/logs`.
- Marking an alert read on `/alerts` doesn't leave a stale row
  somewhere else.
- New alert sources plug into one place, not three.

### Negative

- Admins lose the dashboard's at-a-glance audit preview. They have
  to tap the bell to see the rows. Mitigation: the bell badge
  itself communicates whether anything's there ("3 unread"); and
  the Tier-1 status strip's `deep_link` already routes to /alerts
  on amber/red so urgent things still make themselves known.
- Settings → Security tab's "look at recent events" path now
  requires a click to `/logs`. Mitigation: this is the *settings*
  surface, not the operator's daily triage path; operators use
  `/alerts` for that.

### Neutral

- Tests and docs update together with this PR. The audit-teaser
  regression test from issue #148 (which pinned the teaser's
  default-hidden behaviour) is replaced with a regression test
  pinning the teaser's *absence*. Same defence-in-depth pattern.

## Implementation

Single PR:

- `monitor/templates/dashboard.html` — remove the audit-teaser HTML
  block (lines 110–142 of pre-PR file); remove `auditEvents` and
  `auditAdmin` Alpine state; remove the inline `/api/v1/audit/events`
  fetch; remove `_auditEventLabel` and `_auditEventClass` helpers
  (only consumed by the teaser).
- `monitor/templates/settings.html` — replace the Security tab's
  inline log table with a "Open audit log →" button to `/logs` and
  the existing "Clear log…" admin action; drop `events` and
  `loading` from the `security` Alpine state; drop `loadAuditLog()`.
  Keep `clearAuditLog()` — it's the only UI for #147's truncation.
- `tests/integration/test_views.py` — two new regression tests
  (audit teaser absent on dashboard; security tab links out to
  /logs and /alerts and has no inline table).
- This ADR.
- ADR README index update.

## Validation

- `pytest app/server/tests/` (full suite — verifying nothing pinned
  the removed structure).
- `ruff check . && ruff format --check .`
- `pre-commit run --all-files`
- Manual verification on the live server (`192.168.1.244`) after
  SSH deploy — log in as both admin and a viewer, confirm:
  - Dashboard renders without the "Recent activity" section for
    admins.
  - `/alerts` still shows audit rows for admins.
  - Bell badge reflects the same count it did before.
  - Settings → Security shows the new "Open audit log →" button
    and clear-log control.
  - `/logs` still works admin-only.

## Completion Criteria

- [ ] Dashboard renders Tier-1 + Tier-2 + Tier-3 motion feed only;
      no audit teaser HTML or Alpine state present.
- [ ] Settings → Security tab is a settings surface (open / clear
      buttons + outbound links), no inline log table.
- [ ] Both regression tests pass.
- [ ] Live deploy on `.244` verified for both roles.
- [ ] CHANGELOG entry written when the next release ships this.

## References

- ADR-0018 (dashboard IA, the design this partially supersedes)
- ADR-0024 (alert center, the unified surface this consolidates around)
- Issue #148 (the `auditAdmin: true → false` fix that defended
  against the same flash this ADR sidesteps by removing the surface)
- PR #208 (alert center backend) and PR #212 (alert center frontend)
- Issue #147 (admin clear-log workflow — preserved by this change)

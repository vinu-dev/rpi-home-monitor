# Feature Spec: Quiet Hours / Scheduled Silencing For Motion + System Notifications

Tracking issue: #245. Branch: `feature/245-quiet-hours`.

## Title

Quiet hours / scheduled silencing of active notification delivery, with the
event still landing in the alert-center inbox.

## Goal

Operators configure recurring time-of-day windows (per weekday, optionally
per-camera) during which active notification delivery is suppressed for
motion events, camera-offline alerts, storage-low alerts, and OTA outcomes.
Suppressed events still land in the alert-center inbox so nothing is lost
— quiet hours only silence the *active push / browser / webhook delivery
path*. The user-visible promise is "I will not be woken up by the porch
light at 03:00, but I can still see what happened when I check in the
morning."

This is a per-user, per-camera schedule. Defaults are empty (no schedule)
so existing installs see no behaviour change on upgrade.

## Why this fits the mission

`docs/ai/mission-and-goals.md` says the product should "feel like a real
product, not a prototype." A motion-detection-capable security appliance
that can't be told "don't ping me at night" is a textbook prototype-feel
anti-pattern — competitors (Ring, Arlo, phone-OS DND) all ship some form of
this. ADR-0027 (#121, merged) shipped per-camera mute and per-user enable;
quiet hours adds the time dimension that the same decision tree always
implied. ADR-0017's `recording_scheduler.now_in_window()` already proves
the per-camera weekday/HH:MM window pattern works in this codebase —
quiet hours reuses the same shape on the notification decision tree.

## Context

Existing code this feature must build on:

- `app/server/monitor/services/notification_policy_service.py:255` —
  `_eligible()` is the merged decision tree from ADR-0027. New
  `_in_quiet_hours()` clause inserts as one more gate in this chain.
  Side-effect-free (the existing `last_notification_at` stamp must not
  fire for suppressed events, otherwise quiet hours would silently
  shorten the post-quiet coalesce window).
- `app/server/monitor/services/recording_scheduler.py:34` — `DAY_INDEX`
  map and `now_in_window(schedule, now)` helper. Already handles
  overnight windows (end <= start splits into D-evening + D+1-morning).
  Quiet hours **reuses** this helper rather than duplicating the
  weekday/HH:MM logic. Lift it to a shared module or import directly;
  Implementer to choose (see OQ-1).
- `app/server/monitor/models.py:138` — `User` dataclass. Add
  `notification_schedule: list[dict]` field (default empty list). Same
  shape as `Camera.recording_schedule` so the UI editor and validation
  helpers can be shared.
- `app/server/monitor/models.py:19` — `Camera.notification_rule` already
  carries per-camera notification policy (`enabled`, `min_duration_seconds`,
  `coalesce_seconds`). Add an optional `quiet_schedule: list[dict]` key
  for per-camera overrides; absent means "inherit user schedule."
- `app/server/monitor/api/notifications.py` — `GET/PUT /prefs` already
  round-trips `notification_prefs`. Extend the body shape to include
  `notification_schedule` (top-level user schedule) and
  `cameras[<id>].quiet_schedule` (per-camera override). Reuse the
  partial-update / null-clears-override pattern from #121.
- `app/server/monitor/services/audit.py:8` — emit a new
  `NOTIFICATION_QUIETED` audit event when the schedule suppresses
  delivery so an operator can verify the rule is actually doing what
  they think. Rate-limited (one per camera per quiet-window entry, see
  HAZ-245-3) so a busy hour can't flood the audit log.
- `app/server/monitor/services/alert_center_service.py` — **must not
  change**. The alert-center inbox is the persistent triage surface
  (ADR-0024) and quiet hours is explicitly delivery-side only; the
  inbox keeps receiving every event regardless of schedule. The
  acceptance criteria pin this invariant.
- `app/server/monitor/templates/settings.html` — schedule editor UI
  reusing the recording-schedule pattern (day chips + HH:MM time
  pickers + add/remove window). Already a shipped, tested UI affordance.
- `Settings.timezone` (`app/server/monitor/models.py:178`,
  default `"Europe/Dublin"`) — quiet-hour windows are evaluated in the
  *system* timezone, same as `recording_scheduler` evaluates recording
  windows. No per-user timezone in v1; rationale captured in OQ-3.

## User-Facing Behavior

### Primary path — configure a global quiet-hours schedule (per user)

1. User opens Settings → Notifications.
2. The page already shows the global "Notifications enabled" toggle and
   per-camera mute rules (from #121 / ADR-0027). A new "Quiet hours"
   card appears below.
3. The Quiet hours card shows existing schedule entries (a list, each
   row: weekday chips + start–end time + delete button) and an
   "Add quiet window" button.
4. User clicks "Add quiet window". An inline form appears with:
   - **Days** (weekday chips, multi-select): mon, tue, wed, thu, fri,
     sat, sun. At least one required.
   - **Start time** (HH:MM 24-hour): time picker.
   - **End time** (HH:MM 24-hour): time picker. May be earlier than
     start to express overnight windows (e.g., 22:00 → 06:00).
5. User submits. Server validates per the recording-schedule pattern:
   - Days set is non-empty and a subset of the seven valid keys.
   - Start and end parse as HH:MM.
   - Start ≠ end (a zero-length window is rejected with a clear error;
     "always quiet" is achieved by the existing notifications toggle,
     not by abusing the schedule).
6. On success the entry is appended to `User.notification_schedule`,
   the user is shown a one-line summary ("Mon–Fri 22:00 → 06:00"),
   and the audit log records `SETTINGS_CHANGED` with detail
   `notification_schedule_updated:added`. No plaintext schedule body
   in the audit detail (see SEC §audit-payload).

### Primary path — configure per-camera override

The existing per-camera notification card (#121) gains a "Quiet hours
override" sub-section:

- **Inherit user default** (radio, default): camera uses the user's
  global schedule. `Camera.notification_rule.quiet_schedule` absent
  or `null`.
- **Camera-specific schedule** (radio): the override block is shown,
  same editor as the user-level schedule. Saves into
  `Camera.notification_rule.quiet_schedule` (a list shape identical to
  the user schedule).
- **Always loud (override off)** (radio): explicit empty list
  `quiet_schedule: []`. The camera bypasses the user's quiet hours.
  Useful for a critical garage / driveway camera where the operator
  always wants the ping.

The radio-tri-state is what distinguishes "absent" (inherit) from
"empty list" (always loud) and avoids the ambiguity bug that would
otherwise force the implementer to invent a sentinel.

### Primary path — event delivery during quiet hours

When a motion event ends (or any other notification-eligible event
fires):

1. The alert-center service catalogues the event from its source —
   unchanged from today. The inbox row appears immediately.
2. The notification-policy `_eligible()` chain runs as today, with
   the new `_in_quiet_hours()` clause inserted *after* the
   `user.enabled` / per-camera-enabled checks and *before* the
   coalesce-window check:
   - 1. Peak score below threshold → drop (existing).
   - 2. Duration below per-camera min → drop (existing).
   - 3. `user.notification_prefs.enabled` is False → drop (existing).
   - 4. Per-camera enabled is False → drop (existing).
   - 5. **NEW:** `_in_quiet_hours(now=now, user=user, cam=cam)` is
        True → drop, emit one rate-limited
        `NOTIFICATION_QUIETED` audit event, do **not** stamp
        `cam.last_notification_at` (so the post-quiet event still
        gets a fresh coalesce window).
   - 6. Coalesce window still open → drop (existing).
   - 7. Already-delivered (last_seen pointer) → drop (existing).
   - 8. Otherwise → stamp `last_notification_at`, surface the
        notification.
3. The event remains in the alert-center inbox; the user sees it
   when they next open the dashboard.

### Primary path — manage and observe

- Edit / delete a window from either the user schedule or a per-camera
  override; same validation runs again.
- The Settings UI shows the *next* time delivery will resume (e.g.,
  "Currently quiet — next alert at 06:00") so operators can tell the
  feature is engaged. Computed client-side from the active window.
- Recent audit events (Settings → Audit) show
  `NOTIFICATION_QUIETED:<camera_id>` rows so an operator who suspects
  a missing alert can see "yes, that 03:00 motion was suppressed by
  quiet hours, here is the event id."

### Failure states

- **Schedule with overlapping windows** (e.g., Mon 22:00→06:00 plus
  Mon 23:00→07:00) → accepted; "in quiet hours" is the union, so the
  longer window wins. No de-duplication, no merge — operators can keep
  the entries that map to the way they think about their schedule.
- **Schedule with malformed entry** (corrupt JSON / missing fields)
  on disk after a hand-edit → that *entry* is skipped (return False
  for that row), other entries still apply. Same fail-open discipline
  as `recording_scheduler.now_in_window` — security-relevant alerts
  must not be silenced by a parsing bug.
- **Clock skew** (system time wrong by hours, e.g., NTP failed at
  boot) → quiet hours operates on `datetime.now()` in the system
  timezone, same as recording-schedule. Wrong clock = wrong silencing,
  documented residual risk (RISK-245-4). Mitigation is the existing
  NTP-failure surface (#216); we don't add a clock-trust check here
  because the recording scheduler doesn't either.
- **DST transition mid-window** → window evaluated against
  `datetime.now(tz)` in the configured `Settings.timezone`. Spring-
  forward jumps the clock past 02:30 → that minute is not in the
  window. Fall-back makes 01:30 fire twice → the window covers it
  twice; behaviourally identical to recording-schedule. Documented in
  AC-7.
- **Camera with override schedule but the camera is later deleted**
  → orphaned entry on the user record? No — quiet schedule lives on
  the *camera* record, so deleting the camera deletes the override.
  Per-camera-override-via-`notification_prefs.cameras[<id>]` (the
  per-user mute carryover) is already the pattern; quiet hours adds
  to that map, same lifecycle.
- **User has `notifications.enabled=False`** → quiet hours never runs
  (gate 3 short-circuits first). Adjusting quiet hours while globally
  disabled is allowed but has no effect until the global toggle goes
  back on; UI shows a hint.
- **Coalesced post-window digest** (mentioned in the issue Goal as
  motivation) — explicitly **out of scope** for v1 (see Non-Goals).
  The first slice silences cleanly; the digest format is a separate
  design pass once we have real operator feedback on what should be
  in it.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.

- AC-1: A user can save a global quiet-hours schedule with one or
  more weekday + start + end windows via `PUT /api/v1/notifications/prefs`.
  **[unit + contract]**
- AC-2: The schedule rejects empty `days`, malformed `HH:MM`, and
  start == end with a 400 error and a clear message.
  **[unit]**
- AC-3: Schedule windows where end < start are accepted and treated
  as overnight (same semantics as `now_in_window`).
  **[unit covering 22:00→06:00]**
- AC-4: A motion event whose `ended_at` falls inside any matching
  user-schedule window is suppressed from the polling pending-list
  (no entry returned for that user) but is **still present** in the
  alert-center inbox (`/api/v1/alerts`).
  **[integration: GET /pending = 0; GET /alerts shows the row]**
- AC-5: A motion event outside any matching window is delivered
  exactly as today (#121 behaviour preserved).
  **[integration]**
- AC-6: When `Camera.notification_rule.quiet_schedule = []` (explicit
  empty), the camera bypasses the user-level schedule and notifies
  even during quiet hours.
  **[integration]**
- AC-7: When `Camera.notification_rule.quiet_schedule` is a non-empty
  list, the camera's own schedule is used in place of the user's.
  **[integration]**
- AC-8: When `Camera.notification_rule.quiet_schedule` is absent or
  `null`, the camera inherits the user's schedule.
  **[integration]**
- AC-9: A suppressed event does **not** stamp
  `Camera.last_notification_at` (so the next post-quiet event gets a
  full coalesce window, not a residual one).
  **[unit asserting timestamp unchanged after a quiet-suppressed event]**
- AC-10: Suppressed delivery emits `NOTIFICATION_QUIETED` to the audit
  log, rate-limited to at most one event per camera per quiet-window
  entry per occurrence (so a busy hour does not flood logs).
  **[unit + integration with two motion events one minute apart]**
- AC-11: The `NOTIFICATION_QUIETED` audit detail includes
  `camera_id` and `motion_event_id` (or other event reference) but
  **not** the schedule body or anything that could leak when the
  operator is asleep beyond what the user could already see in the
  alert center.
  **[contract test on audit payload]**
- AC-12: Schedule evaluation handles spring-forward and fall-back DST
  transitions consistently with `recording_scheduler.now_in_window`
  (documented expected output for the transition minute).
  **[unit with frozen time]**
- AC-13: Schedule evaluation uses the configured `Settings.timezone`,
  not UTC, so an operator in `Europe/Dublin` who sets 22:00 → 06:00
  is silenced from 22:00 *local* time.
  **[unit with timezone-shifted clock]**
- AC-14: A schedule entry with one or more malformed fields (corrupt
  hand-edit on disk) does not raise; that entry is skipped and the
  remaining entries still apply.
  **[unit with fuzzed payload]**
- AC-15: A `null` value at `cameras[<id>].quiet_schedule` clears the
  per-camera override, restoring the inherit-user-default behaviour
  (matches the existing partial-update semantics from #121).
  **[unit + integration]**
- AC-16: Webhook delivery (#239 / spec 239-outbound-webhooks) for
  motion + system events MUST also honour the same quiet-hours gate
  (single source of truth in `_in_quiet_hours()`); the suppressed
  webhook attempt is logged as `NOTIFICATION_QUIETED` rather than
  `WEBHOOK_DELIVERY_*`.
  **[integration once #239 has merged; until then, leave a TODO with
  the integration point flagged in the implementer's PR]**
- AC-17: The Settings → Notifications UI lists existing windows, can
  add and delete entries, surfaces a "currently quiet — resumes at
  HH:MM" hint, and round-trips through `GET/PUT /prefs` without
  needing a page reload.
  **[browser-level smoke; pytest harness for the prefs round-trip]**
- AC-18: Default-empty schedule → behaviour is byte-identical to the
  pre-feature build (no spurious suppression on upgrade).
  **[regression test loading a pre-feature `users.json` and asserting
  every motion event surfaces]**

## Non-Goals

- **Per-event-class quiet-hours granularity.** v1 is a single schedule
  per user (and optionally per camera) that applies to *all* event
  classes routed through `_eligible()`. A v2 may carve "OTA outcomes
  always loud" off into its own toggle; not in scope here.
- **Phone-OS DND integration.** The server has no visibility into the
  operator's phone DND state and can't poll it; respecting phone DND
  is a per-OS concern handled on the device. The browser-push channel
  already lets the OS do its own muting on top of ours.
- **Holiday / vacation mode / one-off windows.** Recurring weekly
  windows only in v1. A "until next Tuesday" pause is a separate
  design pass.
- **Coalesced post-window digest** ("X events while you were away").
  Mentioned in the issue Goal as motivation, but the digest format,
  rendering, and delivery are non-trivial enough to warrant their own
  spec once we have operator feedback on what they want in it.
- **Severity-based bypass** ("critical events always notify even
  during quiet hours"). This *is* the operator's job to express via
  per-camera "always loud" overrides on the cameras that watch
  high-value zones. A v2 could add an event-class severity gate.
- **Migration / opt-in flip.** Defaults are empty schedules; nobody
  becomes silent on upgrade.
- **Schedule history / audit-of-edits beyond `SETTINGS_CHANGED`.** The
  existing audit-on-settings-change is enough; we don't ship a per-
  schedule diff trail.
- **Cross-user schedule sharing / role-templated schedules.** Each
  user maintains their own.

## Module / File Impact List

**New code:**

- `app/server/monitor/services/notification_schedule.py` — small
  pure-function module that hosts:
  - `in_quiet_hours(*, now: datetime, user_schedule: list[dict],
    camera_override: list[dict] | None, tz: str) -> bool` — the
    decision function. `camera_override = None` means inherit user;
    `camera_override = []` means "always loud"; non-empty list
    overrides.
  - `validate_schedule(schedule: list[dict]) -> str` — returns ""
    on success or a 400-shaped error string. Same range / type rules
    as `_validate_int`-style helpers in `notification_policy_service`.
  - Reuses `recording_scheduler.now_in_window` (or a lifted, shared
    `time_window` helper module — see OQ-1).
- `app/server/tests/unit/test_notification_schedule.py` — covers
  windows, overnight crossings, DST, validation rejections,
  malformed-entry skip, override precedence.
- `app/server/tests/integration/test_quiet_hours.py` — end-to-end:
  motion event during quiet hours → not in `/pending`, present in
  `/alerts`, `NOTIFICATION_QUIETED` in audit, no
  `last_notification_at` stamp.
- `app/server/tests/integration/test_quiet_hours_prefs_api.py` —
  `GET/PUT /prefs` round-trip including per-camera override
  tri-state (inherit / override / always-loud).

**Modified code:**

- `app/server/monitor/models.py`:
  - `User.notification_schedule: list[dict] = field(default_factory=list)`
    — list of `{"days": ["mon", ...], "start": "HH:MM", "end":
    "HH:MM"}` entries. Default empty (no quiet hours).
  - `Camera.notification_rule` default factory unchanged; per-camera
    override lives at `notification_rule["quiet_schedule"]` when
    present (`None` / absent = inherit, `[]` = always loud,
    non-empty list = override).
- `app/server/monitor/services/notification_policy_service.py`:
  - Inject `notification_schedule.in_quiet_hours` (or import it)
    into `_eligible()`. New gate inserted as gate 5 above. Service
    also gains a `settings` reader (`store.get_settings().timezone`
    or equivalent) so the timezone is loaded once per call.
  - `update_prefs()` extended to accept a top-level
    `notification_schedule` key and per-camera
    `cameras[<id>].quiet_schedule` keys, both validated through the
    new helper.
  - `get_prefs()` returns the schedule alongside the existing fields.
- `app/server/monitor/api/notifications.py`:
  - No new routes. `/prefs` GET response shape extended with the new
    fields; PUT body shape extended likewise. `OPEN QUESTION`-tagged
    contract change must be captured in the API contract test.
- `app/server/monitor/services/audit.py`:
  - Add `NOTIFICATION_QUIETED` to the audit event docstring catalogue.
    No code change in `AuditLogger` itself — it's already
    string-based.
- `app/server/monitor/templates/settings.html` /
  `app/server/monitor/static/js/settings.js`:
  - New "Quiet hours" card under the Notifications section.
  - Schedule editor (day chips + HH:MM pickers + add/remove). Reuse
    or factor out the existing recording-schedule editor pattern;
    Implementer to choose between "extract a shared component" and
    "duplicate styles only" based on how big the JS factor-out turns
    out to be.
  - "Currently quiet — resumes at HH:MM" hint computed client-side.
- `docs/history/adr/0027-rich-motion-notifications.md`:
  - Add a "Follow-up: quiet hours" subsection (one paragraph)
    pointing at this spec, so the ADR's decision tree narrative stays
    coherent.
- `docs/traceability/traceability-matrix.md`:
  - New rows for the IDs listed in the §Traceability section below.
- `app/server/tests/unit/test_notification_policy.py`:
  - Existing tests still pass (regression). New cases: gate-5 quiet
    suppression; gate-5 emits the audit event; gate-5 leaves
    `last_notification_at` untouched.

**Out of scope of this spec (touched only if absolutely necessary):**

- `app/server/monitor/services/recording_scheduler.py`: only touched
  if Implementer chooses OQ-1 option B (lift `DAY_INDEX` /
  `now_in_window` into a shared `time_window.py`). If chosen, the
  scheduler imports from there; behaviour unchanged.
- `app/server/monitor/services/webhook_delivery_service.py` (per
  spec 239-outbound-webhooks): one call site in the future
  webhook-delivery `enqueue` path needs a quiet-hours check (AC-16).
  If #239 has not yet merged when this lands, add a TODO with the
  call-site location pinned.

**Dependencies:**

- No new Python dependencies. Stdlib `datetime` + the existing
  `zoneinfo` / `tzdata` shipped with the Yocto image (already used
  by recording_scheduler and time-sync) is sufficient.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md`:

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| API contract | extend the existing `/api/v1/notifications/prefs` contract test for the new fields and tri-state semantics |
| Frontend / templates | browser-level check on `/settings` Notifications → Quiet hours card |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Hardware behavior | deploy + `scripts/smoke-test.sh` row covering quiet-hour suppression on real hardware |

Smoke-test additions (Implementer to wire concretely):

- "Operator sets a 22:00–06:00 quiet window; a 03:00 motion event
  appears in the alert center but no browser notification fires."
- "Operator removes the quiet window; the next motion event fires a
  browser notification as today."
- "Per-camera override marks the driveway camera as 'always loud'
  during user-level quiet hours; the operator receives the
  notification."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-245-1 | Operator sets a too-wide quiet window (e.g., 24/7) by mistake → security event genuinely missed because no notification fires AND operator never opens the dashboard. | Major (security) | Medium | RC-245-1: zero-length windows rejected by validation; UI shows the active "currently quiet" hint so the operator can see when their window engaged unexpectedly; alert-center inbox always receives the event so a periodic check still catches it. Documented in user-facing docs. |
| HAZ-245-2 | Quiet schedule silently masks a real-time outage (e.g., a camera-offline alert during the night that the operator could have acted on). | Moderate (operational) | Medium | RC-245-2: same — alert-center inbox is unaffected; per-camera override lets the operator mark mission-critical cameras "always loud"; offline-alert event still hits the inbox immediately; documented in user-facing docs. |
| HAZ-245-3 | Audit log flooded with `NOTIFICATION_QUIETED` events during a busy hour (e.g., 100 motion events suppressed in a row) → audit log rotation fires prematurely, displacing other security-relevant audit lines. | Minor (operational) | Medium | RC-245-3: rate-limit `NOTIFICATION_QUIETED` to at most one event per camera per active window per occurrence (e.g., one per 60 s per camera); coalesce at the call site, same idea as ADR-0027's coalesce window. AC-10 enforces. |
| HAZ-245-4 | System clock wrong by hours (NTP failed at boot, RTC drift) → quiet hours fires at the wrong real-world time; operator either notified during sleep or not silenced when they expected. | Minor (operational, security depends on direction) | Low | RC-245-4: documented residual; mitigation is the existing NTP-failure surface (#216) and the time-sync settings UI (ADR-0019). Quiet hours does not add its own clock-trust check; the recording scheduler has the same residual and we keep them consistent. |
| HAZ-245-5 | Implementer accidentally stamps `Camera.last_notification_at` for a quiet-suppressed event → the post-quiet first event is silently coalesced because the residual stamp is still inside the coalesce window. Operator perceives the feature as "broken first event after quiet ends." | Moderate (UX) | Medium | RC-245-5: AC-9 explicitly asserts no stamp on suppression; unit test pins the invariant; spec calls it out in the decision-tree §. |
| HAZ-245-6 | Per-camera override tri-state ambiguity (absent vs `null` vs `[]`) leads to "always loud" being read as "inherit" → operator's high-priority camera falls silent. | Moderate (security) | Medium | RC-245-6: explicit field semantics in this spec; partial-update unit tests cover all three cases; UI is a radio (not a checkbox) so it can't be left in an undefined state. |
| HAZ-245-7 | DST transition silently shifts the start/end of a window by one hour → operator notified at unexpected times the day after the change. | Minor (UX) | Medium | RC-245-7: AC-12 pins the documented behaviour against `recording_scheduler.now_in_window`; both surfaces share the same DST handling so the operator's mental model stays coherent across recording and notification scheduling. |

Reference `docs/risk/` for the existing architecture risk register; this
spec adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs):

- **No new external surface.** Endpoints are existing
  session-authenticated routes (`/api/v1/notifications/prefs`); body
  shape grows but the auth + CSRF posture is unchanged.
- **No new persisted secret material.** Schedule body is plain
  configuration data — weekday + HH:MM strings.
- **Audit completeness vs information disclosure (SEC-245-A):** the
  `NOTIFICATION_QUIETED` event tells the operator that an alert was
  suppressed, but the audit detail must be carefully scoped so a
  reader of the log (e.g., another admin) does not learn the
  *operator's quiet-hour schedule* from the audit record alone — the
  schedule is a habit-disclosure leak (when the operator is asleep /
  away). Detail field includes the camera id and event reference;
  it does **not** include the schedule body, the active window, or
  the resume time.
- **Sensitive paths touched:** `**/auth/**` — no direct change.
  `**/secrets/**` — no. Pairing / OTA / certificate flows — no. The
  spec does not touch sensitive paths beyond reading the existing
  `Settings.timezone`.
- **Defence-in-depth:** the alert-center inbox is the source-of-truth
  triage surface (ADR-0024); quiet hours is delivery-side only. A
  defender who is woken up "by hand" (i.e., they happened to check the
  dashboard) is never blinded by the schedule. The acceptance criteria
  pin this invariant on every event class.
- **Default-deny preserved:** default schedule is empty, default
  notifications are off (per #121); enabling quiet hours requires an
  authenticated user explicitly making the change, audited as a
  `SETTINGS_CHANGED` event.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-245` — User need: "I want to use my home-monitor at full
  fidelity during the day without being woken up by motion alerts at
  night."
- `SYS-245` — System requirement: "The system shall support
  recurring per-weekday time windows during which active notification
  delivery is suppressed for the configured user, while preserving the
  alert-center inbox as the persistent triage surface."
- `SWR-245-A` … `SWR-245-E` — Software requirements (one per
  functional area: schedule storage, schedule validation, decision-tree
  integration, audit emission, per-camera override semantics).
- `SWA-245` — Software architecture item: "Pure-function quiet-hours
  decision in `notification_schedule.in_quiet_hours`; reused by
  `notification_policy_service._eligible()` and (when merged) by
  webhook-delivery service. Schedule field on `User`; per-camera
  override field on `Camera.notification_rule`."
- `HAZ-245-1` … `HAZ-245-7` — listed above.
- `RISK-245-1` … `RISK-245-7` — one per hazard.
- `RC-245-1` … `RC-245-7` — one per risk control.
- `SEC-245-A` (audit information disclosure scope), `SEC-245-B`
  (no-stamp invariant on suppression preserves coalesce semantics),
  `SEC-245-C` (defence-in-depth: inbox unaffected).
- `THREAT-245-1` (operator habit disclosure via audit log),
  `THREAT-245-2` (silent miss of a real-time outage during a too-wide
  schedule).
- `SC-245-1` … `SC-245-N` — controls mapping to the threats above.
- `TC-245-AC-1` … `TC-245-AC-18` — one test case per acceptance
  criterion above.

Code-annotation examples (Implementer adds these):

```python
# REQ: SWR-245-A, SWR-245-C; RISK: RISK-245-5; TEST: TC-245-AC-9
def _in_quiet_hours(self, *, now, user, cam) -> bool:
    ...
```

## Deployment Impact

- Yocto rebuild needed: **no** (no new external dependencies; tzdata
  already shipped).
- OTA path: standard server image OTA. Migration on first boot of the
  new image: existing `User` records load with the dataclass default
  (`notification_schedule = []`) and existing `Camera` records load
  with `notification_rule` unchanged (no `quiet_schedule` key) — both
  paths give the existing "no quiet hours" behaviour.
- Hardware verification: yes — required. Set a quiet window that
  covers the smoke-test minute, walk in front of a paired camera,
  verify the polling client surfaces nothing and the alert-center
  inbox row appears. Add to `scripts/smoke-test.sh` per the smoke
  additions above.
- Default state on upgrade: empty schedules everywhere; no operator
  impact on upgrade day.

## Open Questions

(None of these are blocking; design proceeds. Implementer captures
answers in PR description.)

- OQ-1: `now_in_window` and `DAY_INDEX` currently live in
  `recording_scheduler.py`. Lift them into a shared
  `app/server/monitor/services/time_window.py` (cleanest), or import
  the public symbols from `recording_scheduler` into the new
  `notification_schedule` module (tighter, less churn)?
  **Recommendation:** lift to a shared module. The cost is a
  one-import change in `recording_scheduler`; the benefit is a
  single source of truth for window evaluation that future scheduling
  features can reuse.
- OQ-2: Where exactly does the `_in_quiet_hours()` gate sit in the
  decision tree — before or after the per-camera-enabled gate? Spec
  proposes "after enabled, before coalesce" so a hard-disabled camera
  short-circuits without consulting the schedule (cheaper, and means
  the audit emission only happens for cameras the user otherwise
  *would* have been notified about).
  **Recommendation:** after enabled, before coalesce, as written.
- OQ-3: Per-user timezone vs system timezone? Spec uses
  `Settings.timezone` (system-wide). For a single-household appliance
  this is correct; for a deployment with users in multiple timezones
  it would be wrong. v1 assumes single-household; per-user TZ is a
  v2 concern.
  **Recommendation:** system tz for v1, document the limit in
  user-facing docs.
- OQ-4: Should a `NOTIFICATION_QUIETED` event for a *camera-offline*
  suppression be specially flagged (so an admin scanning the audit log
  can distinguish "we missed a hardware fault" from "we missed a
  routine motion ping")? Spec keeps a single audit event-name with
  the suppressed event class in the detail.
  **Recommendation:** single event name; detail carries the class.
  Re-evaluate after the first 30 days of operator feedback.
- OQ-5: Should we offer a "quiet hours preview" in the UI — e.g., a
  little 24-hour timeline showing today's silenced bands across all
  cameras? Spec only requires the "currently quiet — resumes at
  HH:MM" hint. The timeline is a clear UX win but adds frontend
  scope.
  **Recommendation:** ship the hint in v1; the timeline is a v1.x
  polish.
- OQ-6: How does the per-camera override surface to the user when
  the user-level schedule changes? Today, if the user adds a new
  window, every camera with `quiet_schedule = None` (inherit)
  immediately picks it up — correct. A camera with
  `quiet_schedule = [...]` (override) does *not* — also correct.
  But: should the UI surface a "this camera is on its own schedule"
  indicator next to such cameras so the operator notices?
  **Recommendation:** yes — add a small badge to the per-camera
  notification card. Cheap, prevents surprise.

## Implementation Guardrails

- Preserve the service-layer pattern (ADR-0003): the new logic lives
  in `notification_schedule` (pure functions) and gets called by
  `notification_policy_service`; routes stay thin.
- Preserve the modular monolith (ADR-0006): no new daemon, no new
  threads. Quiet hours is one extra synchronous gate in the existing
  `_eligible()` path.
- `/data` is the only place mutable runtime state lives (the
  schedules ride on the existing `users.json` and `cameras.json`).
- Schedule evaluation is **side-effect free** — `_in_quiet_hours()`
  must not stamp `last_notification_at` and must not write to disk.
  The audit emission is the only side effect, and it is rate-limited.
- The alert-center inbox invariant is non-negotiable: every event the
  alert center accepts today must continue to land in the inbox after
  this feature ships, regardless of any quiet-hours schedule. AC-4
  pins it; the test harness must assert it.
- Tests + docs ship in the same PR as code, per
  `engineering-standards`.
- No backwards-compatibility hacks. Default empty schedule is the
  upgrade path; no migration script, no `// removed` markers, no
  schema version bump.

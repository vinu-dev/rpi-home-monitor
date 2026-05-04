# Feature Spec: surface clock-drift and NTP-sync health on dashboard

Tracking issue: #250. Branch: `feature/250-clock-drift-ntp-health`.

## Title

Operator-visible time integrity: a `time_health` block on the system summary,
a non-fatal warning chip on the dashboard when the server's clock is
desynchronized or any camera's clock has drifted past tolerance vs. the
server, and a one-click "resync now" action that restarts
`systemd-timesyncd` on the affected device. Server-only observation surface
on top of the existing ADR-0019 topology — no NTP-config redesign.

## Goal

Today, ADR-0019 makes the server the LAN's authoritative clock and points
cameras at it via mDNS. This works until it silently doesn't: a DNS hiccup,
a `timesyncd` exponential back-off after repeated failures, an upstream NTP
pool unreachable, or the server itself drifting because its upstreams are
unreachable. None of those are visible to the operator until clip
filenames look wrong after the fact, motion-event ordering on the timeline
goes weird, or the audit-log "what happened when" reasoning breaks.

Concretely, after this change:

- The dashboard's existing status strip (Tier-1 of ADR-0018) goes amber and
  carries the sentence "Camera living-room clock drifted +4.2s — resync"
  as soon as any camera's beat-derived drift crosses the amber threshold,
  and goes red on the red threshold or when the server's own NTP is
  unsynchronized.
- A new `time_health` block on `GET /api/v1/system/summary` carries the
  per-component data (server NTP state + per-camera drift) so the
  status-strip JS can compose the chip and a Settings → Time → Time Health
  card can render the same data in detail.
- A `POST /api/v1/system/time/resync` action on the existing system blueprint
  restarts `systemd-timesyncd` on the server (always allowed) or — for a
  per-camera resync — pushes a one-shot `time_resync` flag through the
  existing camera-config / pending-config channel so the camera restarts
  its own `timesyncd` on its next heartbeat.
- An audit-log row `TIME_RESYNC_REQUESTED` is written for every resync,
  capturing operator, IP, target (`server` or camera id).

This is an **observability** feature, not a configuration one. It does not
change ADR-0019's topology, does not introduce `chrony`, does not move
cameras off mDNS-resolved server NTP, and does not force-set time on any
device. If the new amber chip lights up, an operator now has a reason and
a path to act; today they have neither.

## Context

Existing code this feature must build on, not replace:

- `app/server/monitor/services/settings_service.py:212` —
  `SettingsService.get_time_status()` already shells out to
  `timedatectl show -p Timezone -p NTP -p NTPSynchronized -p TimeUSec
  -p RTCTimeUSec` and returns a dict (`timezone`, `ntp_mode`,
  `ntp_active`, `ntp_synchronized`, `system_time`, `rtc_time`). The new
  `TimeHealthService` reuses this method verbatim — we do not add a second
  `timedatectl` shell.
- `app/server/monitor/services/settings_service.py:185,198` —
  `_apply_timezone` and `_apply_ntp_mode` are the existing patterns for
  invoking `timedatectl set-…` via `subprocess.run`. The "resync now"
  action follows the same pattern (`systemctl restart
  systemd-timesyncd`), with `capture_output=True, text=True, timeout=10,
  check=False`.
- `app/server/monitor/services/system_summary_service.py:113` —
  `SystemSummaryService.compute_summary()` is the **single** dashboard
  aggregator (ADR-0018). It already produces `state`, `summary`,
  `deep_link`, `details` and locks thresholds at the top of the file. The
  new time_health signal slots in here as a peer of cameras, storage,
  recorder host, recent_errors. Thresholds (drift in seconds) are added
  to the locked-constants block.
- `app/server/monitor/services/system_summary_service.py:30-39` — the
  threshold pattern (e.g. `CAMERA_OFFLINE_AMBER_SECONDS = 60`,
  `CAMERA_OFFLINE_RED_SECONDS = 60 * 60`) is the precedent for how we
  declare amber/red drift thresholds: as module-level constants,
  ADR-bound.
- `app/server/monitor/services/system_summary_service.py:97` —
  `_worst(*states)` is the existing red>amber>green merger. The new
  `time_state` plugs into the existing `_worst()` call at the top of
  `compute_summary()`.
- `app/server/monitor/api/system.py:86` — `GET /system/summary` is the
  poller endpoint; the dashboard hits it every 10s
  (`dashboard.html:1354`). No new poll path is added.
- `app/server/monitor/api/system.py` — also where the new
  `POST /system/time/resync` action lives. Same blueprint, same auth
  pattern as the existing `/system/*` routes (admin-only via
  `@admin_required`).
- `app/server/monitor/services/camera_service.py:547` —
  `accept_heartbeat()` is the only place a camera reports liveness. The
  beat already carries `timestamp` (Unix epoch seconds) per
  `app/camera/camera_streamer/heartbeat.py:222`; today the server
  overwrites `camera.last_seen` with the **server's** clock and discards
  the beat's `timestamp`. We change one line: persist the camera-supplied
  beat timestamp into a new `Camera.last_beat_camera_ts` field alongside
  the existing server-authoritative `last_seen`. **Drift = `last_seen` -
  `last_beat_camera_ts`** computed at the next summary tick.
- `app/camera/camera_streamer/heartbeat.py:33,222` —
  `HEARTBEAT_INTERVAL = 15s`; payload includes `"timestamp":
  int(time.time())`. The resolution (1s) is already enough for the amber
  threshold (≥ 2s) — no schema change to the heartbeat.
- `app/server/monitor/models.py:221-226` — `Settings.timezone` and
  `Settings.ntp_mode` are the only existing time-related settings.
  No schema migration is needed: the per-camera drift is derived
  in-memory at summary time. Persisting the most recent
  camera-supplied beat timestamp is a single new field on the
  `Camera` model in the same file.
- `app/server/monitor/services/camera_service.py` (existing
  `pending_config` channel returned in the heartbeat response, line
  ~580) — already used to carry "apply this config on your next tick"
  payloads to a camera. The per-camera "resync now" action piggy-backs on
  this same channel: the server queues a one-shot `time_resync: true`
  flag; the next heartbeat response carries it; the camera invokes
  `systemctl restart systemd-timesyncd` and acks. No new transport.
- `app/server/monitor/services/audit.py:56` — `log_event(event, user, ip,
  detail)` is the audit-log writer. New event type:
  `TIME_RESYNC_REQUESTED`.
- `app/server/monitor/templates/dashboard.html:15-22` — the status-strip
  span template. No template change needed because the strip is purely
  data-driven from `summary.state` + `summary.summary` + `summary.deep_link`.
- `app/server/monitor/templates/settings.html` (Date & Time card from
  ADR-0019) — extended with a "Time Health" sub-section showing
  `ntp_active`, `ntp_synchronized`, last-sync-time (from
  `timedatectl timesync-status`) and per-camera drift table with a
  "Resync" button per row.
- `app/server/monitor/api/__init__.py` — no new blueprint;
  the time-health endpoint is part of the existing `system_bp`. The
  `time_health` block goes into the existing `system_summary` payload.
- ADRs anchoring the choices: ADR-0003 (service-layer — new
  `TimeHealthService` is a peer of `SystemSummaryService`), ADR-0006
  (modular monolith — new service runs in-process, no new daemon),
  ADR-0018 (Tier-1 status strip is the only place a dashboard chip can
  live; thresholds locked here), ADR-0019 (time-sync topology
  unchanged).

## User-Facing Behavior

### Primary path — server clock healthy, all cameras in sync

1. Operator loads the dashboard. JS polls `/api/v1/system/summary` every
   10s.
2. `time_health.state == "green"`, `summary.state == "green"`, the
   status-strip stays green and shows "All systems normal" (or whatever
   the existing green-state sentence is).
3. The Tier-1 status strip is silent; the operator sees nothing about
   time. (Per ADR-0018: green means quiet.)

### Primary path — single camera drifts past amber threshold

1. Camera living-room's clock drifts +4.2s (e.g., its `timesyncd`
   back-off temporarily exceeded its sync window).
2. On the camera's next heartbeat, the server stores its
   `last_beat_camera_ts` and the next summary tick computes `drift =
   server_now - last_beat_camera_ts - approx_one_way_latency` and finds
   `|drift| >= DRIFT_AMBER_SECONDS` (default 2s).
3. `time_health.state == "amber"`, `time_health.worst_camera == "living-
   room"`, `time_health.worst_drift_seconds == 4.2`.
4. `compute_summary` merges this into the global state via `_worst()` →
   `summary.state == "amber"`.
5. `summary.summary == "Camera living-room clock drifted +4.2s —
   resync"`. `summary.deep_link == "/settings#time-health"`.
6. The status strip turns amber and shows that sentence; the dashboard's
   tooltip on the chip explains the impact (clip filenames, audit
   timestamps, motion-event ordering).
7. Operator clicks the chip → routed to Settings → Date & Time → Time
   Health card. The card lists the camera, its drift, and a "Resync"
   button.
8. Click "Resync" → `POST /api/v1/system/time/resync {"target":
   "<camera-id>"}`. Server queues a `time_resync: true` flag in the
   camera's `pending_config`.
9. Camera's next heartbeat (≤ 15s) fetches it, runs
   `systemctl restart systemd-timesyncd`, clears the flag in its
   subsequent heartbeat ack.
10. Within 1–2 heartbeats after the restart settles (~30–60s), the
    camera's drift returns under the amber threshold; the chip clears.

### Primary path — server NTP unsynchronized

1. Server `timesyncd` cannot reach upstream pools (e.g., LAN gateway
   blocks UDP/123 outbound). After its back-off, `NTPSynchronized=no`.
2. `TimeHealthService` polls `get_time_status()`; sees
   `ntp_active=true, ntp_synchronized=false`; sets
   `time_health.state == "amber"`, `time_health.server_synchronized ==
   false`.
3. Strip turns amber: "Server time not synchronized — resync".
4. Operator clicks → Settings → Time Health. "Resync" button on the
   server row. Click → `POST /system/time/resync {"target": "server"}`
   → `systemctl restart systemd-timesyncd` on the server, audit row
   written.
5. Within ~30s `NTPSynchronized=yes`, chip clears.

### Primary path — server clock significantly drifted

1. The server itself reports `NTPSynchronized=yes` but its monotonic
   self-check (the loopback "compare timedatectl TimeUSec to a freshly
   re-read TimeUSec") detects no anomaly. **We do not implement a
   server-self-drift check** — there is no second authoritative source on
   the LAN to compare against. The server's claim of "I am synchronized"
   is taken at face value. (See OQ-2.)
2. If `NTPSynchronized=no` for `>= SERVER_RED_SECONDS` (default 30
   minutes), `time_health.state == "red"`. Strip turns red: "Server time
   has not synchronized in 30+ minutes — check upstream NTP".

### Primary path — camera offline

1. A camera goes offline (no beats for `> CAMERA_OFFLINE_AMBER_SECONDS`).
   Its drift becomes stale.
2. Drift for an offline camera is **not reported** as time-health
   amber/red. The cameras tile already reports the camera as offline;
   surfacing both signals would double-count (per ADR-0018: one
   signal per condition).
3. When the camera comes back, the next heartbeat refreshes
   `last_beat_camera_ts` and the drift signal recomputes from fresh data.

### Failure states (must be designed, not just unit-tested)

- **`timedatectl` binary missing** (e.g., dev shell on a non-systemd
  host) → `get_time_status()` already returns the default dict with
  `ntp_active=false, ntp_synchronized=false`. The new service treats
  this as `time_health.state == "unknown"` and **does not** flip the
  strip amber/red. Logs once at INFO at startup. The dashboard renders
  "Time health unavailable in this environment" in the Settings card
  only.
- **Camera supplies a missing or non-numeric `timestamp`** in its
  heartbeat → server treats the beat as having no usable
  `last_beat_camera_ts`; drift for that camera reports `null`. The
  cameras-tile signal still uses server-side `last_seen`. The camera is
  excluded from drift computation (no chip, no false-amber).
- **Camera supplies a `timestamp` from the future > 1 year** (clock
  badly broken, e.g., RTC battery dead and no NTP) → drift is reported
  but capped at `MAX_DRIFT_SECONDS_REPORTED` (3600). State is `red` for
  that camera. The chip shows "Camera kitchen clock drifted +3600s —
  resync".
- **`systemctl restart systemd-timesyncd` fails on the server**
  (subprocess returns nonzero, or times out at 10s) → API returns 500
  with the stderr trimmed; no audit row written; the existing UI
  toaster surfaces the error sentence. Operator can retry.
- **Per-camera resync queued but the camera never picks it up** (camera
  offline, or pending_config storage write failure) → the flag stays
  queued; on the next heartbeat the camera consumes it. If the camera is
  offline for > 1 hour, the queued flag is **kept** (it is one bit;
  carrying it indefinitely costs nothing) until the camera reconnects.
  This matches existing `pending_config` semantics.
- **Operator double-clicks "Resync" on the same camera** → idempotent.
  The pending_config stores `time_resync: true` once; a second request
  while the flag is unconsumed returns 200 with body
  `"already queued"`. No second audit row in the same 60s window.
- **Drift threshold flipping at the boundary** (drift oscillates around
  exactly 2.0s) → `_TimeHealthService` applies a 0.5s hysteresis: once
  amber, must come below `DRIFT_AMBER_SECONDS - 0.5` to clear. Same for
  red→amber.
- **Server `last_seen` < camera `last_beat_camera_ts`** (server clock is
  behind the camera) → drift is computed as `last_seen -
  last_beat_camera_ts` and reported with sign. `+4.2s` = camera ahead;
  `-4.2s` = camera behind. UI shows the sign explicitly.
- **Heartbeat in flight at exactly the summary-poll boundary** → the
  drift signal uses whatever beat the store has at compute time. No
  locking. A drift report can lag the actual state by up to one
  `HEARTBEAT_INTERVAL` (15s); this is well below the amber threshold's
  2s drift granularity to user-visible state and is acceptable.
- **Network one-way latency on the LAN** (camera→server) is **not**
  measured. Sub-second LAN latency is below the 2s amber threshold by
  more than an order of magnitude. We do not adjust drift by an
  estimated latency. Documented in spec; if field data shows false
  positives on slow LANs, OQ-3 reopens it.

## Acceptance Criteria

Each bullet is testable; verification mechanism is in brackets.

- AC-1: A new module `app/server/monitor/services/time_health_service.py`
  exposes `TimeHealthService` with `__init__(*, store, settings_service)`
  and `compute_health() -> dict`. The result has the shape:
  `{"state": "green"|"amber"|"red"|"unknown", "server": {"ntp_active":
  bool, "ntp_synchronized": bool, "unsynced_seconds": int|None}, "cameras":
  [{"id": str, "name": str, "drift_seconds": float|None, "state":
  "green"|"amber"|"red"|"unknown"}], "worst_camera": str|None,
  "worst_drift_seconds": float|None}`.
  **[unit]**
- AC-2: `compute_health()` reuses
  `SettingsService.get_time_status()` for the server section — does not
  shell out to `timedatectl` itself.
  **[unit, with `get_time_status` mocked]**
- AC-3: Drift is computed as
  `(parse(camera.last_seen) - parse(camera.last_beat_camera_ts))
  .total_seconds()` and reported with sign. When either timestamp is
  missing or unparseable, `drift_seconds` is `None` and the camera's
  `state` is `"unknown"`.
  **[unit]**
- AC-4: A camera's `state` is `"amber"` when
  `DRIFT_AMBER_SECONDS <= |drift| < DRIFT_RED_SECONDS`, `"red"` when
  `|drift| >= DRIFT_RED_SECONDS`, `"green"` when
  `|drift| < DRIFT_AMBER_SECONDS`. Defaults: amber 2s, red 30s.
  Hysteresis: once flipped to amber/red, must come below
  `(threshold - HYSTERESIS_SECONDS)` (default 0.5s) to step back.
  **[unit]**
- AC-5: Offline cameras (`status == "offline"`) are excluded from
  `cameras` list — their `state` is `"unknown"` and `drift_seconds` is
  `None` — so the time-health signal does not double-count cameras-tile
  offlineness.
  **[unit]**
- AC-6: Server NTP `state` is `"red"` when `ntp_active=true` and
  `ntp_synchronized=false` for `>= SERVER_RED_SECONDS` (default
  1800s = 30min); `"amber"` when `ntp_synchronized=false` for less than
  that; `"green"` when `ntp_synchronized=true`; `"unknown"` when
  `get_time_status()` returns its no-timedatectl default.
  **[unit with mocked clock]**
- AC-7: `TimeHealthService.compute_health()` never raises. Any
  sub-signal failure degrades to `state="unknown"` for that signal,
  logged at WARNING.
  **[unit]**
- AC-8: `Camera.last_beat_camera_ts: str = ""` is added to
  `app/server/monitor/models.py` next to `last_seen`. ISO-8601 UTC with
  `Z` suffix, same format as `last_seen`.
  **[unit]**
- AC-9: `CameraService.accept_heartbeat()` parses the heartbeat's
  `timestamp` field as Unix epoch seconds and persists the converted ISO
  string into `camera.last_beat_camera_ts` before the existing save.
  Missing / non-numeric / negative values leave `last_beat_camera_ts`
  unchanged.
  **[unit]**
- AC-10: `SystemSummaryService.__init__` accepts a new `time_health`
  service param (service-layer pattern) and `compute_summary()` calls
  `time_health.compute_health()`, merges its `state` into the
  `_worst(...)` call, and embeds the full health dict under
  `details.time_health`.
  **[unit]**
- AC-11: When `time_health.state` dominates the merge,
  `summary.summary` reads "Camera <name> clock drifted +<n>s — resync"
  (single worst camera) or "Server time not synchronized — resync"
  (server). `summary.deep_link` is `"/settings#time-health"`.
  **[unit]**
- AC-12: Module-level constants `DRIFT_AMBER_SECONDS = 2.0`,
  `DRIFT_RED_SECONDS = 30.0`, `SERVER_RED_SECONDS = 1800`,
  `MAX_DRIFT_SECONDS_REPORTED = 3600`, `HYSTERESIS_SECONDS = 0.5` are
  declared in `time_health_service.py` with the same "LOCKED — see
  ADR-0018" comment header as `system_summary_service.py`.
  **[unit]**
- AC-13: `POST /api/v1/system/time/resync` is registered on the existing
  `system_bp`, requires `@admin_required`, accepts JSON
  `{"target": "server" | "<camera-id>"}`, and:
  - on `"server"` runs `subprocess.run(["systemctl", "restart",
    "systemd-timesyncd"], capture_output=True, text=True, timeout=10,
    check=False)` — same shape as existing time-related subprocess
    invocations — and writes audit `TIME_RESYNC_REQUESTED` with detail
    `"target=server"`.
  - on a camera id, queues `{"time_resync": true}` into that camera's
    pending_config, writes audit `TIME_RESYNC_REQUESTED` with detail
    `"target=<camera-id>"`, returns 200.
  **[unit + integration]**
- AC-14: The resync endpoint validates `target`: missing → 400; unknown
  camera id → 404; subprocess nonzero on server target → 500 with
  trimmed stderr; pending_config write failure → 500.
  **[unit]**
- AC-15: `POST /system/time/resync` is **not** rate-limited beyond
  request idempotency: a second request for the same target while a
  flag is unconsumed (camera) or a previous restart is within 60s
  (server) returns 200 with body `"already queued"` and writes no
  duplicate audit row.
  **[unit]**
- AC-16: `app/camera/camera_streamer/heartbeat.py:_apply_pending_config`
  consumes a `time_resync: true` flag by invoking
  `subprocess.run(["systemctl", "restart", "systemd-timesyncd"],
  timeout=10, check=False)` and clears the flag from the local
  pending_config copy. Failure logs at WARNING; flag is cleared anyway
  (the operator can retry).
  **[unit]**
- AC-17: `Settings → Date & Time` page renders a new "Time Health"
  card (in `templates/settings.html`) showing: server NTP active +
  synchronized state, last sync time (best-effort, from
  `timedatectl timesync-status` if available, else "—"), and a
  per-camera table of `name | drift | state badge | Resync button`.
  Cameras whose `state == "unknown"` show "—" for drift and the button
  disabled.
  **[unit + manual smoke]**
- AC-18: The Time Health card hits a new `GET /api/v1/system/time/health`
  endpoint that returns the `TimeHealthService.compute_health()` payload
  unmodified. Endpoint is `@admin_required`. (The dashboard status-strip
  reuses the embedded copy in `/system/summary`; this endpoint is for
  the Settings page detail view and to allow finer-grained polling.)
  **[unit + contract]**
- AC-19: `time_health` block is added to the OpenAPI spec
  (`openapi/server.yaml`) under both `/system/summary` (as a sub-object
  of `details`) and the new `/system/time/health` and
  `/system/time/resync` endpoints.
  **[contract test, openapi-validator]**
- AC-20: Audit-log event type `TIME_RESYNC_REQUESTED` is documented in
  `docs/cybersecurity/threat-model.md` (or wherever audit events are
  catalogued today) alongside the existing `TIME_SET_MANUAL`.
  **[manual review]**
- AC-21: Status-strip wording is asserted by unit test: when `time_health`
  dominates the merge the sentence matches the regex
  `^(Server time not synchronized|Camera .+ clock drifted [+-]\d+(\.\d+)?s) — resync$`.
  **[unit]**
- AC-22: When `time_health.state` is `"unknown"` (no timedatectl on
  host), it does **not** participate in `_worst()` — the strip is not
  flipped amber/red on its account. The Settings card shows the message
  "Time health unavailable in this environment".
  **[unit]**
- AC-23: A new `app/server/tests/integration/test_time_health.py`
  exercises the end-to-end summary path: mock cameras with a fresh
  beat 4s behind server time → `summary.state == "amber"`, sentence
  matches AC-21, deep_link == `/settings#time-health`.
  **[integration]**
- AC-24: A new `app/server/tests/contracts/test_api_contracts.py` row
  asserts the shape of `time_health` in `/system/summary.details` and
  the `time/health` and `time/resync` route shapes (status, JSON keys,
  required `target` field).
  **[contract]**
- AC-25: Hardware smoke (deferred to Implementer to wire concretely):
  on a real Pi-4 server + 1 paired camera, `date -s "+5 seconds"`
  on the camera (over ssh) within 30s flips the dashboard chip amber
  with the drift sentence; clicking Resync → camera's `timesyncd`
  restarts; chip returns green within 60s.
  **[hardware smoke]**

## Non-Goals

- Replacing `systemd-timesyncd` with `chrony` (ADR-0019 explicitly
  defers this; we do not reopen).
- Forcing camera time directly from the server (cameras already point
  at the server via NTP per ADR-0019; this ticket only observes and
  triggers a resync; it does not call `timedatectl set-time` on cameras).
- Per-camera timezone overrides (system uses one timezone — ADR-0019).
- Cryptographically signed timestamps for evidentiary use — separate
  threat model, separate ticket. The `time_health` chip improves
  *detectability* of drift, not non-repudiation.
- Auto-resync (cron, systemd timer, or background loop that calls
  `systemctl restart systemd-timesyncd` whenever drift is high). Not
  in v1 — operator-triggered only. Auto-resync is a defensible
  follow-up if field data shows operators ignore the chip.
- Server self-drift detection vs. an external authoritative source. The
  server's NTP-synchronized claim is taken at face value. (See OQ-2.)
- Bandwidth-based drift compensation / one-way latency estimation. The
  amber threshold (2s) is more than 100× the worst plausible LAN
  one-way latency.
- A separate "alerts" entry in `/api/v1/alerts`. ADR-0018 says one
  status-strip is the dashboard's surface; the chip is enough.
- Surfacing time-health on Tier-2 dashboard tiles. The four locked
  tiles (Cameras / Last activity / Storage / Recorder host) per
  ADR-0018 are intentionally locked. The chip + Settings card are the
  whole surface.

## Module / File Impact List

**New code:**

- `app/server/monitor/services/time_health_service.py` — `TimeHealthService`
  class. Pure derived-state computation: takes the camera store and
  `SettingsService` as constructor deps, returns the health dict from
  `compute_health()`. No I/O beyond the calls into those deps. ~150
  lines including locked-thresholds header.
- `app/server/monitor/api/time_health.py` (or extension of
  `api/system.py`) — `GET /api/v1/system/time/health` returning
  `TimeHealthService.compute_health()`; `POST /api/v1/system/time/resync`
  doing the server-vs-camera dispatch. Both `@admin_required`.
- `app/server/tests/unit/test_time_health_service.py` — drift math,
  hysteresis, state merge, offline-camera exclusion, sign convention,
  unknown-state handling, threshold boundaries.
- `app/server/tests/unit/test_api_time_health.py` — endpoint
  validation, target dispatch, audit-log emission, idempotency window.
- `app/server/tests/integration/test_time_health.py` — end-to-end
  summary path with realistic fixtures.
- `app/server/tests/contracts/test_api_contracts.py` — extend with
  `/system/summary` `time_health` block + the two new routes.

**Modified code:**

- `app/server/monitor/models.py:221-226` (Settings region) and the
  `Camera` model — add `last_beat_camera_ts: str = ""` to `Camera` (next
  to `last_seen`). No `Settings` field added.
- `app/server/monitor/services/camera_service.py:547` —
  `accept_heartbeat()` now parses `data.get("timestamp")` as a Unix
  epoch int/float, converts to UTC ISO-8601 with `Z`, persists into
  `camera.last_beat_camera_ts`. ~5 lines.
- `app/server/monitor/services/system_summary_service.py:120` —
  `__init__` gains `time_health` keyword arg; `compute_summary()`
  calls `time_health.compute_health()`, merges into `_worst()`, embeds
  result under `details.time_health`. The `_build_summary()` helper
  is extended with one branch for the time-health-dominant case
  (sentence per AC-11).
- `app/server/monitor/__init__.py` (`_init_services` /  `_startup`) —
  instantiate `TimeHealthService` and pass it to `SystemSummaryService`.
  Same factory pattern used for the existing services.
- `app/server/monitor/api/system.py` — register the new
  `time/health` and `time/resync` routes (or import the new blueprint).
- `app/server/monitor/api/__init__.py` — re-export only if a new
  blueprint is created; keeping it inside `system_bp` is preferred to
  avoid blueprint sprawl.
- `app/server/monitor/templates/settings.html` — add the Time Health
  card under the existing Date & Time section. Alpine.js x-data block
  fetches from `/system/time/health` on mount + on a 30s interval.
- `app/camera/camera_streamer/heartbeat.py:_apply_pending_config` —
  recognise the `time_resync` key, invoke `systemctl restart
  systemd-timesyncd`, log result.
- `openapi/server.yaml` — three additions: `time_health` schema under
  `SystemSummary.details`; `GET /system/time/health` (200 →
  `TimeHealth`); `POST /system/time/resync` (request body
  `{target: string}`, 200/400/404/500).
- `docs/cybersecurity/threat-model.md` — add `TIME_RESYNC_REQUESTED`
  to the audit-event catalogue.

**No change to:**

- `app/camera/camera_streamer/heartbeat.py:_build_payload` — already
  carries `timestamp`. No schema bump.
- `app/camera/config/timesyncd-camera.conf` — drop-in unchanged.
- nginx config — `/system/time/health` and `/system/time/resync` go
  through the existing `system_bp` proxy path; no new `location` block.

**Yocto:**

- No recipe change. No new RDEPENDS. The camera already has
  `systemd-timesyncd` and its drop-in baked in per ADR-0019.

## Validation Plan

Pulled from `docs/ai/validation-and-release.md` "Validation Matrix":

| Area touched | Required validation |
|--------------|---------------------|
| Server Python | `pytest app/server/tests/ -v`, `ruff check .`, `ruff format --check .` |
| Camera Python | `pytest app/camera/tests/ -v` (only `_apply_pending_config` test path), `ruff check .`, `ruff format --check .` |
| API contract | extend `test_api_contracts.py` for the `time_health` block + new routes; `openapi-validator` on `openapi/server.yaml` |
| Security-sensitive surface | `time/resync` is a new admin action invoking `systemctl restart` — admin-required, audit-logged, target-validated; documented threat-model row added |
| Requirements / risk / security / traceability | `python tools/traceability/check_traceability.py`, `python scripts/ai/check_doc_links.py` |
| Yocto config or recipe | not applicable (no recipe change, no unit-file change) |
| Hardware behaviour | smoke rows below |

Smoke-test additions (Implementer to wire concretely):

- "Force-skew a camera's clock by `+5s` (`date -s "+5 seconds"` on the
  camera over ssh); within `2 * HEARTBEAT_INTERVAL` (≤ 30s) the
  dashboard status strip reads
  `Camera <name> clock drifted +5s — resync` and is amber."
- "Click Resync on the affected camera; verify
  `journalctl -u systemd-timesyncd` on the camera shows a restart;
  within 60s the chip returns green and `summary.state == 'green'`."
- "Force the server's `timesyncd` into an unsynced state
  (`systemctl stop systemd-timesyncd && timedatectl set-ntp false &&
  timedatectl set-ntp true`); chip flips amber within 30s, then red
  after 30 minutes if upstream stays unreachable."
- "Click server Resync; `systemd-timesyncd` restarts, audit event
  `TIME_RESYNC_REQUESTED target=server` appears in
  `/data/audit.log`."
- "With a camera offline for > 5 minutes, the chip does **not** show a
  drift sentence (only the cameras-tile offline state)."

## Risk

ISO 14971-lite framing. Hazards specific to this change:

| ID | Hazard | Severity | Probability | Risk control |
|----|--------|----------|-------------|--------------|
| HAZ-250-1 | False-positive drift chip during normal LAN jitter (one-way latency, scheduling skew on an underloaded Pi-Zero camera). Operator dismisses repeated chips → desensitised → ignores a real drift. | Moderate (operational) | Medium | RC-250-1: AC-4 sets the amber threshold at 2s, which is two orders of magnitude above plausible LAN one-way latency. AC-4's hysteresis prevents oscillation. AC-25 hardware smoke validates on real Pi-4 + Pi-Zero. |
| HAZ-250-2 | Drift signal masks a deeper problem: clip filenames are written using the *server* clock (recorder host); a drifted *camera* clock does not actually corrupt clip filenames. The chip wording could mislead an operator into thinking clips are mistimed when they are not. | Moderate (operational) | Medium | RC-250-2: chip wording is "Camera <name> clock drifted +Xs — resync" (specific to the camera, not "your timestamps are wrong"). The Settings → Time Health card carries an explanatory tooltip about which timestamps a camera-side drift affects (heartbeats, motion ordering relative to other cameras) vs. which it does not (clip filenames, audit events — both server-side). |
| HAZ-250-3 | Per-camera resync stuck in pending_config because camera offline; operator clicks Resync repeatedly; pending_config never grows because the field is bool, but audit-log gets noisy. | Minor (audit) | Medium | RC-250-3: AC-15's idempotency window suppresses duplicate audit rows within 60s for the same target. |
| HAZ-250-4 | `systemctl restart systemd-timesyncd` on the server briefly disconnects the camera's NTP source (cameras point at server via mDNS) → cascading re-sync ripple. | Minor (operational) | Medium | RC-250-4: timesyncd restart on the server takes < 1s; cameras' next sync attempt picks up immediately. The cascade is the *intent* (resync everything) — documented in the Settings card. |
| HAZ-250-5 | Audit-log row written before the actual `systemctl restart` returns nonzero → false claim that resync happened. | Minor (audit) | Low | RC-250-5: AC-13 and AC-14 — audit row is written *after* a successful subprocess return; nonzero returns 500 and writes no audit row. |
| HAZ-250-6 | Heartbeat `timestamp` is forgeable by anyone who can reach the heartbeat endpoint (no HMAC over `timestamp` today, beyond what the existing camera-auth covers). A compromised camera could forge a drift to flip the chip. | Minor (security) | Low | RC-250-6: existing camera auth (HMAC-bearer per ADR-0016) gates the heartbeat endpoint; an attacker who can post heartbeats already has bigger problems than flipping a UI chip. Drift signal does not control any safety-critical action. Tracked as `THREAT-250-1`. |
| HAZ-250-7 | The `MAX_DRIFT_SECONDS_REPORTED` cap masks an extreme drift (> 1 hour) so the operator sees `+3600s — resync` on every camera with a dead RTC, indistinguishable from "very broken" vs. "totally broken". | Minor (operational) | Low | RC-250-7: cap is 1 hour, an extreme drift; the chip wording is "Camera <name> clock far ahead of server — resync" when capped. Logs the raw drift at INFO so journals carry the real value. |
| HAZ-250-8 | Operator clicks Resync on a camera that is in the middle of a recording; restart of `timesyncd` causes a sub-second clock jump that interrupts the recording or splits a clip. | Moderate (operational) | Low | RC-250-8: `timesyncd`'s adjustments are slewed (not stepped) by default; a restart re-establishes sync without a step. AC-25 hardware smoke verifies recording continuity across a resync. |
| HAZ-250-9 | The `time_health` block grows over time as future PRs "just add another field" until it leaks internal state. | Minor (security) | Medium | RC-250-9: AC-19 + AC-24 pin the contract in OpenAPI and the contracts test. Future PRs adding fields fail the contract test and require explicit re-design. |

Reference `docs/risk/hazard-analysis.md` — this spec adds rows.

## Security

Threat-model deltas (Implementer fills `THREAT-` / `SC-` IDs in
`docs/cybersecurity/threat-model.md`):

- **Sensitive paths touched:** none from the architect.md sensitive
  list. No `**/auth/**`, `**/secrets/**`, OTA, pairing, certificate, or
  workflow change. The new `systemctl restart systemd-timesyncd`
  invocation is admin-gated and audit-logged but is the same shape as
  the existing `timedatectl set-ntp` invocation in `SettingsService`.
- **New attack surface — `POST /api/v1/system/time/resync`
  (admin-required):** invokes `systemctl restart systemd-timesyncd`
  on the server or queues a flag for a camera. Auth is the existing
  admin-required decorator. Rate-limit / idempotency: AC-15 caps to
  one effective restart per 60s per target. Subprocess invocation
  argv is hard-coded (`["systemctl", "restart", "systemd-timesyncd"]`)
  — no shell, no user-controlled argv components. Target string is
  validated against `"server"` or a known camera id; no other strings
  reach subprocess.
- **New attack surface — `GET /api/v1/system/time/health`
  (admin-required):** returns the same payload as the embedded
  `details.time_health` block in `/system/summary` (the latter is
  also admin-required by inheritance from the existing dashboard auth
  rules). No new public surface.
- **Heartbeat `timestamp` trust:** the existing camera auth (HMAC-
  bearer per ADR-0016) is the trust boundary. A compromised camera
  could forge `timestamp`, but an attacker with the camera's HMAC
  secret already has heartbeat write access — the marginal capability
  added here (flip the dashboard chip amber/red) is harmless.
  `THREAT-250-1`.
- **Audit:** every resync writes `TIME_RESYNC_REQUESTED` with target,
  user, IP. Existing audit-log retention applies.
- **`/system/time/resync` is the only new mutation surface.** It is
  bound to loopback + admin auth; no nginx exposure change.
- **No new env-var read at runtime.** No new file write. The
  `pending_config` channel is the existing one; we add one bool key.

## Traceability

Placeholder IDs (Implementer fills concrete numbers in
`docs/traceability/traceability-matrix.md`):

- `UN-250` — User need: "When my server's clock is unsynchronised or
  one of my cameras has drifted, I want the dashboard to tell me, with
  a single click to fix it."
- `SYS-250` — System requirement: "The system shall observe NTP-sync
  state on the server and clock drift between each camera and the
  server, surface a non-fatal warning on the dashboard, and provide
  an operator-triggered resync action."
- `SWR-250-A` — `TimeHealthService` derived-state shape and threshold
  set (amber 2s, red 30s, server-red 30min, hysteresis 0.5s).
- `SWR-250-B` — Drift computation: server `last_seen` minus camera
  `last_beat_camera_ts`, sign-aware, no LAN-latency adjustment.
- `SWR-250-C` — `Camera.last_beat_camera_ts` field; populated from
  the existing heartbeat `timestamp` payload on `accept_heartbeat`.
- `SWR-250-D` — `time_health` block embedded in
  `/api/v1/system/summary.details`; chip sentence rules (AC-11).
- `SWR-250-E` — `GET /api/v1/system/time/health` (admin) and
  `POST /api/v1/system/time/resync` (admin).
- `SWR-250-F` — Camera-side `_apply_pending_config` consumes
  `time_resync: true` by `systemctl restart systemd-timesyncd`.
- `SWR-250-G` — Settings → Date & Time → Time Health card
  (per-camera drift table + Resync button).
- `SWA-250` — Software architecture item: "Per-process derived-state
  service in the existing service-layer pattern (ADR-0003); chip
  rendered via the existing Tier-1 status-strip (ADR-0018); no
  changes to the Tier-2 locked tiles."
- `HAZ-250-1` … `HAZ-250-9` — listed above.
- `RISK-250-1` … `RISK-250-9` — one per hazard.
- `RC-250-1` … `RC-250-9` — one per risk control.
- `SEC-250-A` (admin-required guard on `time/resync` and
  `time/health`).
- `SEC-250-B` (subprocess argv hard-coded; target string validated
  against an allow-list).
- `SEC-250-C` (audit-log row written on success only;
  `TIME_RESYNC_REQUESTED` event added).
- `THREAT-250-1` (compromised camera forges `timestamp` to flip chip
  → bounded by camera auth; no safety impact).
- `THREAT-250-2` (`time_health` block leaks internal state over time
  → pinned by OpenAPI + contract test).
- `SC-250-1` … `SC-250-N` — controls mapping to the threats above.
- `TC-250-AC-1` … `TC-250-AC-25` — one test case per acceptance criterion.

## Deployment Impact

- **Yocto rebuild needed: no.** No recipe change, no unit-file change,
  no new RDEPENDS. Server Python deploys via the standard server image
  pipeline; camera Python via the standard camera image pipeline.
- **OTA path: standard server + camera image OTA** (ADR-0008 A/B
  rollback). New server image carries `TimeHealthService` and the new
  routes; old camera image continues to work (it already sends
  `timestamp` in heartbeats — server happily computes drift; the
  resync flag is a new key the old camera ignores in
  `_apply_pending_config`, no error). Forward compat is therefore
  free; backward compat is via "old camera + new server is fine; new
  camera + old server is fine (camera ignores the resync flag because
  the old server never sets it)".
- **Hardware verification: yes — required.** Smoke rows AC-25 plus
  the four others under Validation Plan.
- **Default state on upgrade:** time-health observation is **on** as
  soon as the new server image installs. No opt-in. The chip stays
  green unless drift exceeds the locked thresholds. Per-camera
  resync is operator-triggered only; nothing happens automatically.
- **Backwards compatibility on a partial-merge:** if the server-side
  feature lands without the camera-side `_apply_pending_config`
  change, the resync flag is queued but never consumed; pending_config
  shows it indefinitely. Mitigation: ship server + camera changes
  atomically in one PR.

## Open Questions

(None blocking; design proceeds.)

- OQ-1: Drift threshold — 2s amber / 30s red, or tighter (e.g., 1s amber)?
  **Recommendation:** 2s amber / 30s red. Sub-second LAN one-way
  latency is well under 1s; 2s gives a comfortable margin. 30s red
  matches the existing `OFFLINE_TIMEOUT = 30s` constant, so anything
  beyond that is "hard-broken clock", not "transient sync hiccup".
  Implementer revisits if smoke data shows false positives.
- OQ-2: Server self-drift detection (e.g., compare against the
  device's RTC, or a stratum-1 source). Out of scope for v1?
  **Recommendation:** out of scope. ADR-0019 already chose to trust
  `timesyncd` on the server. Adding a second authoritative reference
  is a separate ticket — possibly revisited if `chrony` is adopted.
  This spec relies on `NTPSynchronized=yes/no` from `timedatectl`.
- OQ-3: Should drift be adjusted by an estimated one-way LAN latency
  (e.g., median ping time)?
  **Recommendation:** no for v1. The amber threshold (2s) is over
  100× the worst plausible LAN one-way latency. Adding latency
  estimation adds a moving baseline that is itself a source of
  bugs.
- OQ-4: Should the camera-side resync use `systemctl try-restart`
  instead of `restart` (no-op if the unit is masked)?
  **Recommendation:** `restart`. The drop-in from ADR-0019 ensures
  the unit is enabled on every camera; `try-restart` would silently
  no-op on a misconfigured device, hiding the failure. `restart`
  fails loudly via the camera's own log.
- OQ-5: Should the `time_health` block carry historical drift (e.g.,
  last-30-min mean and stddev) for the Settings card to graph?
  **Recommendation:** no for v1. No historical drift store; just the
  most recent point. A trend graph is a defensible follow-up if
  operators report wanting to see "is drift getting worse?".
- OQ-6: Should we expose `last_beat_camera_ts` on the existing
  `/api/v1/cameras` endpoint so an external integrator can compute
  drift themselves?
  **Recommendation:** yes — additive, no risk. Implementer wires
  this in the same PR if cheap; otherwise a follow-up.
- OQ-7: Should the Settings → Time Health card auto-refresh, or
  require a manual page reload?
  **Recommendation:** auto-refresh on a 30s interval (matches the
  existing dashboard 10s polling cadence approximately, but slower
  to reduce load on a settings-deep page that nobody is actively
  watching). Implementer free to choose 30s or 60s.

## Implementation Guardrails

- Preserve service-layer pattern (ADR-0003): `TimeHealthService` is a
  service in `app/server/monitor/services/`; it has no Flask import,
  no template render, no direct subprocess.run; all I/O routes through
  injected dependencies (`store`, `settings_service`).
- Preserve modular monolith (ADR-0006): the new service runs as a
  function call from `SystemSummaryService.compute_summary()` — same
  process, no new daemon, no new thread.
- Preserve ADR-0018 dashboard IA: the new signal joins the existing
  `_worst()` merge for the Tier-1 status strip; **no new tile** is
  added; **no new alert center**.
- Preserve ADR-0019 topology: cameras still NTP-resolve the server via
  mDNS; the server still runs `systemd-timesyncd`. No `chrony`. No
  `timedatectl set-time` called on cameras from the server.
- Locked thresholds must be module-level constants with the
  "LOCKED — see ADR-0018" header. Any future PR that touches them
  triggers an explicit ADR.
- The drift sign is load-bearing. `+` = camera ahead, `-` = camera
  behind. Asserted in unit tests.
- The chip sentence is load-bearing. Asserted by regex in AC-21 so
  future copy-edits can't slip in a confusing rephrase that breaks
  the operator's mental model.
- `time/resync` is the only new mutation. It is **always** admin-
  required; it **always** writes an audit row on success only; it
  **never** invokes a shell.
- Heartbeat schema is unchanged. The `timestamp` field already exists;
  we just persist it.
- `time_health.state == "unknown"` is **never** allowed to flip the
  status strip amber/red. Unknown is unknown, not "worst-case
  amber/red".
- Tests + docs + smoke-row updates ship in the same PR as code, per
  `docs/ai/engineering-standards.md`.
- Traceability annotations on `time_health_service.py` and the new
  routes use the existing `# REQ:` / `# RISK:` / `# SEC:` / `# TEST:`
  comment header pattern.

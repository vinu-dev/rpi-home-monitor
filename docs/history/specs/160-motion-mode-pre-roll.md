# Feature Spec: Motion-Mode Pre-Roll (3 s Ring Buffer)

Tracking issue: #160. Branch: `feature/160-motion-mode-pre-roll`.
Companion exec plan: `docs/exec-plans/motion-mode-pre-roll.md`
(landed via PR #202 — the four-phase implementation plan; this spec
is the architect-controlled traceability + acceptance contract that
each of those four PRs must satisfy).

## Title

When `recording_mode = motion`, save a 3-second pre-roll window so
the saved MP4 contains the action that fired the motion event,
not just the post-encoder-spawn aftermath. Closes the long-standing
"click event → empty scene" UX bug by realising the **D5a
decision** recorded in
`docs/archive/exec-plans/motion-detection.md` (3 s H.264 ring buffer,
default-on with a kill-switch flag) on the Picamera2 backend that
shipped under ADR-0021.

## Goal

Restate of issue #160. Today, when a camera is on
`recording_mode = motion`:

1. The motion detector fires `motion_event:start` to the server.
2. The server's `RecordingScheduler` reacts and asks
   `StreamingService.start_recorder(...)` to spawn an ffmpeg
   subprocess that writes `/data/recordings/<camera_id>/...mp4`.
3. The ffmpeg startup + Picamera pipeline takes 1-3 s to produce
   the first encoded keyframe; the saved MP4's wallclock
   `start_time` is `event_time + encoder_latency`.
4. The Recent Events row links to that clip with
   `offset_seconds = 0` (or the pre-tolerance fallback in
   `motion_clip_correlator.py:_pre_tol`); the user clicks and sees
   an empty room. The hand wave / person walking that triggered the
   alert is gone.

After this spec ships, the saved clip for a motion event in motion
mode contains **the action that fired the event**, with ~3 s of
scene context **before** the detected motion. The dashboard's
existing `<video>#t=` seek lands the playhead at the moment of
detection inside a clip whose first frame is 3 s earlier.

The user-visible win: motion mode stops looking broken. Operators
who care about sub-second-of-action review (parents checking on a
napping child; homeowners reviewing a porch alert) get a usable
clip without paying the 24/7 recording disk cost.

This is **product correctness work**, not a new feature. The
`recording_mode = motion` option already ships, the dashboard
already shows it, and the bug at issue #160 is the final blocker
on declaring motion mode a reasonable default for low-power
deployments.

## Context

Existing code this change builds on, not replaces.

### Camera side

- `app/camera/camera_streamer/picam_backend.py:114-470` —
  `PicameraH264Backend`. Owns the single `H264Encoder` instance
  (line 305-323). The ring buffer plugs in here as a **second
  sink** on the existing encoder (see "What's actually missing"
  below). The contract is unchanged: `H264Encoder` keeps emitting
  bytes to `FileOutput(self._ffmpeg.stdin)` for live RTSPS push;
  `CircularOutput` is an additional output, not a replacement.
- `app/camera/camera_streamer/motion_runner.py:1-390` — the motion
  detector runtime. Fires start/end transitions to the server via
  `server_poster.post(...)` (HMAC-signed, same scheme as
  heartbeat). The `start` transition gains a second consumer: the
  local backend, which opens the merged clip file and starts the
  ring flush.
- `app/camera/camera_streamer/control.py` — `recording_mode`
  plumbing. Untouched in shape; the backend reads
  `recording_mode == "motion"` from the existing `ConfigManager`.
- `app/camera/camera_streamer/config.py` — adds two config keys
  (`MOTION_PREROLL_ENABLED`, `MOTION_PREROLL_SECONDS`). Same
  `DEFAULTS` pattern as every other camera config knob.
- `app/camera/config/camera-streamer.service:39` —
  `ReadWritePaths=/data /var/lib/camera-ota`. The merged clip
  writes under `/data` (camera-local recordings staging dir), so
  no new path needs to be added. The existing systemd-hardening
  unit test (`app/camera/tests/unit/test_systemd_hardening.py`)
  covers this; the spec asserts the test stays green.
- `app/camera/camera_streamer/heartbeat.py:294-330` — capability
  block sent up with each heartbeat. Grows by one optional bool
  field (`motion_pre_roll: bool`) so the server records which
  cameras are delivering pre-roll-merged clips. Visibility only;
  does not gate UI in v1.

### Server side

- `app/server/monitor/services/recording_scheduler.py:1-242` —
  evaluates `recording_mode = motion` via
  `MotionEventStore.is_camera_active(...)`. The line 9 docstring
  ("treated as off") is **stale** — it predates Phase 4 wiring
  and contradicts lines 156-172 which now actually evaluate
  motion. Fixed in this PR alongside any motion-clip-correlator
  arithmetic edit (single-PR doc-truth pass per AC-13).
- `app/server/monitor/services/motion_clip_correlator.py:71-234` —
  finds the finalised clip covering an event timestamp. Today
  computes `offset_seconds` as
  `event_dt - clip_start_dt` (clamped to
  `[0, clip_duration)`) plus a `_pre_tol` fallback (line 100) that
  zeros offset for events that fired in the encoder-startup gap.
  After this spec, when a motion event carries
  `pre_roll_seconds > 0`, the clip's first frame is
  `started_at - pre_roll_seconds` and the offset is
  `pre_roll_seconds` (not `0`), so the dashboard `<video>#t=`
  fragment lands on the moment of detection rather than the start
  of the pre-roll context.
- `app/server/monitor/services/motion_event_store.py:46-235` —
  persists motion events to
  `/data/config/motion_events.json`. Schema gains one optional
  integer field (`pre_roll_seconds`); legacy events default to
  0; `_load()`'s `MotionEvent(**item)` already tolerates extra
  fields per dataclass semantics if `MotionEvent` adds the field
  with a default.
- `app/server/monitor/models.py:309-327` — `MotionEvent`
  dataclass. Gains
  `pre_roll_seconds: int = 0` (zero-defaulted; backwards-compat
  on read, opt-in on write).
- `app/server/monitor/api/cameras.py` — heartbeat ingestion.
  Persists the camera-reported `motion_pre_roll: bool` capability
  flag onto the `Camera` row (visibility only).
- `app/server/monitor/services/streaming_service.py` /
  `recorder_service.py` — server-side ffmpeg recorder. **The
  canonical-clip ownership question (camera-side merged clip vs
  server-side ffmpeg recorder) is OQ-1 below.** The implementer
  resolves OQ-1 during Phase 1 before the merged-clip write path
  ships.

### Background reading

- `docs/archive/exec-plans/motion-detection.md` §D5 — the original
  decision record (3 s ring buffer, kill-switch flag default-on
  for v1; D5a is the alternative chosen). This spec is the
  contract-level realisation.
- ADR-0017 — recording modes. Pre-roll is an internal property of
  motion mode; ADR-0017's "off / continuous / motion / schedule"
  vocabulary is preserved.
- ADR-0021 — Picamera2 became the shipped backend, which is what
  makes `CircularOutput` available; the original D5 decision
  pre-dated this.
- ADR-0023 — fault framework. Pre-roll start/stop failures are a
  potential fault surface, but **out of scope for v1** — the
  kill-switch flag is the primary mitigation; per-fault
  classification is a follow-up if real-hardware soak (Phase 4)
  surfaces an actionable failure mode.

### What's actually missing today

1. **The ring buffer itself.** `picamera2.outputs.CircularOutput`
   is shipped by
   `meta-home-monitor/recipes-multimedia/picamera2/python3-picamera2_0.3.34.bb`
   but is not attached to `H264Encoder` anywhere in
   `picam_backend.py`.
2. **A merged-clip switching mechanism.** No code path today opens
   a "pre-roll + live" output file on the camera or on the server.
3. **An event-record `pre_roll_seconds` annotation.** Absent from
   `MotionEvent` and from
   `/data/config/motion_events.json`.
4. **Correlator awareness of pre-roll offset.** The current math
   only knows about `started_at` vs clip-stem timestamp.
5. **Capability advertisement.** Server has no way to know which
   cameras are delivering pre-rolled clips today.
6. **Canonical-clip ownership decision.** See OQ-1 below.

## User-Facing Behavior

### Primary path — operator (the saved clip contains the action)

1. Operator sets `recording_mode = motion` for a camera (existing
   UX, no change).
2. Operator (or the world) waves a hand in front of the camera.
3. Camera's motion detector fires the start transition. The camera
   backend simultaneously:
   - posts the start event to the server (existing path), and
   - flushes its 3 s pre-roll ring to the merged-clip file and
     keeps writing live frames into it.
4. Camera fires the end transition; backend closes the merged-clip
   file after `MOTION_POST_ROLL_SECONDS` of post-roll grace.
5. Server's correlator matches the event to the merged clip in
   `/data/recordings/<camera_id>/`, computes
   `offset_seconds = pre_roll_seconds` (default 3), and writes the
   `clip_ref` onto the `MotionEvent` row.
6. Operator opens Recent Events, clicks the row. Dashboard plays
   the clip, seeks to `t=offset_seconds`, the playhead lands on the
   moment of detection. The 3 seconds **before** the playhead
   contain the lead-up. The hand wave is no longer missing.

### Failure states (must be designed, not just unit-tested)

These are the operationally interesting paths each implementer PR
must keep behaving correctly. Each is testable.

- **Camera reboot during a motion event.** The ring is in RAM; on
  reboot the partial `.part` file is left behind. On next start,
  the camera startup path discards `.part` files smaller than
  `MIN_RETAIN_BYTES` (~32 KB). No zero-action noise files
  accumulate. AC-7 covers this.
- **Encoder restart mid-event.** The backend treats this as an
  abort: `stop_pre_rolled_recording('aborted')`, discard `.part`
  if too small, drop pre-roll for the in-flight event. Subsequent
  events behave normally. AC-8 covers this.
- **Motion-mode toggled off during an event.** Same as encoder
  restart — abort path. The user explicitly asked to stop
  recording motion clips; their explicit ask wins over completing
  the in-flight clip. AC-9 covers this.
- **`MOTION_PREROLL_ENABLED = false` (kill switch).** Camera
  behaves exactly like today: the existing late-recorder path
  runs; saved clips start cold at detection time;
  `pre_roll_seconds = 0` on the event record; correlator math
  reduces to the existing `_pre_tol` fallback. **This is the
  rollback path** if Phase 4 hardware soak reveals a regression
  in the wild. AC-10 covers this.
- **Legacy motion events** (events written before Phase 3 ships,
  in `motion_events.json` with no `pre_roll_seconds` field).
  `MotionEvent` dataclass default is 0; correlator math reduces
  to today's behaviour for those events. **Zero-impact upgrade
  read path.** AC-11 covers this.
- **Camera advertises pre-roll, server cannot find the merged
  clip.** Today's `_pre_tol` fallback (offset 0) still applies;
  the dashboard plays the clip at offset 0. The user sees a clip
  that may or may not contain the moment of detection (degraded
  but non-broken). One log line records the correlator miss.
  AC-12 covers this.
- **`H264Encoder` keyframe boundary issue.** The merged clip's
  first byte is mid-GOP if the ring's tail isn't a keyframe.
  Mitigation: `H264Encoder` is configured with periodic
  keyframes (`iperiod=keyframe_interval`,
  `picam_backend.py:312`), and `repeat=True` inlines SPS/PPS with
  every keyframe. The merged clip's first keyframe within the ring
  is the playable seek point; players that can't seek to byte 0
  scan forward to the first keyframe (Chrome / Firefox / iOS
  Safari behaviour confirmed during Phase 4 validation per
  ffprobe). AC-13 covers this.

### Non-failure: continuous and schedule modes are untouched

`recording_mode = continuous` and `recording_mode = schedule` do
not get pre-roll. They already record into the active clip when
motion fires; correlator already correlates events to clip offsets.
This is explicit Non-Goal §N1 below.

## Acceptance Criteria

Each bullet is testable; verification mechanism noted in brackets.
Mapped 1:1 to TC-160-N traceability IDs (see Traceability §).

- AC-1: When `recording_mode = motion` AND
  `MOTION_PREROLL_ENABLED = true`, the camera's `H264Encoder` has
  a `CircularOutput` attached as a second sink, sized for
  `MOTION_PREROLL_SECONDS * target_fps` frames. The existing
  `FileOutput(ffmpeg.stdin)` sink remains attached and unchanged.
  **[unit: `app/camera/tests/unit/test_picam_backend.py` —
  inspect `encoder.output` after `start()` returns]**
- AC-2: `PicameraH264Backend.start_pre_rolled_recording(path,
  started_at)` atomically opens `<path>.part`, sets
  `ring.fileoutput`, starts the ring, returns
  `pre_roll_seconds_actual` (the buffered duration that was
  flushed). Returned value is bounded by
  `[0, MOTION_PREROLL_SECONDS]` — at startup the ring may not yet
  be full, in which case the actual pre-roll is shorter.
  **[unit: feed synthetic frames into a stub encoder, assert the
  pre-roll bytes appear ahead of the live bytes; assert the
  returned value matches the buffered duration]**
- AC-3: `PicameraH264Backend.stop_pre_rolled_recording(reason)`
  closes the ring's fileoutput, renames `.part` → final clip
  filename, and returns
  `{path, pre_roll_seconds, total_seconds}`. On `reason='aborted'`
  with `.part` size below `MIN_RETAIN_BYTES`, the `.part` is
  deleted and the return is `None`. No symbol leak across
  start/stop pairs.
  **[unit: assert post-conditions; assert no leftover `.part`
  after abort-with-small-file; assert the rename is atomic]**
- AC-4: `motion_runner.py` start-transition handler calls
  `backend.start_pre_rolled_recording(...)` **before**
  `server_poster.post(start_event)`. Rationale: if the server
  post fails (network blip), the local clip still has the action.
  The end-transition handler calls
  `backend.stop_pre_rolled_recording('post_roll_done')` after the
  configured `MOTION_POST_ROLL_SECONDS` grace.
  **[unit: mock the backend, assert call ordering under start,
  end, abort, restart-during-event]**
- AC-5: `MotionEvent.pre_roll_seconds: int = 0` field exists on
  the dataclass. `MotionEventStore.append(event)` persists it.
  Heartbeat ingestion accepts the camera-reported value when the
  capability flag advertises pre-roll.
  **[unit: `app/server/tests/unit/test_motion_event_store.py` —
  round-trip a non-zero value through write + read; assert
  legacy records (no field) round-trip with 0]**
- AC-6: `MotionClipCorrelator.find_clip(camera_id, started_at)`,
  given an event with `pre_roll_seconds = N > 0`, returns
  `clip_ref.offset_seconds = N` when it matches a clip whose
  filename timestamp equals `started_at - N` (within
  `±_pre_tol`). When `pre_roll_seconds = 0`, the existing math
  is unchanged.
  **[unit:
  `app/server/tests/unit/test_motion_clip_correlator.py` — fixture
  with `pre_roll_seconds = 3` and a clip stem 3 s before the
  event; assert offset = 3]**
- AC-7: Camera-side startup discards `.part` files in the merged
  clip directory smaller than `MIN_RETAIN_BYTES` (~32 KB) before
  starting the encoder. Files at-or-above the threshold are left
  alone (potential resumable recordings, though resumption is
  out of scope).
  **[unit: synthetic `.part` fixtures of varying sizes; assert
  startup behaviour]**
- AC-8: On encoder restart, `stop_pre_rolled_recording('aborted')`
  is called from the lifecycle teardown. The
  `motion_event:end` post is **not** suppressed — the server
  still gets the event close so the UI doesn't show "ongoing"
  forever.
  **[unit: simulate encoder failure mid-event; assert teardown
  call sequence]**
- AC-9: When operator toggles `recording_mode` away from `motion`
  during an in-flight event, `stop_pre_rolled_recording('aborted')`
  fires before the next mode evaluation; the in-flight `.part`
  is discarded if too small.
  **[unit: control-handler test that flips mode mid-event]**
- AC-10: With `MOTION_PREROLL_ENABLED = false`, the camera does
  not attach `CircularOutput` to the encoder, does not call any
  pre-roll method, and `MotionEvent.pre_roll_seconds` on
  emitted events is `0`. The system behaves identically to the
  pre-#160 implementation.
  **[unit + integration: flip the flag; assert no ring is
  attached, assert events have `pre_roll_seconds == 0`,
  assert recording_scheduler still uses the existing late-spawn
  path]**
- AC-11: Pre-Phase-3 motion event records (rows in
  `motion_events.json` written before this PR, with no
  `pre_roll_seconds` field) round-trip through
  `MotionEventStore._load()` with `pre_roll_seconds = 0`. The
  correlator returns the same `clip_ref` for them as it does
  today (no behavioural drift on legacy data).
  **[migration test: load a pre-#160 fixture, run one
  correlation cycle, diff against expected current behaviour]**
- AC-12: When the correlator cannot find a clip with the
  pre-roll-adjusted timestamp, it falls back to the existing
  `_pre_tol` window (which produces `offset_seconds = 0`). One
  log line at INFO level records the correlator miss with
  `(camera_id, event_id, pre_roll_seconds)`. The dashboard
  experience is degraded (playhead at clip start) but
  non-broken.
  **[unit: fixture with `pre_roll_seconds = 3` but no matching
  clip; assert fallback path; assert log line]**
- AC-13: The merged clip plays from byte 0 in Chrome, Firefox, and
  iOS Safari. `ffprobe` reports a sane `start_time` and a first
  keyframe within the configured `keyframe_interval`. This is
  validated on real hardware before the kill-switch flag is
  flipped to default-on (Phase 3 step 14 of the exec plan).
  **[hardware: Phase 4 step 15-16 of the exec plan; manual
  ffprobe + browser playback]**
- AC-14: `recording_scheduler.py` line-9 docstring is updated to
  describe Phase 4 motion-mode behaviour (motion mode is
  evaluated when `motion_event_store` is wired; absent only in
  pre-Phase-4 tests). One-line doc fix; no logic change.
  **[doc-truth review on the PR diff]**
- AC-15: Heartbeat capability block grows one optional field
  `motion_pre_roll: bool`; server stores it on the `Camera` row.
  No UI surface in v1.
  **[unit: heartbeat round-trip test asserts the field
  persists]**
- AC-16: Validation matrix rows pass on the resulting branches:
  `pytest app/camera/tests/ -v` (camera) AND
  `pytest app/server/tests/ -v` (server),
  `ruff check .`, `ruff format --check .`,
  `python tools/traceability/check_traceability.py`. Coverage
  stays at-or-above the 80 % camera / 85 % server gates.
  **[CI: existing pipelines]**
- AC-17: `docs/exec-plans/motion-mode-pre-roll.md` Phase
  references in PR descriptions match the as-built. Each of the
  four phase PRs lists which ACs from this spec it satisfies.
  **[PR-description review]**

## Non-Goals

- **N1: Pre-roll for `recording_mode = continuous`**. Continuous
  already records 24/7 — `MotionClipCorrelator` already
  correlates events to clip offsets; nothing missing on that
  path.
- **N2: Pre-roll for `recording_mode = schedule`**. Same reasoning
  as continuous: when a schedule window is active, the recorder
  is already running; events inside the window land in the
  running segment.
- **N3: Audio.** The repo doesn't capture audio anywhere
  (ADR-0017 §1).
- **N4: Replacing `H264Encoder`.** The shipped encoder feeds the
  RTSPS push via `FileOutput(self._ffmpeg.stdin)`; pre-roll is
  an additional sink on the same encoder, not a new pipeline.
  ADR-0021's "single-encoder discipline" is preserved.
- **N5: Configurable pre-roll duration in the UI.** 3 s is the
  decided default; making it tunable is a follow-up. The
  `MOTION_PREROLL_SECONDS` config knob exists for ops debug, not
  for end-user tuning.
- **N6: CRL / OCSP / cert revocation work.** Out of scope; not
  related to the bug.
- **N7: Per-camera UI toggle for pre-roll.** Server records the
  capability flag (AC-15) for future use, but no v1 UI knob.
  Operators get pre-roll with motion mode or they don't get
  motion mode pre-roll. Single deployment-wide flag is the
  rollback control.
- **N8: ADR-0023 fault-framework integration.** Pre-roll
  start/stop failures could be a fault surface, but the v1
  posture is "kill-switch flag flips off, single log line, no
  fault." Phase 4 soak data informs whether the fault hookup is
  worth it as a follow-up.
- **N9: Server-side recording-of-camera-merged-clip ingestion
  protocol changes**. **Constrained by OQ-1 below.** If OQ-1
  resolves to "camera writes locally + server ingests via new
  endpoint," the endpoint design is in scope of THIS spec's
  Phase 1 deliverable; if it resolves to "server-side recorder
  remains canonical with pre-roll injection," the camera-side
  merged-clip is a debug-only artefact and N9 holds. The
  implementer's Phase 1 PR closes OQ-1 explicitly.

## Module / file impact list

Concrete files and likely changes. Implementer may discover
small adjacent additions (logging glue, audit-log enum entries,
fixture updates) — those are in scope.

| File | Change |
|------|--------|
| `app/camera/camera_streamer/picam_backend.py` | Add `CircularOutput` second sink on `H264Encoder` (AC-1). Add `start_pre_rolled_recording` / `stop_pre_rolled_recording` methods (AC-2, AC-3). Add startup `.part`-file sweep (AC-7). Honour `MOTION_PREROLL_ENABLED` (AC-10). |
| `app/camera/camera_streamer/motion_runner.py` | On start transition, call `backend.start_pre_rolled_recording` before `server_poster.post` (AC-4). On end + post-roll, call `stop_pre_rolled_recording('post_roll_done')`. On abort paths (encoder restart, mode toggle off, camera reboot), call `stop_pre_rolled_recording('aborted')` (AC-8, AC-9). |
| `app/camera/camera_streamer/control.py` | Mode-toggle path triggers in-flight abort when `recording_mode` leaves `motion` (AC-9). |
| `app/camera/camera_streamer/config.py` | Add `MOTION_PREROLL_ENABLED` (default `False` in Phase 1, flipped to `True` in Phase 3 after validation), `MOTION_PREROLL_SECONDS` (default `3`), `MIN_RETAIN_BYTES` (default `32_768`). |
| `app/camera/camera_streamer/heartbeat.py` | Capability block grows `motion_pre_roll: bool` (AC-15). |
| `app/camera/config/camera-streamer.service` | **No change** — `ReadWritePaths=/data` already covers the merged-clip write path. |
| `app/server/monitor/models.py` | `MotionEvent` gains `pre_roll_seconds: int = 0` (AC-5). |
| `app/server/monitor/services/motion_event_store.py` | `_load()` and `append()` round-trip the new field (AC-5, AC-11). No schema-version bump (default 0 is the legacy semantics). |
| `app/server/monitor/services/motion_clip_correlator.py` | When `event.pre_roll_seconds > 0`, the correlator searches for a clip whose stem timestamp equals `event.started_at - pre_roll_seconds` (within `±_pre_tol`); returned `offset_seconds` is `pre_roll_seconds`. Fallback path on no match (AC-6, AC-12). |
| `app/server/monitor/services/recording_scheduler.py` | Line 9 docstring fix (AC-14). **OQ-1-dependent**: if motion mode shifts to camera-canonical clip ownership, the scheduler may stop spawning a server-side recorder for motion mode (camera owns the clip). Implementer closes during Phase 1. |
| `app/server/monitor/api/cameras.py` | Heartbeat ingestion persists `motion_pre_roll: bool` capability flag onto `Camera` row (AC-15). |
| `app/server/monitor/services/recordings_service.py` (or whatever the current ingestion path is named — implementer confirms) | **OQ-1-dependent.** If the merged clip uploads from camera to server, this is the ingest path. If the server-side recorder remains canonical, no change. |
| `app/camera/tests/unit/test_picam_backend.py` | New ring-buffer cases (AC-1 through AC-3, AC-7, AC-10). |
| `app/camera/tests/unit/test_motion_runner.py` | New signal-on-start / abort-path tests (AC-4, AC-8, AC-9). |
| `app/camera/tests/unit/test_systemd_hardening.py` | **No change required** — merged clips write under `/data` which is already in `ReadWritePaths`. The test must still pass on the new code (AC-16 implicit). |
| `app/server/tests/unit/test_motion_clip_correlator.py` | New offset-arithmetic cases (AC-6, AC-11, AC-12). |
| `app/server/tests/unit/test_motion_event_store.py` | New `pre_roll_seconds` round-trip (AC-5, AC-11). |
| `docs/history/adr/0021-camera-side-motion-detection.md` (or equivalent) | "Open items" section: strike pre-roll off the list (Phase 3 step 14). |
| `CHANGELOG.md` (or `docs/history/releases/...`) | Phase 3 PR adds an entry: "Motion-mode clips now contain ~3 s of pre-roll context (issue #160)." |
| `tools/traceability/` matrix | Add REQ-160-1, ARCH-160-1, RISK-160-N, SEC-160-N, TC-160-1..TC-160-17 rows. |

`tools/traceability/check_traceability.py` will check that every
new REQ / ARCH / RISK / SEC / TEST ID in the changed source files
is listed in the matrix. No tooling changes.

## Validation Plan

Pull the applicable rows from `docs/ai/validation-and-release.md`'s
validation matrix:

- **Camera Python.** `pytest app/camera/tests/ -v`,
  `ruff check .`, `ruff format --check .`. Coverage gate
  `--cov-fail-under=80`. Required for every phase touching the
  camera (Phases 1, 2, and any camera-side fixes in Phases 3-4).
- **Server Python.** `pytest app/server/tests/ -v`, ruff. Coverage
  gate `--cov-fail-under=85`. Required for every phase touching
  the server (Phase 3 mainly; Phase 4 does not change code).
- **API contract.** Existing camera-control and motion-event
  contract suites must stay green. Pre-roll is a new optional
  field, not a contract break.
- **Security-sensitive path.** **Not a security-sensitive path
  per `docs/ai/roles/architect.md`'s sensitive-paths list.** This
  spec touches `motion_runner.py`, `picam_backend.py`,
  `motion_event_store.py`, and the correlator — none of which are
  on the auth/secrets/pairing/OTA path. Standard validation
  applies (no extra security-suite gate).
- **Requirements / risk / security / traceability / annotated
  code.** `python tools/traceability/check_traceability.py`.
  Required — this spec adds new traceability IDs.
- **Repository governance.**
  `python tools/docs/check_doc_map.py`,
  `python scripts/ai/check_doc_links.py`,
  `python scripts/ai/validate_repo_ai_setup.py`,
  `python -m pre_commit run --all-files`. The doc updates land in
  files the doc-map already tracks; no `doc-map.yml` edit needed.
- **Yocto config or recipe.** **Not applicable** — `picamera2` is
  already shipped by
  `meta-home-monitor/recipes-multimedia/picamera2/python3-picamera2_0.3.34.bb`,
  the version that includes `CircularOutput`. No new Yocto dep,
  no recipe bump, no image rebuild. Camera-image redeploy via the
  existing OTA pipeline once the four PRs land.
- **Hardware behavior.** **Required at Phase 4.** Per the exec
  plan steps 15-18: deploy to OV5647 ZeroCam (already paired,
  low-stakes), wave hand for 5 s, open event, confirm clip starts
  ~3 s before wave (ffprobe + visual). Repeat on IMX219 lab
  camera. 24-hour soak: count `.part` files left behind, count
  abort transitions, confirm RAM stays bounded. Run
  `bash scripts/smoke-test.sh <server-ip> <pwd> <camera-ip> <pwd>`
  before and after deploy; compare live-stream startup leg —
  pre-roll must not regress that path.

### Coverage gate proof per AC

The new ACs map to existing tests files in
`app/camera/tests/unit/` and `app/server/tests/unit/` with
existing fixtures available; the additions are bounded
(estimated ~150-300 new test lines across the suite). No new
test infra needed.

## Risk

ISO 14971-lite framing per `docs/ai/medical-traceability.md`.
This is a recording-pipeline correctness change in a
home-security product. Hazards are operational and
trust-of-evidence hazards.

| Hazard | Severity | Probability | Risk control |
|--------|----------|-------------|--------------|
| **HAZ-160-1**: `CircularOutput` introduces a memory leak on the Zero 2W under sustained motion mode (e.g. an outdoor camera with a tree branch waving for hours). | Medium (camera process OOMs / restarts; user sees a "camera offline" alert) | Low (3 s @ 4 Mbps = ~1.5 MB; the buffer is bounded) | RC-160-1: Phase 4 24-hour soak (exec plan step 17). `MOTION_PREROLL_ENABLED = false` rollback flag. `MIN_RETAIN_BYTES` discard guard prevents `.part` accumulation. |
| **HAZ-160-2**: Pre-roll changes the H.264 keyframe boundary in the saved clip; players can't seek to t=0; the clip looks broken. | Medium (UX regression on a fix is worse than the original bug) | Low (`H264Encoder` repeats SPS/PPS with each keyframe; players scan forward to first keyframe) | RC-160-2: AC-13 — Phase 4 ffprobe + Chrome / Firefox / iOS Safari verification before flipping `MOTION_PREROLL_ENABLED` default to true. If any browser breaks, the flag stays off and the rollout pauses. |
| **HAZ-160-3**: Server's `MotionClipCorrelator` math drifts when `pre_roll_seconds` is missing on legacy events; legacy events stop matching their clips after upgrade. | Medium (events from before this PR show empty clip refs) | Would be high without RC | RC-160-3: AC-11 + AC-6 — `pre_roll_seconds` defaults to 0 on read; existing tests for legacy events stay green; new tests cover the non-zero case. |
| **HAZ-160-4**: Recording-scheduler stale docstring (line 9 still says motion is treated as off) becomes a self-fulfilling lie if a future agent reads it and "fixes" it back to a no-op. | Low (per-PR human review catches it) | Realised already (the stale docstring exists today) | RC-160-4: AC-14 — fix in this PR, not a follow-up. |
| **HAZ-160-5**: Hardening rule violation if the merged-clip write lands outside `ReadWritePaths`. | Medium (camera-streamer.service refuses to start; "camera offline" alert) | Very low (the merged clip writes under `/data` which is already in `ReadWritePaths`) | RC-160-5: AC-7 + AC-16 — existing `test_systemd_hardening.py` enforces `ReadWritePaths`; merged-clip write path passes the same test as today's recordings. |
| **HAZ-160-6**: Sensor swap mid-event leaves a partial `.part` file. | Low (one orphan file per swap) | Very low (sensor swap is a rare physical action) | RC-160-6: AC-7 — startup sweep deletes `.part` smaller than `MIN_RETAIN_BYTES`; the existing recordings index already ignores `.part` extensions per `motion_clip_correlator.py:166`. |
| **HAZ-160-7**: OQ-1 resolves to "camera-canonical clip" and the camera→server upload path is the new bug surface. | Medium (motion-mode clips don't appear server-side; user sees a broken event) | Low if Phase 1 designs the upload path before Phase 2 ships | RC-160-7: OQ-1 closure during Phase 1 with explicit AC additions in the Phase 1 PR if the upload path is in scope. |

Risk-control summary: every loud-failure case is covered by an
explicit AC. The only residual risk is HAZ-160-7 (the OQ-1
resolution risk), and it is bounded by the requirement that the
implementer close OQ-1 explicitly during Phase 1, not implicitly
during Phase 2.

## Security

This change does **not** touch the sensitive-paths list in
`docs/ai/roles/architect.md`:

- `**/auth/**`, `**/secrets/**`: untouched.
- `**/.github/workflows/**`: untouched.
- `app/camera/camera_streamer/lifecycle.py`, `wifi.py`,
  `pairing.py`: untouched.
- certificate / TLS / pairing / OTA flow code: untouched.
- `docs/cybersecurity/**`, `docs/risk/**`: only the risk register
  is updated with new HAZ-160-N rows — additive only, no
  posture change.

Threat-model deltas:

- **THREAT-160-1** (informational): A motion event's
  `pre_roll_seconds` field is operator-visible metadata now. It
  is not security-sensitive (the existence of motion at a given
  time is already in the event record); the field just clarifies
  the offset of the saved clip. **No new disclosure surface.**
- **THREAT-160-2** (informational): Camera capability flag
  `motion_pre_roll: bool` is heartbeat-included. The heartbeat
  is already mTLS-authenticated (per ADR-0009). **No new
  exposure.**

ADR-0022 ("No Backdoors") audit:

- Rule 1 (no auth-bypassing command/script/endpoint): satisfied
  — pre-roll plumbing reuses existing motion-event channels;
  no new endpoint.
- Rule 2 (pre-auth surfaces disclose nothing): satisfied — no
  new pre-auth surface.
- Rule 3 (lost-sole-admin recovery is hardware): unaffected.
- Rule 5 (when in doubt, refuse): the kill-switch flag is the
  posture for "we are not sure pre-roll is safe on this
  hardware yet" (Phase 1 ships it default-off; Phase 3 flips
  default-on after Phase 4 validation). Rule 5 is honoured by
  shipping behind a flag, not by absence.

If OQ-1 resolves to "camera writes merged clip locally + server
ingests via new endpoint," the new endpoint **inherits the
existing mTLS-authenticated heartbeat / motion-event channel**
(per ADR-0015). The implementer's Phase 1 PR must show this
endpoint passes the same auth contract; otherwise OQ-1 is a
sensitive-path expansion and re-routes through architect /
security review before merge.

## Traceability

Placeholder IDs the Implementer fills in (per
`docs/ai/medical-traceability.md`). Each touched code/test file
must carry at least one `REQ:` annotation per the standing rule.

Annotation block to add at the top of each newly modified file
(or extend existing block):

```
# REQ: SWR-160; ARCH: SWA-160; RISK: RISK-160-1, RISK-160-2, RISK-160-3;
# TEST: TC-160-1..TC-160-17
```

ID space proposed for this spec (Implementer pins exact numbers
during traceability matrix update):

- **REQ:** `SWR-160` — "When `recording_mode = motion` and
  pre-roll is enabled, the saved clip for a motion event
  contains at least `MOTION_PREROLL_SECONDS` of scene context
  before the event's `started_at` timestamp."
- **ARCH:** `SWA-160` — "Pre-roll is a `CircularOutput` second
  sink on the existing `H264Encoder`; merged clip is written
  by the camera; correlator math accounts for
  `pre_roll_seconds` offset."
- **RISK:** `RISK-160-1` (memory bound), `RISK-160-2` (player
  seek), `RISK-160-3` (legacy event drift). HAZ-160-4 through
  HAZ-160-7 reuse RC-160-N labels rather than new RISK rows
  (they are doc-truth / startup-sweep / OQ-1 mechanic risks,
  not new safety hazards).
- **SEC:** No new SEC IDs — this change is not on the
  sensitive-paths list (see Security §). The threat-model deltas
  are informational only.
- **TEST:** `TC-160-1` through `TC-160-17` mapping 1:1 to
  AC-1 .. AC-17 above.

Each new ID must be added to the traceability matrix
(`tools/traceability/`) with links back to user need
**UN-recording-evidence** (existing) or, if missing, a new user
need **UN-160-motion-event-evidence** with description: "An
operator who clicks a motion event sees a saved clip that
contains the action that triggered the event."

## Deployment Impact

- **Yocto rebuild needed?** **No.** `picamera2 0.3.34` is already
  shipped; `CircularOutput` is in that version; no recipe edit.
- **Camera firmware change?** **Yes** — Phases 1 and 2 land
  camera-side Python; the existing camera-image OTA path
  delivers them.
- **Server image change?** **Yes** — Phase 3 lands server-side
  Python (event store schema, correlator, heartbeat ingestion);
  the existing server-package-deploy path applies.
- **OTA path?** Existing camera OTA + server-package-deploy.
  Phases ship as four separate PRs (per exec plan); each merges
  to `main` independently. No flag-day deploy.
- **Hardware verification?** **Yes** — Phase 4 is mandatory
  (exec plan steps 15-18). Without Phase 4 sign-off, the
  `MOTION_PREROLL_ENABLED` flag stays default-off.
- **Migration?** Existing motion events keep working
  (AC-11). Already-paired cameras get pre-roll on the first
  camera-image OTA after Phase 2 ships, with the flag still
  default-off until Phase 3 flips it.
- **Rollback?** Pure software rollback per phase. The
  kill-switch flag (`MOTION_PREROLL_ENABLED = false`) is the
  in-the-field rollback control — no OTA needed to disable
  pre-roll on a problematic camera. Ship operators can flip the
  flag via the existing config-push channel without a code
  change.
- **Backwards compatibility window?** Pre-roll-aware events and
  pre-roll-unaware events coexist forever (the `0` default is
  permanent). No expiry, no schema migration, no deprecated
  field cleanup.

## Open Questions

- **OQ-1 (PRIMARY): Which side owns the canonical merged clip
  for motion-mode events with pre-roll?** The exec plan describes
  the camera writing a merged "pre-roll + live" MP4 via
  `CircularOutput`, AND a server-side recorder still spawning
  via `RecordingScheduler` on motion events. Two clips on disk
  is wrong. The two viable resolutions:
  - **(A) Camera-canonical**: server's recorder is **disabled**
    in motion-mode-with-pre-roll. The camera writes the merged
    clip locally and uploads it to the server (over the existing
    mTLS channel) when motion ends. Server's
    `MotionClipCorrelator` finds it in the same recordings dir
    as today. **Pro:** one clip, simple correlator math.
    **Con:** new camera→server clip-upload protocol is in scope.
  - **(B) Server-canonical**: camera does not write a merged
    clip; server's recorder spawns as today, BUT the camera
    sends the buffered pre-roll bytes to the server's recorder
    via a new mechanism so the server's clip starts ~3 s
    earlier. **Pro:** no upload protocol, server stays the
    canonical clip writer. **Con:** new server-recorder
    extension; the H.264 PTS continuity across the buffered
    flush boundary is non-trivial.
  - **(C) Camera-canonical with no upload**: camera-side merged
    clip stays on the camera; the server's correlator returns
    a `clip_ref` with a flag indicating the clip is camera-side;
    dashboard fetches the bytes from the camera's status server
    on demand. **Pro:** no protocol changes, no disk migration.
    **Con:** dashboard playback path must learn the new
    fetch-from-camera flow; events from offline cameras have
    no playable clip.

  This spec does **not** decide between (A), (B), and (C). The
  Phase 1 implementation PR closes OQ-1 with a one-paragraph
  decision record (committed alongside the Phase 1 code), and
  the matching test additions either expand or contract the
  Module Impact list above. **OQ-1 is non-blocking for label
  transition** to `ready-for-implementation` because each
  resolution is implementable; the implementer must not start
  Phase 2 until OQ-1 is recorded.

- **OQ-2: Should `MOTION_PREROLL_SECONDS` be per-camera, or
  global?** v1 is global (one config file, one number). A
  per-camera override would let a slow-Pi-Zero camera set 2 s
  while a Pi 4 camera uses 5 s. Treated as future work; not in
  scope for this spec. **Non-blocking unless reviewer flips
  it.**

- **OQ-3: Should the dashboard surface that the clip has
  pre-roll?** Today the playhead lands at offset; the user sees
  the event with leading context, no banner. Argument for a
  banner: helps the user understand why the clip starts before
  their stated event time. Argument against: every banner is
  noise. v1 ships without a banner. **Non-blocking.**

- **OQ-4: Should pre-roll respect a per-camera
  `motion_pre_roll: bool` toggle from the dashboard?** No in
  v1; the heartbeat capability flag is read-only visibility
  per AC-15. A future "operator-disable-pre-roll-on-this-
  camera" toggle is one way OQ-2 grows up. Not blocking.

No question above is regulatory or hardware-redesign in nature,
so this issue does **not** need a `blocked` label per
`docs/ai/roles/architect.md`'s "When to label `blocked` instead
of designing." OQ-1 is the only one that **must** close before
Phase 2 ships; all others can stay open and be folded into
follow-up specs without blocking #160 closure.

## Implementation Guardrails

- **Don't add a new pre-auth endpoint.** If OQ-1 resolves to (A)
  with an upload, the upload endpoint must inherit the existing
  mTLS-authenticated channel (per ADR-0015 / ADR-0022).
- **Don't change `recording_mode` vocabulary.** ADR-0017's
  `off / continuous / motion / schedule` set is preserved.
  Pre-roll is a property of the `motion` mode, not a new mode.
- **Don't bump the `motion_events.json` schema version.** The
  `pre_roll_seconds` field is additive with a 0 default;
  existing rows round-trip unchanged.
- **Don't ship Phase 3's flag flip without Phase 4 sign-off.**
  AC-13 + RC-160-2 require ffprobe + browser verification
  before `MOTION_PREROLL_ENABLED` defaults to `true`. The
  implementer must not bundle the flag flip into the Phase 3
  PR if Phase 4 hasn't completed.
- **Don't bypass `MIN_RETAIN_BYTES` for "interesting-looking"
  small files.** AC-7 must be a pure size threshold; semantic
  filtering grows the surface for keep-vs-discard bugs.
- **Don't conflate "pre-roll-disabled-by-config" with
  "pre-roll-failed-on-this-event."** Disabled is silent
  (current-day behaviour); failed emits one log line and a
  fault if `ADR-0023`-style fault classification is wired
  later (N8). Distinct paths.

## Alternatives Considered

### A. 3-second `CircularOutput` ring buffer (chosen)

What this spec proposes. Realises the D5a decision from
`docs/archive/exec-plans/motion-detection.md` against the
Picamera2 backend that shipped under ADR-0021. Smallest blast
radius compatible with the H264 single-encoder discipline.
Kill-switch flag for in-the-field rollback. Phased delivery
matches the exec plan.

### B. Continuous-for-a-window (motion-triggered)

When motion fires, record continuously for the event duration
+ cooldown; when idle long enough, stop. Per the issue body's
option (b). Rejected because:

- Still misses the first second or two of the action — the
  encoder spawn latency is the same regardless of how long the
  recording then runs.
- The bug is "first ~8-15 s missing"; this alternative shrinks
  it to "first 1-3 s missing." Better than today, but worse
  than pre-roll. Fails the user-need bar.

Documented here so it is not re-proposed as "simpler" in six
months.

### C. Switch all cameras to `recording_mode = continuous`

Per the issue body's "Workaround" section. This is the current
operator workaround; it always works. Rejected as a product
answer because:

- 24/7 disk cost: ~1 GB / camera / day at 4 Mbps. A 64 GB SD
  fills in two months with one camera; less with two.
- Defeats the purpose of motion mode (low-bandwidth /
  low-storage deployments where motion detection is the
  selling point).
- Doesn't fix the bug; it sidesteps motion mode entirely.

### D. Push the recorder to start earlier (server-side
heuristic)

When motion fires, the server could spawn the recorder
immediately and pre-pad the clip with a black/synthetic frame
to compensate for the encoder-spawn gap. Rejected because:

- Synthetic frames are not the action — the user wants the
  scene context, not 3 s of black.
- Doesn't compose with H.264 PTS continuity.
- Fails the user-need bar even more loudly than B.

### E. Keyframe-aligned ring (fixed N keyframes instead of
fixed-time buffer)

Instead of `MOTION_PREROLL_SECONDS * fps` frames, buffer the
last N keyframes-worth of bytes. Rejected because:

- The H.264 keyframe interval is operator-tunable
  (`keyframe_interval` in `control.py:PARAM_SCHEMA`); a
  fixed-N-keyframe buffer's wallclock duration is unpredictable.
- The user-facing contract is "~3 s of context"; that is a
  wallclock contract, not a byte contract.
- `picamera2.CircularOutput`'s `buffersize` parameter is in
  frames anyway — the math `seconds * fps` falls out
  naturally.

## Cross-References

- Issue #160 — the user-visible bug.
- `docs/exec-plans/motion-mode-pre-roll.md` — the four-phase
  implementation plan (PR #202 landed the plan; this spec is
  the contract those phases must satisfy).
- `docs/archive/exec-plans/motion-detection.md` §D5 / §D5a —
  the original decision record.
- ADR-0017 — recording modes (preserved).
- ADR-0021 — Picamera2 backend (the pipeline this hooks into).
- ADR-0022 — no backdoors (audit passed).
- ADR-0023 — fault framework (potential future hookup; out of
  scope for v1 per N8).
- `docs/ai/execution-rules.md` Systemd Hardening Rule (Phase 4
  step 18 enforcement; AC-7 + AC-16).

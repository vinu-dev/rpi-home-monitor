# Exec Plan: Motion-mode pre-roll (issue #160)

## Goal

When `recording_mode = motion`, the saved MP4 clip must contain the
seconds **before** the motion event fired, so the action that triggered
the event is in the recording. Today the clip starts cold at detection
time + encoder startup latency (~1–3 s on a Pi Zero 2W), and the user
clicks an event row to open a clip whose first frame is "an empty
scene where the action just ended."

Implements the **D5a decision** already recorded in
[`docs/exec-plans/motion-detection.md` §D5](motion-detection.md), which
chose a 3-second H.264 ring buffer with a `MOTION_PREROLL_ENABLED=false`
kill switch. The decision pre-dates ADR-0021 (Picamera2 became the
shipped backend, which makes `CircularOutput` available); this plan
translates the decision into an implementation now that the capture
pipeline supports it.

## Non-Goals

- Pre-roll for `recording_mode = continuous`. Continuous already records
  24/7 — `MotionClipCorrelator` already correlates events to clip
  offsets; nothing missing on that path.
- Pre-roll for `schedule` mode. Same reasoning as continuous: when a
  schedule window is active, the recorder is already running; events
  inside the window land in the running segment.
- Configurable pre-roll duration in the UI for v1. 3 seconds is the
  decided default; making it tunable is a follow-up.
- Audio. The repo doesn't capture audio anywhere (ADR-0017 §1).
- Replacing the encoder. The existing `H264Encoder` instance from
  `picam_backend.py` is the producer; pre-roll is a second sink on the
  same encoder, not a new pipeline.

## Constraints

- **RAM budget.** A 3 s ring buffer at 4 Mbps H.264 = ~1.5 MB. Trivial
  against the Zero 2W's ~200 MB free.
- **Single-encoder discipline.** ADR-0021 ships one `H264Encoder`
  feeding the RTSP ffmpeg via `FileOutput(self._ffmpeg.stdin)`. The
  ring buffer has to share that encoder — Picamera2's `CircularOutput`
  is designed to be attached to the same encoder as a second output,
  not a duplicate encoder.
- **Kill switch required.** Per D5a's fallback clause, pre-roll must
  ship behind a config flag (`MOTION_PREROLL_ENABLED`, default `true`)
  so we can flip it off in the field without an OTA if `CircularOutput`
  misbehaves on real hardware. Same discipline as the
  `CAMERA_STREAM_BACKEND=cli` fallback documented in motion-detection.md
  §D1.
- **Hardening contract.** `camera-streamer.service` runs as
  `User=camera` with a strict `ReadWritePaths`. Any new file the
  pre-roll path writes (the merged "pre-roll + live recording" MP4)
  must land under an already-permitted path *and* be added to the
  static hardening test (`app/camera/tests/unit/test_systemd_hardening.py`)
  per the Systemd Hardening Rule (`docs/ai/execution-rules.md`).
- **Version SSOT.** No new firmware version reads — keep using
  `app/shared/release_version/release_version.py`.

## Context

### Files involved

**Camera side:**
- `app/camera/camera_streamer/picam_backend.py` — owns `H264Encoder`,
  `FileOutput`, ffmpeg child. The ring buffer plugs in here.
- `app/camera/camera_streamer/motion_runner.py` — currently fires the
  event; on `start` transition it must signal the backend to flush the
  ring + start writing the merged clip.
- `app/camera/camera_streamer/control.py` — `recording_mode` plumbing.
- `app/camera/config/camera-streamer.service` — `ReadWritePaths`.

**Server side:**
- `app/server/monitor/services/recording_scheduler.py` — already wires
  motion mode (lines 207–220 evaluate via `MotionEventStore.is_camera_active`).
  The line-9 docstring still says "treated as off" — *stale, predates
  Phase 4*. Needs a doc fix in the same PR or a separate one.
- `app/server/monitor/services/motion_clip_correlator.py` — links
  events to clips by `(camera_id, started_at)`. Pre-roll changes
  `started_at`-vs-clip-offset arithmetic: the correlator must learn
  that a motion-mode clip's first frame is `event_started_at - 3 s`,
  not `event_started_at`.
- `app/server/monitor/api/cameras.py` — heartbeat ingestion; capability
  block grows by one optional bool field (see "Wire format" below).

**Tests:**
- `app/camera/tests/unit/test_picam_backend.py` (new ring-buffer cases)
- `app/camera/tests/unit/test_motion_runner.py` (signal-on-start test)
- `app/camera/tests/unit/test_systemd_hardening.py` (no new path needed
  if the merged clip writes to the existing recordings dir)
- `app/server/tests/unit/test_motion_clip_correlator.py` (offset
  arithmetic when `pre_roll_seconds > 0`)

### What the existing code already gives us

- One `H264Encoder` instance per camera (`picam_backend.py:317`).
- A working ffmpeg sink that's been gated by motion-event-active
  windows for a release.
- `motion_runner.py` already emits start/end transitions to a server
  poster — adding a second consumer (the local backend) is a one-line
  callback registration.
- `MotionClipCorrelator` already knows how to seek inside a clip; the
  arithmetic just needs a per-clip `pre_roll_seconds` annotation.

### What's actually missing

1. The ring buffer itself. `Picamera2.encoders.CircularOutput` exists
   in the picamera2 package shipped by
   `meta-home-monitor/recipes-multimedia/picamera2/python3-picamera2_0.3.34.bb`.
   Verified API:
   ```python
   from picamera2.outputs import CircularOutput
   ring = CircularOutput(buffersize=BUFFER_FRAMES)  # frames, not bytes
   encoder.output = [encoder.output, ring]          # list of sinks
   # on event:
   ring.fileoutput = open(merged_clip_path, "wb")
   ring.start()  # flushes buffer then continues writing live frames
   ```
2. A switching mechanism in `picam_backend` that opens the merged-clip
   file, asks the ring buffer to flush, then closes it on motion-end +
   post-roll.
3. A `pre_roll_seconds` field on the persisted motion event (currently
   absent from the JSON in `/data/config/motion_events.json` — see
   `docs/exec-plans/motion-detection.md` §D6 record schema; this adds
   one optional integer field, default `0` for backward compat).
4. Correlator + UI awareness that the clip's `offset_seconds` for a
   motion event is `pre_roll_seconds`, not `0`.
5. The `MOTION_PREROLL_ENABLED` config knob and its plumbing through
   the heartbeat capability block (so the server knows whether a
   given camera is recording with pre-roll, in case a future
   per-camera UI toggle wants it).

## Plan

### Phase 1 — Backend ring buffer (camera-only, behind feature flag default-off)

1. Add `MOTION_PREROLL_ENABLED` (default `False` in this phase, flipped
   to `True` in Phase 3 once validated) and `MOTION_PREROLL_SECONDS`
   (default `3`) to camera config.
2. In `picam_backend.py`, when motion mode is selected and the flag is
   on, create a `CircularOutput` sized for `pre_roll_seconds *
   target_fps` frames and attach it as a second sink on the existing
   `H264Encoder`. Verify with a unit test that uses a fake encoder
   stub.
3. New backend method `start_pre_rolled_recording(path, started_at)`:
   atomically opens `<path>.part`, sets `ring.fileoutput`, starts the
   ring (which flushes the buffered frames first, then continues
   live), and returns the offset where `started_at` lands inside the
   resulting file (= `len(buffered)` in seconds).
4. Symmetric `stop_pre_rolled_recording(reason)`: closes ring, renames
   `.part` → final, returns metadata `{path, pre_roll_seconds,
   total_seconds}`.
5. Tests: feed synthetic frames into a stub encoder, assert the
   pre-roll bytes appear ahead of the live bytes; assert the offset
   math; assert no leak across start/stop pairs.

### Phase 2 — Wire motion_runner to backend

6. `motion_runner.py` currently calls `server_poster.post(start_event)`
   on the start transition. Add a second call:
   `backend.start_pre_rolled_recording(...)` *before* the post, so the
   ring is closed on the recording side first; that way if the post
   fails the local clip still has the action.
7. On end transition + post-roll grace expiry,
   `backend.stop_pre_rolled_recording('post_roll_done')`.
8. On any of: camera reboot, motion-mode toggled off, encoder restart —
   `stop_pre_rolled_recording('aborted')` and discard the `.part` if
   it's smaller than `MIN_RETAIN_BYTES` (~32 KB, prevents zero-action
   noise files from polluting recordings).
9. Tests: mock the backend, assert start/stop pairing under start, end,
   abort, restart-during-event.

### Phase 3 — Server arithmetic + UI

10. Extend the motion event JSON record (`/data/config/motion_events.json`)
    with `pre_roll_seconds: int` (optional, default 0). Server-side
    `MotionEventStore.upsert` accepts the new field; legacy events
    without it continue to work.
11. `MotionClipCorrelator` learns: for an event with `pre_roll_seconds`
    set, the clip's first frame is `started_at - pre_roll_seconds`. The
    `seek=` URL parameter the correlator emits already drives the
    dashboard's `<video>` tag offset; just add the field to the math.
12. Heartbeat capability block grows one optional bool field
    `motion_pre_roll: bool` so the server records which cameras are
    delivering pre-roll'd clips (visibility only — does not gate UI).
13. Dashboard: no UI change in v1. The `<video>` tag's `#t=offset`
    fragment already takes the post-correlator offset, so users
    transparently land at the moment of detection inside a clip whose
    first frame is 3 s earlier.
14. Flip `MOTION_PREROLL_ENABLED` default to `True`. Document the
    field in CHANGELOG and ADR-0021's "Open items" section.

### Phase 4 — Hardware verification

15. Deploy the resulting image to one OV5647 ZeroCam in the lab
    (already paired, low-stakes). Wave hand for 5 s,
    open the event, confirm the saved clip starts ~3 s before the wave.
16. Repeat on an IMX219 lab camera — sensor differences shouldn't matter,
    but verify.
17. 24-hour soak: count `.part` files left behind, count abort
    transitions, confirm RAM stays bounded.
18. Smoke test (`bash scripts/smoke-test.sh`) before and after to make
    sure live streaming + non-motion modes are unaffected.

## Resumption

- **Current status:** Plan written. No code yet.
- **Last completed step:** This document.
- **Next step:** Phase 1 step 1 — add config flags. Branch suggestion:
  `feat/160-motion-pre-roll-phase1`.
- **Branch / PR:** none yet.
- **Devices / environments:** OV5647 ZeroCam and IMX219 lab cameras for
  hardware-stage validation. Lab server
  unchanged — no server image changes needed for Phase 1.
- **Commands to resume:**
  ```bash
  git checkout main && git pull
  git checkout -b feat/160-motion-pre-roll-phase1
  # implement Phase 1 step 1 (config flags + tests)
  pytest app/camera/tests/ -v
  ```
- **Open risks / blockers:** `CircularOutput` behaviour on a Zero 2W
  under sustained motion-mode use isn't proven. The kill-switch flag
  is the safety net; if it misbehaves we ship `MOTION_PREROLL_ENABLED=false`
  by default and track the underlying picamera2 bug.

## Validation

For each phase:

- `pytest app/camera/tests/ -v` (camera) and `pytest app/server/tests/
  -v` (server) — must stay above the 80/85 % coverage gates from
  `docs/ai/validation-and-release.md`.
- `ruff check . && ruff format --check .`
- For Phase 4 only:
  ```bash
  bash scripts/smoke-test.sh <server-ip> <pwd> <camera-ip> <pwd>
  ```
  before and after deploy. Compare the run timing for the live-stream
  startup leg — pre-roll must not regress that path.

## Risks

| Risk | Mitigation |
|---|---|
| `CircularOutput` introduces a memory leak on the Zero 2W under sustained motion | Phase 4 24h soak; `MOTION_PREROLL_ENABLED=false` rollback flag; `MIN_RETAIN_BYTES` discard guard so abort paths don't accumulate `.part` files |
| Pre-roll changes the H.264 keyframe boundary in the saved clip and players can't seek to t=0 | The `H264Encoder` is configured for periodic keyframes; the merged clip's first frame is whichever one is in the ring buffer at flush time. Validate with `ffprobe` that the merged file plays from byte 0 in Chrome + Firefox + iOS Safari before flipping the default flag |
| Server's `MotionClipCorrelator` math drifts when `pre_roll_seconds` is missing on legacy events | Default to 0 in the read path; existing tests for legacy events stay green; new tests cover the non-zero case |
| Recording-scheduler stale docstring (line 9 of `recording_scheduler.py` still says motion is treated as off) becomes a self-fulfilling lie if a future agent reads it and "fixes" it back to a no-op | Out of scope for this plan — flagged as a separate trivial doc PR for the same hands |
| Hardening rule violation if the merged-clip write lands outside `ReadWritePaths` | Reuse the existing recordings directory which is already covered; add an assertion in the test from `test_systemd_hardening.py` if the path differs |
| Sensor swap mid-event leaves a partial `.part` file | Phase 2 step 8 handles abort; the `.part` extension means the existing recordings index already ignores it |

## Completion Criteria

- [ ] All four phases merged via separate PRs, each with passing CI
      (server + camera tests, ruff, pre-commit).
- [ ] On a paired Zero 2W camera in motion mode, waving a hand at the
      sensor produces a saved MP4 whose first ~3 seconds show the
      scene **before** the wave (verified with `ffprobe` start time
      and visual inspection).
- [ ] Smoke test passes both before and after the deploy with no
      regression on the continuous / off / schedule paths.
- [ ] CHANGELOG entry written; ADR-0021's "Open items" updated to
      strike pre-roll off the list.
- [ ] Issue #160 closed with a comment linking the four merged PRs and
      a note that the original D5a decision is now implemented.

## References

- Issue #160 — the user-visible bug
- `docs/exec-plans/motion-detection.md` §D5 — the original decision
- ADR-0017 — recording modes, on-demand streaming
- ADR-0021 — camera-side motion detection (the shipped pipeline this
  hooks into)
- ADR-0023 — fault framework (potential surface for ring-buffer
  failures, though out of scope for v1)
- `docs/ai/execution-rules.md` Systemd Hardening Rule (Phase 4 step 18
  enforcement)

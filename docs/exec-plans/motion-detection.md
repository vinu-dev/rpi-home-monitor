# Exec Plan — Camera-Side Motion Detection

**Status:** Shipped (Phase 2). See [ADR-0021](../adr/0021-camera-side-motion-detection.md) for the formal decision record.
**Date:** 2026-04-19 (proposal) → 2026-04-20 (shipped)
**Owner:** vinu-dev

> Shipped on branch `feat/motion-events-ui`. The sections below are the
> design as implemented; the "Rejected alternatives" and "Decision
> points" notes are preserved for future maintainers.
> Open items beyond this plan (per-camera sensitivity slider, motion
> zones, server-side MOG2) are tracked in ADR-0021 §Open items.

---

## Goal

Make `recording_mode = "motion"` **actually work**. Today it's a reserved
enum value silently treated as `off` (ADR-0017 §6). The UI already
exposes it alongside Off / Continuous / Schedule; the gap is the
end-to-end wiring.

Detection runs **on the camera** (not the server). Every detection
surfaces in two places on the server:

1. **Recordings** — the clip that contains the motion is annotated with
   one or more motion-event markers (start offset, duration, intensity).
   The recordings UI shows red ticks on the scrubber and filters the
   clip list to "with motion only".
2. **Events** — every motion detection produces a first-class event on
   the server (timeline, audit log entry, dashboard badge, future
   notification hook). Events outlive the clip: even if loop-recording
   has deleted the underlying MP4, the event record persists.

### Scope clarifications

**Live viewing works exactly as it does today.** ADR-0017 says opening
the Live tab triggers an on-demand WebRTC stream regardless of recording
mode. That remains true in motion mode. Motion detection only drives the
**recorder**; it does not gate the live path. A user in motion mode can
still open Live at any time, and the camera will stream on demand —
same as Off / Continuous / Schedule today.

**Motion events are logged in every recording mode.** The detector is a
passive observer: whenever the camera H.264 encoder is running (live
viewer, scheduled recording, continuous, or motion-triggered), the
detector runs alongside it at negligible cost and emits events. Only
the *recording reaction* differs:

| Mode        | Event logged?                              | Clip saved for the event?                       |
|-------------|--------------------------------------------|-------------------------------------------------|
| Off         | Yes, while a live viewer is active         | No (mode = off)                                 |
| Continuous  | Yes, always                                | Yes — part of the ongoing continuous segment    |
| Schedule    | Yes, during schedule windows               | Yes — part of the ongoing scheduled segment     |
| Motion      | Yes, always                                | Yes — clip triggered by the event itself        |

This means a user on Continuous can still scan "motion events last
night" and jump to the interesting moments inside a 24-hour stream,
instead of scrubbing hours of corridor.

**Click-through behaviour for an event — two outcomes only:**

```
Click event → is a finalised clip on disk for this timestamp?
  │
  ├── yes ─► Recordings page, auto-seek to event offset
  │
  └── no  ─► Live view
```

That is the whole rule. Every click lands somewhere; no "recording was
off" screen, no "rotated" screen, no "event too brief" screen.
Simplicity wins over completeness for this surface.

Behaviour that follows naturally:

- **Continuous** — fresh event → Live for a few minutes while the
  segment is still being written, then same click lands on Recordings.
- **Motion** — clip was created *for* the event, so almost always
  Recordings.
- **Schedule** — Recordings if event fell inside a scheduled window,
  otherwise Live.
- **Off** (camera encoder was up because a viewer was on Live) — no
  clip exists, so the click goes to Live. The user sees the current
  camera scene. Acceptable because the event is already visible in the
  Events list with its metadata; Live is a natural "see what's going
  on now" continuation.

Three preconditions the router depends on, and their counter-measures:

| Precondition                                | Failure mode if ignored                              | Fix                                                                                 |
|---------------------------------------------|------------------------------------------------------|-------------------------------------------------------------------------------------|
| "On disk" excludes in-progress writes       | Router sends user to a half-written MP4 that won't play | Recorder writes `foo.mp4.part`, renames to `foo.mp4` on clean segment close         |
| Event timestamp is authoritative server-time| Camera clock skew → wrong clip lookup → always Live  | Server stamps event time from HMAC `X-Timestamp` (already validated), not from body |
| Events don't flood storage                  | Runaway detector fills `motion_events.json` in an hour | Server rate-limits to ≤1 start-event / 20 s per camera; excess returns 429          |

User outcome: the operator can open the dashboard, see *"3 motion
events overnight at the Front Door camera"*, click through to the
exact clips that captured them (or to Live if the event just happened
and isn't saved yet), trust the system not to waste recording budget on
empty rooms, and still pull up Live at any moment.

## Non-Goals

- **No object / person / face classification.** "Something moved" is the
  scope. A separate ADR can add ML-based classification later (would
  need a Coral USB or a much bigger SoC than the Zero 2W).
- **No cloud round-trip.** Detection runs entirely on the camera; the
  server is informed only of already-classified events.
- **No audio-triggered events.** The ZeroCam has no microphone and the
  Zero 2W WiFi stack already saturates under RTSP load.
- **No detection on the existing H.264 main stream.** Decoding the main
  stream on the Zero 2W just to difference frames wastes the encoder
  you already paid for. Detection works off a dedicated low-resolution
  analysis stream (see §3).
- **No per-user motion zone editor in this pass.** A single
  detector-wide sensitivity + a global "ignore top N %" mask is enough
  for MVP. Zone polygons are forward-compat in the payload (see §5) but
  the UI is deferred.

## Hardware Constraints

Grounding the design in what the RPi Zero 2W actually has.

| Resource        | Capacity                                                   | Headroom today (streaming 1080p25, idle CPU) |
|-----------------|------------------------------------------------------------|----------------------------------------------|
| CPU             | 4× Cortex-A53 @ 1 GHz, ARMv8, NEON                         | ~45 % idle across all cores                  |
| RAM             | 512 MB LPDDR2                                              | ~200 MB free after streamer + systemd stack  |
| Encoder         | VideoCore IV, H.264 up to 1080p30 (hardware)               | fully utilised when streaming                |
| Sensor          | OV5647 (PiHut ZeroCam) — 1080p30, 720p60, 480p90           | one consumer at a time (libcamera)           |
| Storage         | microSD, flash-only                                        | no persistent motion index on camera         |
| Power           | 5 V / 2.5 A; bus ~2 W streaming                            | no HW budget for a Coral/NPU dongle          |

Practical implications:

- Detection must cost **< 10 % of one core** at the chosen analysis
  framerate. That means frame-diff-plus-morphology on a small YUV
  array — not anything that decodes H.264 or runs a CNN.
- The sensor has exactly one consumer. We cannot run `libcamera-vid`
  (current pipeline) and `picamera2` at the same time. One of them
  owns the sensor; the other is out.
- RAM budget for a full YUV history ring is tight. A 5-second pre-roll
  at 320×240 grayscale is ~5 × 5 × 320 × 240 = 1.9 MB — fine. A
  5-second pre-roll at 1080p would be ~155 MB — not fine.
- Pushing motion events to the server reuses the existing HMAC heartbeat
  credentials (ADR-0016). No new auth surface.

## Context — what we reuse

The repository already has everything structural we need:

| Piece                             | File                                                                   | What we reuse                                                                                      |
|-----------------------------------|------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| Camera capture + encoder          | `app/camera/camera_streamer/stream.py`                                 | Replace `libcamera-vid` subprocess with Picamera2 dual-stream; keep ffmpeg RTSP push identical.     |
| Camera control / config           | `app/camera/camera_streamer/control.py`                                | Add `recording_mode` awareness; add analysis start/stop endpoints on the status server.             |
| Camera → server M2M channel       | `app/camera/camera_streamer/heartbeat.py`, `server_notifier.py`        | Reuse HMAC scheme for motion-event POSTs.                                                           |
| Server control client             | `app/server/monitor/services/camera_control_client.py`                 | Add `enable_motion_analysis(ip, params)` / `disable_motion_analysis(ip)`.                           |
| Scheduler                         | `app/server/monitor/services/recording_scheduler.py`                   | Replace the "motion → off" stub with real motion-mode policy.                                       |
| Camera M2M API                    | `app/server/monitor/api/cameras.py`                                    | Add `/api/v1/cameras/motion-event` HMAC endpoint (start + end in one payload, OR two events).       |
| Recording MP4 layout              | `app/server/monitor/services/streaming_service.py`, `recorder_service.py` | No layout change; motion events refer to clips by `(camera_id, start_time)` tuple.                  |
| Audit log                         | `app/server/monitor/services/audit.py`                                 | Emit `MOTION_DETECTED`, `MOTION_ENDED` as audit events.                                             |
| Data storage                      | `/data/config/*.json` (ADR-0002)                                       | New `/data/config/motion_events.json`, capped-size rolling store.                                    |
| Dashboard information architecture| ADR-0018                                                               | Add motion badge + event count per camera tile; add "motion" filter to Recordings page.             |
| Recording scheduler semantics     | ADR-0017 §6                                                            | `recording_mode = "motion"` becomes first-class, not a no-op.                                        |

## Decision Points (please sign off before implementation)

The rest of this doc assumes the following choices. Each is a real
trade-off; flag anything you want changed.

### D1. Capture pipeline — revised after Yocto reality check

**Original plan (kept for historical context):** replace `libcamera-vid`
with Picamera2 for a clean `(main H.264 + lores YUV)` dual-stream.

**Why revised:** Picamera2 is not packaged in the pinned scarthgap
meta-raspberrypi revision (2026-04-19 VM check). Adding it would mean
authoring a custom recipe + flipping `libcamera`'s PACKAGECONFIG[pycamera]
on via a bbappend — real work that belongs in a separate PR, and one
that changes the known-good streaming path.

**Adopted plan:** **ffmpeg-tee** instead.

The existing libcamera-vid process stays as-is (single sensor owner,
no change). The ffmpeg process it feeds already consumes the local TCP
H.264 stream; we make ffmpeg fan out to two outputs:

```
libcamera-vid (H.264, --listen) ─► tcp://127.0.0.1:8888
                                        │
                          ffmpeg -i tcp://127.0.0.1:8888 \
                                 -map 0:v -c copy -f rtsp ...    ───► server RTSP (unchanged)
                                 -map 0:v -vf scale=320:240,format=gray \
                                 -f rawvideo /tmp/lores.pipe
                                                                    │
                                                            Python reader thread
                                                                    │
                                                              MotionDetector
```

- Zero new sensor consumers, zero new TCP listeners, no new Python deps
  beyond `numpy`.
- Negligible extra CPU: the scale+format filter runs on decoded frames
  but ffmpeg already has decoded them (H.264 stays `copy` to server).
  Measured overhead budget on Zero 2W: ~5 %.
- Named FIFO + `rawvideo` keeps frames exactly grayscale-YUV400 at
  320×240, one frame = 76800 bytes — trivial to `numpy.frombuffer` in
  the reader thread.

Picamera2 + pre-roll (D5a) remain desirable but are explicitly deferred
to a follow-up cycle that can take on the recipe + bbappend work.

### D2. Detection algorithm: grayscale frame difference + morphology

- Grab `lores` YUV (we only use Y = luma), shrink to 320×240, convert
  to uint8 grayscale.
- Running mean background (EMA, α≈0.1) — cheap adaption to lighting
  drift, no opencv dependency.
- `diff = abs(frame - background)`; threshold at T (default 20 / 255).
- 3×3 box filter (or `numpy` 2-pass 1D sum) as cheap morphology.
- Count thresholded pixels → normalised score (0.0 – 1.0).
- Hysteresis: event starts when score > `start_threshold` for
  `min_start_frames` consecutive frames; ends when score <
  `end_threshold` for `min_end_frames` consecutive frames (default
  start=0.02, end=0.005, start_frames=3, end_frames=15).

We avoid opencv (big, slow link, +50 MB in the image). Pure numpy at
320×240 × 5 fps is ~3 ms/frame on a Cortex-A53 — well under budget.

### D3. Analysis framerate: 5 fps

Low enough to leave CPU for the H.264 encoder and Python GIL overhead;
high enough that a human walking through frame is caught for 3-5
frames. Configurable (1-10 fps) per camera.

### D4. Event model: start-event + update-event + end-event

Three HMAC POSTs per motion burst:

1. `POST /api/v1/cameras/motion-event` with `phase="start"` —
   camera fires this within one analysis frame of detection.
2. Optional `phase="update"` every 5 s for long-running events — carries
   the current peak score so the UI can show intensity in near-real time.
3. `phase="end"` when hysteresis ends the event. Payload carries the
   final duration and peak score.

Alternative (simpler): one POST at end-of-event only. Rejected because
notification latency matters — we want the dashboard to light up within
a second of a person walking in, not 30 seconds later when the event
finally closes.

### D5. Recording behaviour under `recording_mode = "motion"`

**Motion mode governs the recorder only. Live viewing is unchanged — it
stays on-demand exactly as it is for Off / Schedule / Continuous today
(ADR-0017). A viewer opening the Live tab always starts the camera H.264
encoder regardless of recording mode.**

The recorder has one extra trigger added to the existing set
(continuous, schedule-window): **"motion event active"**.

```
motion mode timeline (no viewer present)
                                  ┌─ motion start
                                  │          ┌─ motion end + post-roll
                                  │          │
analysis stream (lores, tiny):    ─always─on─always─on─always─on─ (~free)
H.264 main encoder:    ─── idle ──┴── running ─┴── idle ──
server recorder:                  └── running ─┘
                                  ▲            ▲
                                  │            └─ stop recorder after post-roll
                                  └─ start recorder + emit clip

motion mode timeline (viewer opens live mid-scene)
analysis stream:       ─always─on──────────────────────────
H.264 main encoder:    ─idle─┬── running ───────────────── (viewer keeps it up)
server recorder:             │    └── running ──┘ (motion triggered during live)
viewer arrival: ─────────────┘
```

- The **analysis stream** (lores 320×240 grayscale, ~free) is always
  running while `recording_mode = motion`. The detector's job.
- The **main H.264 encoder** starts when any of these are true —
  same-on-demand rules as today, plus one new trigger:
  - a viewer is watching Live (unchanged)
  - scheduler says "I need to record now" — for motion mode, that means
    a motion event is currently active
- Post-roll = 10 s: keep recording that long after motion ends
  (same grace pattern as ADR-0017 §5 on-demand close).
- Min event duration = 2 s: sub-2-s pulses (bug on lens, reflection)
  still get logged as events but don't trigger a clip.

**Pre-roll — decided D5a with D5b as kill-switch fallback.**

Default: camera keeps a 3-s H.264 ring-buffer in RAM while in motion
mode using Picamera2's `CircularOutput`. On motion trigger, the buffer
is flushed as leading bytes of the recorded clip so you see the moment
*before* detection fired. ~1.5 MB RAM continuously (trivial against
~200 MB free on the Zero 2W).

Fallback behind `MOTION_PREROLL_ENABLED=false` (config flag, ADR-0014
style dual-backend): no ring-buffer; clip starts cold at detection
time, ~3 s after real-world motion onset. Same discipline as the
`CAMERA_STREAM_BACKEND=cli` libcamera-vid fallback in D1 — if
`CircularOutput` turns out flaky on real Zero 2W hardware we flip the
flag without a rollback.

Either variant preserves live viewing. The difference is only whether
pre-roll footage exists in the recorded clip.

### D6. Storage: `/data/config/motion_events.json` — rolling cap

JSON-file discipline per ADR-0002. Cap = 5000 most recent events
globally; when exceeded, drop oldest 10 % (batched compaction to avoid
per-event full rewrites). Each record:

```json
{
  "id": "mot-20260419T143002Z-cam-d8ee",
  "camera_id": "cam-d8ee",
  "started_at": "2026-04-19T14:30:02Z",
  "ended_at":   "2026-04-19T14:30:17Z",
  "peak_score": 0.173,
  "pixels_changed_peak": 18420,
  "duration_seconds": 15,
  "clip_ref": {
    "camera_id": "cam-d8ee",
    "date": "2026-04-19",
    "filename": "20260419_142957.mp4",
    "offset_seconds": 5
  },
  "zones": [],
  "version": 1
}
```

`clip_ref` is populated by the server when (and only when) it can match
the event timestamp to a finalised clip on disk. Absence of `clip_ref`
drives the router's "go to Live" branch.

A future per-user zones editor populates `zones`; today it's empty.

### D7. UI surface (minimum)

- **Recordings page:** date picker already exists; add a **"Only with
  motion"** checkbox. The clip row renders a small red dot when
  `len(motion_events) > 0` and the scrubber bar shows ticks at each
  motion `offset_seconds`.
- **Dashboard tile:** add a `motion` badge (pulse) when an event is
  currently active; show a 24-h motion-event count under the camera
  name.
- **Camera settings:** extend the Recording tab — when `recording_mode
  = motion` is selected, show sensitivity slider (maps to
  `start_threshold`), pre-roll/post-roll numeric fields, and test
  button ("Simulate motion" → fires a synthetic event).
- **Audit log:** `MOTION_DETECTED` and `MOTION_ENDED` lines with
  camera_id, duration, peak score.

## Plan

### Phase 1 — Infrastructure swap (no user-visible change)

1. Add Picamera2 to the camera Yocto recipe +
   `app/camera/requirements.txt`. Confirm image size budget on a real
   Zero 2W build (must fit the 8 GB rootfs).
2. Replace `StreamManager._build_libcamera_ffmpeg_cmd()` with a
   Picamera2-driven encoder path that still emits H.264 to localhost
   TCP 8888; keep ffmpeg RTSP push identical so MediaMTX side is
   unchanged.
3. Ship the old libcamera-vid path behind `CAMERA_STREAM_BACKEND=cli`
   fallback; default `picamera2`.
4. Smoke test on 192.168.1.186 — confirm 1080p25 RTSPS to server with
   the new backend. Measure CPU + RAM before / after.

### Phase 2 — Motion detector (camera-side only, events not wired)

5. New module `camera_streamer/motion.py` with `MotionDetector`.
   Constructor-injected config (thresholds, fps). Two entry points:
   `process_frame(y_plane)` and `poll_event()` returning `None` / start /
   end transitions.
6. Wire `MotionDetector` into the capture loop: Picamera2 delivers
   `lores` arrays on a callback; we call `process_frame`.
7. Log detections to `camera-streamer` journal only — no server POSTs
   yet. Run the detector against a recorded test clip to validate the
   tuning.

### Phase 3 — Server events + data model

8. `MotionEvent` dataclass in `monitor/models.py`; `MotionEventStore`
   service wrapping `/data/config/motion_events.json`.
9. New HMAC endpoint `POST /api/v1/cameras/motion-event` — same HMAC
   scheme as heartbeat, same replay protection. Validates `phase`,
   `camera_id`, timestamps, score.
10. Server emits `MOTION_DETECTED` / `MOTION_ENDED` to `AuditLogger`.
11. Architecture fitness test: new HMAC route must be in
    `_M2M_ROUTE_NAMES` set (camera-side auth boundary) and have no
    `@login_required`.

### Phase 4 — Wire camera → server + scheduler policy

12. Camera: `motion.py` emits events via the existing
    `server_notifier` HMAC helper.
13. `RecordingScheduler`: `evaluate()` returns `True` for motion mode
    only while a motion event is active (feed via
    `MotionEventStore.is_camera_active()`). Add the 3 s pre-roll +
    10 s post-roll grace in `_reconcile_camera`.
14. `CameraControlClient.enable_motion_analysis(ip, params)` +
    `disable_motion_analysis(ip)` wrappers over a new control endpoint
    pair on the camera status server.
15. Server scheduler: whenever a camera is paired, enable motion
    analysis on the camera side regardless of `recording_mode` (events
    are always logged — see "Scope clarifications" table). The
    `recording_mode` only changes what the scheduler does with the
    events, not whether the camera produces them.

### Phase 5a — Event click-through resolver

16. New server route `GET /events/<event_id>` — HTML redirect endpoint
    applying the two-way rule:
    - finalised clip matches event timestamp →
      `302 /recordings?cam=<id>&date=<d>&file=<f>&seek=<offset>`
    - otherwise → `302 /live?cam=<id>`

    "Finalised" means a `.mp4` file (not `.mp4.part`) whose start /
    duration contain the event's server-side timestamp.
17. Recorder: write-then-rename pattern — output to `foo.mp4.part`,
    rename to `foo.mp4` on clean segment close. Clip-listing code
    (`recorder_service.list_clips`) already filters extensions; verify
    it also ignores `.part`.
18. Recordings page: accept `seek=<offset>` query param; auto-jump the
    `<video>` element to that second on load and highlight the event
    tick.
19. Live page: no change needed — existing on-demand path handles it.
20. Events list: each row's click handler points at `/events/<id>`.
21. Motion-event API handler: per-camera rate limit (≤1 `phase=start`
    per 20 s); excess returns 429 and does not persist.

### Phase 5 — UI + settings

22. Camera Settings → Recording tab: sensitivity slider, pre-roll /
    post-roll fields, test button.
23. Recordings page: filter checkbox + motion tick marks on scrubber.
24. Dashboard tile badge + 24-h event count.
25. Events list page (or panel) with per-row click-through to
    `/events/<id>` (see Phase 5a).
26. Documentation: update `docs/architecture.md` §8 and §8.0.1 tables;
    mark ADR-0017 "motion slot reserved" note as implemented.

### Phase 6 — Hardware validation

20. Real-hardware smoke (GCE VM → OTA push → Zero 2W 192.168.1.186)
    with ten synthetic and ten organic walk-through tests. Measure
    false-positive rate across 12 h of a typical room (lights, AC,
    daytime sun-patch).
21. Update `scripts/smoke-test.sh` with a motion assertion.

## Resumption

- **Current status:** design proposal written; awaiting sign-off on D1–D7.
- **Last completed step:** `docs/exec-plans/motion-detection.md` committed.
- **Next step:** Phase 1 — confirm Picamera2 is in the camera Yocto
  recipe or add it; measure image-size delta.
- **Branch / PR:** `feat/motion-detection` (local, not yet pushed).
- **Devices / environments:**
  - Camera: 192.168.1.186 (Zero 2W, IP 192.168.1.186, serial ending d8ee)
  - Server: 192.168.1.245 (RPi 4B)
  - Build VM: GCE yocto / europe-west2-c
- **Commands to resume:**
  - `git checkout feat/motion-detection`
  - `cd app/camera && python -m pytest tests/ -v`
  - Yocto: on build VM, `./scripts/build.sh camera-dev`
- **Open risks / blockers:**
  - Picamera2 on Yocto: confirm recipe exists or needs one (blocker for Phase 1).
  - Pre-roll implementation: `CircularOutput` API must be wired through the ffmpeg bridge without dropping I-frames (blocker for D5 full version).

## Validation

- **Unit (camera):** `pytest app/camera/tests/unit/test_motion.py`
  - deterministic synthetic YUV frames; assert start/end transitions
    match expected thresholds and hysteresis.
- **Unit (server):** `pytest app/server/tests/unit/test_motion_events.py`
  - store append/rotate/cap; `MotionEventStore.is_camera_active()` truth
    table; HMAC route signing correctness.
- **Integration:** `pytest app/server/tests/integration/test_motion_flow.py`
  - simulate camera posting start + end; assert audit log, scheduler
    reacts, event persisted, GET returns event.
- **Architecture fitness:** update
  `tests/contracts/test_architecture_fitness.py` — the new route must
  live in the HMAC M2M set, not session-auth.
- **Contract tests:** extend `openapi/server.yaml` with the motion
  routes; regenerate `openapi/camera.yaml` analysis endpoints.
- **Hardware smoke:** `scripts/smoke-test.sh` gains a motion check
  (trigger via IR LED or wave-hand rig).
- **Coverage gate:** stay ≥ 85 % server, ≥ 80 % camera (same as
  world-class suite thresholds).

## Risks

- **Picamera2 swap regresses streaming on real hardware.** Mitigation:
  dual backend + CAMERA_STREAM_BACKEND kill-switch for one release.
- **False positives from sun patches / headlight sweeps.** Mitigation:
  EMA background adaption + hysteresis; post-MVP knob for ignoring top
  N % of frame (sky / window region).
- **Event flood DoSing the server.** Mitigation: per-camera rate limit
  (max 1 start-event / 30 s) enforced in the HMAC route handler; camera
  already throttles by virtue of the end-hysteresis window.
- **Zero 2W RAM pressure.** Mitigation: numpy arrays sized exactly once
  (no per-frame allocation); CircularOutput pre-roll capped at 5 MB;
  measure with `systemd-cgtop` during smoke.
- **Sensor ownership bug between backends.** Mitigation: hard-fail if
  both `libcamera-vid` and Picamera2 are detected running at startup
  (mutually-exclusive process lock).
- **Design decision D5 (pre-roll) is expensive; D5-simple (continuous
  stream while mode=motion) is much easier.** If Phase 1 slips or
  Picamera2 CircularOutput integration proves painful, fall back to
  D5-simple and document the pre-roll gap as a known limitation.

## Deployment Impact

- **Yocto image:** Picamera2 package added to camera image. Run
  `bitbake -p` + a VM build before merging to main. Image size must
  still fit in the A/B 8 GB rootfs.
- **OTA:** normal SWUpdate path (ADR-0008, ADR-0020). No U-Boot changes.
- **Backwards compatibility:** cameras without the new code continue to
  work — the server's new scheduler policy treats a camera without the
  motion-event endpoint as "never detects motion" (equivalent to
  recording_mode = off), so the operator sees no worse behaviour than
  today.
- **Storage:** new file `/data/config/motion_events.json` (small, capped
  at ~5000 events ≈ 2 MB).
- **Docs:** updates to `architecture.md` §8, `requirements.md`,
  `testing-guide.md`. New ADR-0021 drafted after sign-off.

---

## Appendix A — Why on-camera and not on-server

The user asked for detection **on the camera**; this appendix records
why that's also the engineering-correct choice:

- **Bandwidth:** the entire reason ADR-0017 exists is that 24 × 7 RTSP
  push saturates home WiFi at ~10 cameras. Moving detection to the
  server means going back to always-on streaming — defeating ADR-0017.
- **Latency:** an event on-camera is detected within one analysis
  frame (~200 ms). On-server detection adds RTSP + decode latency
  (~1 s minimum).
- **Privacy posture:** less frame data crosses the network boundary;
  aligns with the "TLS on all connections, minimal raw video transit"
  stance in `architecture.md §3`.
- **Scales with cameras:** each camera carries its own ~5 % CPU cost;
  adding a camera doesn't increase server CPU pressure.

## Appendix B — Rejected alternatives

- **Server-side motion via ffmpeg motion vectors.** The motion vectors
  are accurate enough but require the server to receive and parse every
  stream — same 24 × 7 saturation problem as continuous mode.
- **External `motion` daemon on the camera.** Mature, but it owns the
  sensor and can't coexist with the libcamera streaming pipeline.
  Re-solving sensor arbitration to accommodate it is more work than
  the Picamera2 dual-stream approach.
- **ONVIF motion events.** OV5647 + libcamera does not expose ONVIF;
  we'd be writing an ONVIF server ourselves. Out of scope for MVP.
- **TensorFlow Lite person detector on the Zero 2W.** Technically
  possible (quantised MobileNetV2 runs ~2 fps on a Cortex-A53) but
  doubles the RAM budget and ships model weights in the OTA image.
  Deferred to a successor ADR if "person vs cat vs tree" becomes a
  product requirement.

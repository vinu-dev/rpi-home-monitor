# ADR-0021: Camera-Side Motion Detection

**Status:** Accepted
**Date:** 2026-04-20
**Deciders:** Vinu
**Relates to:** ADR-0006 (modular monolith), ADR-0009 (mTLS pairing), ADR-0016 (heartbeat protocol), ADR-0017 (on-demand streaming + recording modes), ADR-0018 (dashboard IA)

## Context

ADR-0017 introduced `recording_mode ∈ {off, continuous, schedule, motion}` but shipped `motion` as a **reserved enum value silently treated as off**. The UI showed a greyed-out "coming soon" radio. There was no detector, no event surface, no recorder integration.

The product ask is "I check the camera after a day — show me when something happened and let me jump into the video." Three design pressures:

1. **Where does detection run?** Server-side (decode every camera's RTSP, run an algorithm) costs ~150 ms/frame per camera on a Pi 4B and doesn't scale past ~3 cameras. Camera-side is free — it already owns a grayscale ISP stream.
2. **What becomes a motion event?** A first-class object (stored, queryable, surfaced in the UI timeline and audit log), not a transient tag on a clip. Clips get pruned; events should outlive them as bookmarks.
3. **What's the relationship to recording?** Motion events **always** log regardless of recording mode. Recording mode controls only whether video is written to disk; the event feed is the same under Off / Continuous / Schedule / Motion.

The original `stream.py` design asked ffmpeg to decode the RTSP stream on the camera side, scale to 320×240 grayscale, and pipe that to the detector. This introduced a ~20 s live-feed delay (software decode at 54 % of one core plus `os.pipe` backpressure coupling the RTSP copy output to the decode-for-motion path) and was rejected before landing on `main`.

## Decision

**Motion detection runs on the camera using Picamera2's native dual-stream ISP output. Events are first-class server objects. Recording mode decides whether video writes to disk; it does not gate event capture.**

### Pipeline

```
┌──────────────── CAMERA (Pi Zero 2W) ───────────────────────────┐
│                                                                │
│  OV5647 sensor                                                 │
│    │                                                           │
│    ▼                                                           │
│  libcamera / Picamera2                                         │
│    ├─ main  1920×1080 YUV ─► H264Encoder ─► ffmpeg -c copy ──► RTSPS push
│    │                                                           │
│    └─ lores  320×240  YUV ─► MotionDetector (2-frame diff)     │
│                               │                                │
│                               ▼ start / end transitions        │
│                              MotionEventPoster (HMAC)          │
│                               │                                │
└───────────────────────────────┼────────────────────────────────┘
                                │ HTTPS + HMAC-SHA256
                                ▼
┌──────────────── SERVER (Pi 4B) ────────────────────────────────┐
│  POST /api/v1/cameras/motion-event                             │
│    └─► MotionEventStore (JSON file, ADR-0002)                  │
│           └─► RecordingScheduler (ADR-0017) ←─ motion-mode poll│
│           └─► MotionClipCorrelator ──► clip_ref on disk match  │
│           └─► /events/<id> router ──► /recordings&seek= | /live│
└────────────────────────────────────────────────────────────────┘
```

### Detector (camera)

- **Algorithm:** two-frame absolute differencing (classic `motion` package / OpenCV basic-motion-detection recipe). Each frame compared to the one immediately before; no learned background model.
- **Why not MOG2 / MoG / KNN?** Benchmarked on the Zero 2W — MOG2 runs at ~27 ms/frame (7.4× headroom at 5 fps) and handles multimodal backgrounds (wind, leaves) correctly, but the in-process memory footprint (~30 MB for OpenCV + MOG2 state) plus Picamera2's 6-buffer dual-stream (~20 MB) plus ffmpeg plus the Python runtime pushed the 362 MB Zero 2W past OOM. Deferred to a future hardware refresh or server-side deployment on the Pi 4B. Documented in `docs/archive/exec-plans/motion-detection.md §OpenCV MOG2 exploration`.
- **Hysteresis:** start requires 2 consecutive frames above `start_score_threshold` (0.006), end requires 10 consecutive frames below `end_score_threshold` (0.002). Scene-specific tuning will become a Camera Settings sensitivity slider; current defaults are empirically separated from typical indoor sensor noise (~0.0015 floor).
- **Dual-stream:** main and lores share the sensor; lores is a free byproduct of the ISP. No additional CPU cost for the detector input.

### Transport

- Each phase transition posts JSON to `POST /api/v1/cameras/motion-event` with an HMAC-SHA256 signature over the body, timestamped, and keyed by the pairing secret (same scheme as the heartbeat, ADR-0016).
- Payload: `{phase: "start"|"end", event_id, started_at, peak_score, peak_pixels_changed, duration_seconds}`.
- The same `event_id` is reused across start + end so the server upserts.

### Server side

- **MotionEventStore** (`services/motion_event_store.py`) — JSON-file-backed append log with atomic writes (ADR-0002). 10 000-event cap with drop-oldest compaction. Two safety guards:
  1. **Auto-close on new start from same camera** — protects against missed "end" POSTs (camera reboot, network blip).
  2. **Reap stale open events** — background sweep every ~60 s closes events open for > 10 min.
- **MotionEventPoster endpoint** (`api/cameras.py`) — verifies HMAC, upserts.
- **MotionClipCorrelator** (`services/motion_clip_correlator.py`) — on `/events/<id>` click, scans the camera's recording directory (`<rec>/<cam>/*.mp4`) for a clip whose start + duration brackets the motion's `started_at`. On hit, 302 to `/recordings?...&seek=<offset>`. On miss, 302 to `/live?cam=...`.
- **RecordingScheduler** (`services/recording_scheduler.py`) evaluates per camera every 10 s. For `recording_mode="motion"`, `evaluate()` calls `MotionEventStore.is_camera_active(cam_id, post_roll_seconds=10.0)` — true while any event is open OR within 10 s of the last end. Recorder starts / stops accordingly.

### UI

- **Dashboard "Recent events"** — motion events appear alongside recording clips in one sorted feed. Wall-clock time (`14:23:05` / `Yesterday 14:23` / `Mon 14:23` / `Apr 15 14:23`). Click routes through `/events/<id>`.
- **`/events`** — dedicated page, same row vocabulary, filter chips, infinite scroll.
- **`/logs`** — audit surface for admins; motion events surface alongside auth + OTA events.
- **Settings → Recording mode** — `Motion` radio is live (the "coming soon" gate is gone) with an inline explainer so operators know what it does.

## Alternatives considered

### Server-side detection

Pull RTSP from every camera on the server and run MOG2 / YOLO there. Rejected: CPU scaling (ADR-0017 rationale), plus the detector would lose frames during an ffmpeg restart, plus a bigger blast radius (one server crash = every camera blind).

### ffmpeg `-filter:v` + pipe (original `stream.py` design)

Tee the RTSP H.264 into a second ffmpeg branch, decode + scale to 320×240 gray, pipe bytes to a Python reader. Rejected after hitting a 20 s live-feed delay on the Zero 2W (software decode + pipe backpressure). Documented in the exec plan's rejected-alternatives section.

### H.264 motion-vector readout (motion vectors baked into the encoded stream)

picamera legacy supported `motion_output=` on the encoder and returned `(x, y, sad)` tuples per macroblock — essentially zero-cost detection. **Investigated and blocked** on Pi Zero 2W: the VC4 hardware encoder path uses V4L2, which has no motion-vector sideband (confirmed on Raspberry Pi forums). The MMAL legacy path that supported this was retired. Would be viable on a Pi 5 (PiSP encoder exposes MVs) — parked for a future hardware refresh.

### Frame differencing as a one-shot vs continuous

Three-frame intersection (Lipton/Fujiyoshi 1998) was tried first. It rejects single-frame sensor spikes by requiring a pixel to differ from BOTH predecessors, but on the OV5647 at 5 fps the intersection stripped ~70 % of a real hand's motion area. Two-frame diff + hysteresis produced better real-world detection at the cost of needing a slightly higher `start_score_threshold` to reject single-frame noise.

## Consequences

### Positive

- **Sub-second live-feed latency.** ffmpeg runs `-c copy` only; no decode anywhere on the hot path.
- **Scales with cameras.** Detection cost stays on the camera that produced the frame; the server does O(events/second) work, not O(cameras × 25 fps).
- **Motion events outlive clips.** The audit log / events page shows every detection even after the covering clip has been pruned. Click falls through to `/live` when no clip exists.
- **Motion recording mode works** end-to-end without requiring any new recorder process — it reuses the continuous recorder gated by the scheduler.
- **Restart-safe.** Orphan events auto-close on the next start from the same camera; a reaper handles the never-reconnects case.

### Negative

- **Indoor / outdoor sensitivity is currently one global default.** Scenes with fans / monitors / swaying plants will either miss real motion (threshold too high) or false-fire on ambient change (threshold too low). A per-camera sensitivity slider in Settings is the next step; motion zones (polygon draw-on-snapshot) will follow.
- **2-frame diff will fire on wind-moved leaves outdoors.** Known limitation; the MOG2 path that handles multimodal backgrounds is blocked on the Zero 2W's RAM budget. Motion zones will mitigate by excluding the tree region.
- **Clip boundaries don't align with motion boundaries.** Recordings are segmented every 180 s; a motion event in the middle of a segment lands in that segment, not a dedicated clip. The click-through `seek=<offset>` compensates — the user lands inside the clip at the right moment.

### Neutral

- **Detection lives on the camera image**, so it ships as part of the Yocto camera build (picamera2 + videodev2 + libcamera-pycamera recipes added in `meta-home-monitor/recipes-multimedia/`). Server image is unchanged.

## Implementation

Shipped on branch `feat/motion-events-ui`. Key commits:

| Commit | Content |
|---|---|
| `20d2975` | MotionDetector + MotionEvent model + HMAC endpoint |
| `ef7a4ad` | Clip correlator, events API, `/events/<id>` router |
| `b5446ba` | Dashboard "Recent events" + `/events` page + `/logs` admin |
| `978abf1` | RecordingScheduler evaluates motion-mode windows |
| `2e1c743` | Picamera2 dual-stream backend (replaces ffmpeg-tee) |
| `7ebb7ac` | `_running` flag race fix in the lores capture thread |
| `17ac640` | Recorder `.mp4.part` bug + fragmented-mp4 flags |
| `c73723a` | Recorder uses `TZ=UTC` so clip filenames match event timestamps |
| `22f2617` | Dashboard motion rows route through `/events/<id>` (not `/live`) |
| `1c2e2e9` | Orphan auto-close + stale-event reaper |
| `8132b27` | Algorithm swap: custom EMA → standard 2-frame differencing |
| `0214f22`, `0dd63ef`, `709a66b` | Wall-clock time in UI + threshold tuning |
| `43b6d68` | Enable the Motion radio in Settings |

Yocto image additions (camera build):

- `meta-home-monitor/recipes-multimedia/libcamera/libcamera_%.bbappend` — enables `pycamera` PACKAGECONFIG.
- `meta-home-monitor/recipes-multimedia/picamera2/python3-picamera2_0.3.34.bb` — Picamera2 with lazy-import patch so optional deps (simplejpeg / piexif / pidng / PIL / PyAV / DRM / Qt previews) are opt-in.
- `meta-home-monitor/recipes-multimedia/videodev2/python3-videodev2_0.0.4.bb` — required by picamera2's H264Encoder.
- `meta-home-monitor/conf/machine/home-monitor-camera.conf` — ships `ov5647.dtbo` + `imx219.dtbo` overlays for one-line sensor swaps.

## Open items (not blocking)

- Motion zones (polygon draw-on-snapshot) for outdoor deployment.
- Per-camera sensitivity slider in Settings → Recording.
- Post-roll + segment-length sliders in Settings.
- 12 h false-positive soak at the deployment site.
- MOG2 / MoG when hardware or server-side deployment allows.

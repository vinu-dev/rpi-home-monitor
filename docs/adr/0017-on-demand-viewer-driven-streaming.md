# ADR-0017: On-Demand, Viewer-Driven Streaming + Recording Modes

**Status:** Accepted (shipped v1.1.0, 2026-04-13)
**Date:** 2026-04-17
**Deciders:** vinu-dev

---

## Context

### Problem

Today every paired camera pushes RTSP to the server 24 × 7, whether or not
anyone is watching and whether or not recording is required:

1. `lifecycle._do_running()` (camera) starts `libcamera-vid | ffmpeg` on
   boot and keeps it alive unconditionally.
2. `StreamingService` (server) spawns a per-camera HLS muxer ffmpeg and a
   30-second snapshot-respawn thread the moment a camera pairs, and keeps
   them running forever.
3. MediaMTX relays the RTSP push whether anyone consumes the WebRTC /
   HLS output or not.

At a nominal 4 Mbps this is ~43 GB/day/camera of Wi-Fi + disk + CPU — all
wasted when nobody is looking at the dashboard and the user has not opted
in to recording. With a 10-camera home target (see mission), 24 × 7
streaming saturates home Wi-Fi and burns SD-card write endurance for no
user benefit.

There is also no user-facing control over *when* to record. Tapo and
UniFi cameras let the owner choose Off / Continuous / Schedule / Motion;
this system has no equivalent — it effectively records continuously if
any recorder is hooked up, with no schedule.

### Goal

- Idle Wi-Fi traffic from a camera → server drops to ~0 when no dashboard
  is open AND `recording_mode = off` (or outside a schedule window).
- Live view starts within ~3–5 s of a user opening a tile (bounded by
  libcamera sensor + ffmpeg keyframe warm-up).
- User chooses per camera: `Off` / `Continuous` / `Schedule`
  (Motion slot reserved in the data model, disabled in the UI).
- Loop recording on the inserted media: oldest segments auto-deleted when
  free space falls below a low-watermark.
- No hardware changes; no new protocols; no new auth surface.

### Non-Goals

- Dual-encoder sub-stream / main-stream split on the camera. Keeping a
  single H.264 encoder keeps us on known-good libcamera territory; the
  on-demand win already addresses the bandwidth problem without it.
- Motion detection logic. The UI exposes a disabled Motion option so the
  data model is future-proof, but no detector ships in this change.
- Remote (off-LAN) viewing — requires TURN and belongs in a separate ADR.
- Per-camera timezone. Schedule windows evaluate in the server's system
  timezone; surfaced read-only in the UI.

---

## Decision

### Overall shape

```
Viewer (browser)        Server                    MediaMTX           Camera
     │                    │                         │                  │
     │ open /live/<id>    │                         │                  │
     │───────────────────>│  (WebRTC page)          │                  │
     │<─── html + JS ─────│                         │                  │
     │                    │                         │                  │
     │ WHEP POST ──────────────────────────────────>│                  │
     │                    │                         │ runOnDemand hook │
     │                    │  /internal/on-demand/<id>/start            │
     │                    │<──────── curl ──────────│                  │
     │                    │                         │                  │
     │                    │ POST /control/stream/start (HMAC, mTLS)    │
     │                    │───────────────────────────────────────────>│
     │                    │                         │                  │ libcamera+ffmpeg
     │                    │                         │<── RTSP push ────│ starts
     │<──── WebRTC frames ─────────────────────────│                  │
     │                    │                         │                  │
     │ close tab          │                         │ (no readers)     │
     │                    │                         │ runOnDemandClose │
     │                    │  /internal/on-demand/<id>/stop             │
     │                    │<──────── curl ──────────│                  │
     │                    │                         │                  │
     │                    │  coordinator asks:                         │
     │                    │   does scheduler still need the stream?    │
     │                    │    - yes → no-op                           │
     │                    │    - no  → POST /control/stream/stop ────>│ ffmpeg stops
```

Recording continues to ride on the same camera-push stream; when a
schedule is active, the scheduler is the thing that "needs" the stream
and the coordinator keeps the camera streaming even with no viewer.

### 1. Persisted desired stream state on the camera

- New file: `/data/config/stream_state` — one line, either `running` or
  `stopped`.
- Default on first boot: `stopped`. A freshly paired camera does not
  start streaming until something (viewer or scheduler) asks it to.
- `lifecycle._do_running()` reads the file on entry and either spawns
  the stream pipeline or parks in an idle wait loop.
- Every explicit start/stop command from the server rewrites the file
  atomically (tempfile + `os.replace`) so a power cut can't lose intent
  nor resurrect a stopped camera on reboot.

### 2. Camera control endpoints

Two new HMAC-authenticated endpoints on `status_server.py`, same scheme
as ADR-0015 `config-notify`:

| Method | Path                                 | Body        | Response                      |
|--------|--------------------------------------|-------------|-------------------------------|
| POST   | `/api/v1/control/stream/start`       | `{}`        | `{"state": "running"}`        |
| POST   | `/api/v1/control/stream/stop`        | `{}`        | `{"state": "stopped"}`        |

Both are idempotent: calling `start` on an already-running camera is a
no-op that returns the current state. `ControlHandler.set_stream_state`
is the single code path, persists the file in §1, and signals
`lifecycle`.

### 3. Heartbeat payload additions

ADR-0016 heartbeat gains two fields (server-tolerant of missing values
for legacy cameras):

```json
{
  "stream_state": "running",
  "recording_state": {
    "mode": "schedule",
    "recording_now": true
  }
}
```

The camera does not itself schedule recordings — `recording_state` is
the mirror the server last told the camera to expect, echoed back so the
server can detect drift (same pattern as `config_sync`).

### 4. Server control client

`CameraControlClient` gains `start_stream(ip)` / `stop_stream(ip)` —
thin wrappers over the existing HMAC POST helper.

### 5. On-demand coordinator

- New blueprint bound to **127.0.0.1 only**, no auth (localhost trust):
  - `POST /internal/on-demand/<camera_id>/start`
  - `POST /internal/on-demand/<camera_id>/stop`
- MediaMTX invokes these via a shell wrapper
  `/opt/monitor/bin/mediamtx-on-demand.sh <id> <start|stop>` hooked as
  `runOnDemand` / `runOnDemandCloseAfter` (15 s grace).
- `start` handler: idempotent; if camera already `running`, no-op;
  otherwise calls `CameraControlClient.start_stream`.
- `stop` handler: consults `RecordingScheduler.needs_stream(camera_id)`.
  If the scheduler still wants the stream (continuous or in-window
  schedule), no-op. Otherwise calls `stop_stream`.
- The coordinator is the single "anyone still need this?" gate — no
  race between scheduler and viewer-close.

If MediaMTX version actually only exposes `runOnConnect` / `runOnReady`,
we hook those equivalently; the coordinator API is the stable surface.

### 6. Recording mode policy

New fields on the `Camera` model (stored in `cameras.json`, ADR-0002):

| Field                         | Type     | Values / shape                                              |
|-------------------------------|----------|-------------------------------------------------------------|
| `recording_mode`              | `str`    | `"off"` \| `"continuous"` \| `"schedule"` \| `"motion"`     |
| `recording_schedule`          | `list`   | `[{"days": ["mon","tue",...], "start": "HH:MM", "end": "HH:MM"}, ...]` |
| `recording_motion_enabled`    | `bool`   | reserved for future motion ADR; default `false`             |

`recording_mode = "motion"` is accepted by the API for forward compat
but treated as "off" by the scheduler until the motion ADR lands.

### 7. RecordingScheduler service

- Daemon thread, 60 s tick (overlaps with heartbeat cadence; good
  enough for minute-precision schedule windows).
- For each camera:
  - Compute `wanted = mode is continuous, OR (mode is schedule AND
    now-in-window)`. Overnight windows (`end < start`) handled by
    splitting into `[start, 24:00) ∪ [00:00, end)`.
  - If `wanted` and no recorder ffmpeg running → start one, AND if the
    camera's `stream_state == stopped` call `start_stream(ip)`.
  - If not `wanted` and a recorder is running → stop it; then call
    the coordinator's stop path (which will no-op if a viewer is
    active).
- Recorder ffmpeg command: `-c copy` RTSP → segmented MP4 in
  `/media/recordings/<cam-id>/` — no re-encoding, bit-for-bit copy.
- Persists no extra state; recomputes from `cameras.json` every tick so
  mode changes propagate within one minute.

### 8. LoopRecorder service

- Daemon thread, 60 s tick.
- Scans `/media/recordings/`; if `free_percent < low_watermark`
  (default 10 %, configurable), deletes oldest segment files until
  `free_percent >= low_watermark + hysteresis` (default 5 %).
- Emits `RECORDING_ROTATED` audit events per deletion.
- Never deletes the currently-writing segment.

### 9. Live transport

Live view is served via **WebRTC WHEP** (ADR-0005 primary) from
MediaMTX. The server's old per-camera HLS muxer and 30-s snapshot
respawn are deleted. A single long-lived ffmpeg per camera pulls one
snapshot every 30 s while the RTSP stream is available (`-update 1`);
when the camera is idle, the last snapshot persists on disk and the
dashboard tile shows a "last seen Xs ago" label over the stale frame.

LL-HLS remains the documented fallback (ADR-0005) for browsers that
fail WHEP negotiation; the client-side player tries WHEP first, then
falls back.

---

## Alternatives Considered

### Keep always-on streaming, add "pause when idle" later

Rejected: the costs compound as cameras are added. With 10 cameras at
4 Mbps each the LAN saturates a typical 2.4 GHz AP; SD-card writes on
the server bound monthly write volume even with no viewers. The
symmetric fix ("idle = off") is cleaner than layering throttles.

### Camera-side schedule evaluation

Rejected: introduces clock-skew bugs, requires NTP discipline per
camera, and needs the camera to know the user-configured schedule
(either via push config or polling). The server already knows the
schedule, already has reliable time, and already has the control
channel. Server-side evaluation is strictly simpler.

### Separate sub-stream for live, main-stream for record

Rejected for MVP: libcamera multi-stream + dual ffmpeg pipelines is a
real hardware-risk area. Single-encoder + on-demand already solves the
bandwidth problem for the 10-camera home target. Re-visit when motion
detection (ADR-TBD) needs a continuous low-res analysis stream.

### MQTT control bus instead of per-camera HTTPS

Rejected: ADR-0015 already standardises HTTP + HMAC for the control
channel; adding a broker is additional infrastructure with no win at
10-camera scale. The new endpoints reuse the existing HMAC scheme and
mTLS socket.

### Recording on the camera, pulled on demand

Rejected: SD cards on the camera are smaller and harder to service;
users expect the "server collects footage" shape. Also defeats loop
recording on a user-inserted storage medium (USB / external SD on the
server) which is the shipping design.

---

## Consequences

### Positive

- Idle bandwidth from a camera drops from ~4 Mbps to ~600 bytes every
  15 s (just the heartbeat).
- Recorder policy is user-visible and matches consumer camera UX.
- Loop recording guarantees the disk never fills; user does not have
  to manually prune.
- Single-encoder hardware path unchanged — lowest-risk route to the
  user-visible behaviour change.
- Schedule evaluation lives entirely on the server — no camera-clock
  bugs to chase.

### Negative / Trade-offs

- Live-view first-frame is ~3–5 s instead of sub-second, because the
  encoder has to warm up on each viewer arrival. Acceptable for a home
  monitor; documented in the settings UI.
- MediaMTX `runOnDemand` hook semantics depend on version — plan
  validates at implementation time and falls back to `runOnReady` or a
  server-owned WHEP proxy shim if needed.
- Recording continuously still writes ~43 GB/day/camera at 4 Mbps. Loop
  policy bounds total bytes, but SD endurance is still the user's
  concern — recommend high-endurance cards in docs.
- One more background thread on the server (`RecordingScheduler`) plus
  one (`LoopRecorder`). Within the "small number of service threads"
  budget set by ADR-0003.

---

## Implementation

| Component                           | File                                                                       |
|-------------------------------------|----------------------------------------------------------------------------|
| Camera stream control               | `app/camera/camera_streamer/control.py`                                    |
| Camera control HTTP surface         | `app/camera/camera_streamer/status_server.py`                              |
| Camera lifecycle honours state file | `app/camera/camera_streamer/lifecycle.py`                                  |
| Persisted stream state              | `/data/config/stream_state` (atomic write)                                 |
| Camera heartbeat extension          | `app/camera/camera_streamer/heartbeat.py`                                  |
| Server control client               | `app/server/monitor/services/camera_control_client.py`                     |
| Server streaming rewrite            | `app/server/monitor/services/streaming_service.py`                         |
| Recording scheduler (new)           | `app/server/monitor/services/recording_scheduler.py`                       |
| Loop recorder (new)                 | `app/server/monitor/services/loop_recorder.py`                             |
| On-demand coordinator (new)         | `app/server/monitor/api/on_demand.py`                                      |
| MediaMTX hook script                | `meta-home-monitor/recipes-multimedia/mediamtx/files/mediamtx-on-demand.sh`|
| MediaMTX config                     | `meta-home-monitor/recipes-multimedia/mediamtx/files/mediamtx.yml`         |
| Camera model fields                 | `app/server/monitor/models.py`                                             |
| REST validation                     | `app/server/monitor/api/cameras.py`                                        |
| Live page (WebRTC WHEP)             | `app/server/monitor/templates/live.html`                                   |
| Settings: Recording tab             | `app/server/monitor/templates/camera_settings.html`                        |
| Settings: Storage tab               | `app/server/monitor/templates/settings.html`                               |
| OpenAPI updates                     | `app/server/monitor/openapi/*.yaml`, `app/camera/.../openapi.yaml`         |
| Camera tests                        | `app/camera/tests/unit/test_control.py`, `test_stream.py`, `test_heartbeat.py` |
| Server tests (new + updated)        | `app/server/tests/unit/test_streaming.py`, `test_recording_scheduler.py`, `test_loop_recorder.py`, `test_on_demand_coordinator.py` |
| Integration tests                   | `app/server/tests/integration/test_on_demand_flow.py` (new)                |
| Docs                                | `docs/architecture.md`, `docs/requirements.md`, `docs/exec-plans/on-demand-streaming.md` |

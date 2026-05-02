# On-Demand Streaming + Recording Modes — Exec Plan

Version: 1.0
Date: 2026-04-17
Status: Active
Owner: vinu-dev
Assistant: Claude Sonnet 4.6
Branch: `feat/on-demand-streaming`

---

## Goal

Eliminate always-on camera → server streaming when no one is watching and
no recording is required, and give the user Tapo-style control over *when*
cameras record (Off / Continuous / Schedule). Keep the design simple enough
for a 10-camera home deployment while leaving room for motion-triggered
recording later.

User-visible outcomes:

- Idle Wi-Fi traffic drops to near zero when `recording_mode = off` and no
  viewer is open.
- Live view first-frame within ~3-5 s of clicking a tile (sensor + encoder
  warm-up is the floor).
- Recording modes surfaced in per-camera settings (Off / Continuous /
  Schedule), schedule editor in 24 × 7 grid form.
- Loop recording on the inserted media — oldest segments auto-deleted
  when free space drops below a low-watermark.

## Non-Goals

- Dual-encoder sub-stream / main-stream split on the camera. Deferred:
  adds real hardware risk (libcamera multi-stream), not needed at 10
  cameras when recording is the dominant always-on cost anyway.
- Motion detection and motion-triggered recording. The UI exposes a
  disabled "Motion" option so the data shape is future-proof, but no
  detection logic ships in this PR.
- Remote (off-LAN) live view. WebRTC over the open internet needs TURN;
  that's a separate ADR.
- Timezone configurability. Schedule uses the server's system timezone.
  Surfaced in the UI as read-only for now.
- Database migration. ADR-0002 says JSON files; this PR respects that —
  schedule + recording mode are stored as JSON fields on the camera row.

## Constraints

- **Hardware**: unchanged. Same camera Pi + server Pi. No new tiers.
- **Control-plane**: stays HTTP + HMAC (ADR-0015). No MQTT broker.
- **Heartbeat**: stays 15 s HTTP POST (ADR-0016). Payload gains
  `stream_state` and `recording_state` fields.
- **Storage**: JSON only (ADR-0002). Recording-mode + schedule added to
  the existing `cameras.json` row, not a new store.
- **Transcoding**: forbidden. `-c copy` end-to-end.
- **Security**: reuse existing mTLS + HMAC. No new auth surface.
- **Coverage gates**: server ≥ 80 %, camera ≥ 70 % (pyproject.toml).
- **Single PR**: one coherent change, one branch, merged together.

## Context

Files that matter (from the codebase survey):

### Camera

- `app/camera/camera_streamer/stream.py` — single libcamera-vid + ffmpeg
  pipeline; already has `start()` / `stop()` / `is_streaming`. Adding
  remote start/stop is a thin control surface over what's there.
- `app/camera/camera_streamer/control.py` — `ControlHandler`, HMAC auth,
  already has `set_config` / `get_status`. Extend with
  `set_stream_state(desired)`.
- `app/camera/camera_streamer/heartbeat.py` — already builds
  `stream_config`; extend payload with `stream_state`.
- `app/camera/camera_streamer/lifecycle.py` — `_do_running()` starts
  stream unconditionally today. Change to honour a persisted
  "desired stream state" (default: stopped; last command wins) so the
  camera boots idle unless someone has asked for live recently.

### Server

- `app/server/monitor/services/streaming_service.py` — rewrite the HLS
  muxer path; drop always-on HLS ffmpeg; drop 30 s snapshot respawn.
- `app/server/monitor/services/camera_control_client.py` — add
  `start_stream(camera_ip)` / `stop_stream(camera_ip)`.
- `app/server/monitor/services/` — new `recording_scheduler.py` and
  `loop_recorder.py`.
- `app/server/monitor/api/cameras.py` — extend recording-mode field on
  `PUT /cameras/<id>` (validation), add
  `POST /internal/on-demand/<id>/{start,stop}` (localhost-only, called
  by MediaMTX `runOnDemand`).
- `app/server/monitor/models.py` — add `recording_mode`,
  `recording_schedule`, `recording_motion_enabled` to `Camera`.
- `meta-home-monitor/recipes-multimedia/mediamtx/files/mediamtx.yml` —
  add `runOnDemand` on the camera path + grace window.
- `app/server/monitor/templates/live.html`, `dashboard.html`, settings
  templates — WebRTC WHEP for live, recording settings tab.
- `app/server/monitor/openapi/*.yaml` — contract updates.

### Tests

- `app/camera/tests/unit/test_control.py`, `test_stream.py`,
  `test_heartbeat.py` — extend.
- `app/server/tests/unit/test_streaming.py`,
  `test_camera_service.py`, `test_camera_control_client.py` — rewrite.
- New: `test_recording_scheduler.py`, `test_loop_recorder.py`,
  `test_on_demand_coordinator.py`.

### Docs

- `docs/history/adr/0017-on-demand-viewer-driven-streaming.md` — new ADR.
- `docs/history/adr/README.md` — new ADR index (none today).
- `docs/history/baseline/architecture.md` — streaming section update.
- `docs/history/baseline/requirements.md` — recording-mode requirements.

## Plan

### 1. Discovery & design (complete)

- [x] Read rules in `docs/ai/`.
- [x] Survey current streaming / recording code.
- [x] Confirm ADR-0002 (JSON storage), ADR-0005 (WebRTC primary),
  ADR-0015 (control channel), ADR-0016 (heartbeat) all align with the
  proposed design.
- [x] Scope down from dual-encoder to single-stream-on-demand for MVP.

### 2. ADR + index

- [x] Write `docs/history/adr/0017-on-demand-viewer-driven-streaming.md` using
  the format established by 0015 / 0016.
- [x] Create `docs/history/adr/README.md` index covering 0001-0017.

### 3. Camera firmware

- [x] Add `set_stream_state(desired: "started"|"stopped")` to
  `ControlHandler` with HMAC auth, idempotent, returns current state.
- [x] Expose new endpoints in `status_server.py`:
  `POST /api/v1/control/stream/start`, `POST /api/v1/control/stream/stop`.
- [x] Persist desired state to `/data/config/stream_state` so a reboot
  during idle doesn't auto-resume streaming and blow up the bandwidth
  budget. Default on first boot = stopped.
- [x] `lifecycle._do_running()` reads persisted desired state; honours
  it on boot and whenever state changes.
- [x] Heartbeat payload gains:
  `stream_state: "running"|"stopped"`,
  `recording_state: {"mode": "off|continuous|schedule|motion", "recording_now": bool}`
  (the camera itself doesn't schedule — the recording_state mirror is
  server-sourced so the heartbeat can confirm server ↔ camera are in
  sync, just like `config_sync`).
- [x] Unit + integration tests for all of the above.

### 4. Server control client

- [x] `CameraControlClient.start_stream(ip)` /
  `CameraControlClient.stop_stream(ip)` — thin wrappers over
  existing HMAC POST pattern.
- [x] Unit tests.

### 5. Server streaming service rewrite

- [x] Delete per-camera always-on HLS muxer ffmpeg; live view routes
  via MediaMTX WHEP URL directly.
- [x] Delete the 30 s snapshot respawn thread; replace with a single
  long-lived ffmpeg per camera reading the RTSP stream when it's
  available, writing `snapshot.jpg` every 30 s with `-update 1`.
  When the camera isn't streaming (on-demand idle), the last snapshot
  stays on disk until the camera reconnects — dashboards show the
  stale frame with a `last seen Xs ago` label.
- [x] The recorder ffmpeg is now managed by the recording-mode policy
  (see §6), not unconditionally started.
- [x] Watchdog logic kept but amended: a recorder process that's
  *deliberately* stopped (because mode = off or outside schedule) is
  NOT restarted. State tracked via a small per-camera control object.
- [x] Unit tests.

### 6. Recording mode policy

- [x] Add to `Camera` model: `recording_mode` (off / continuous /
  schedule / motion — motion disabled), `recording_schedule` (list of
  `{days, start, end}`), `recording_motion_enabled` (bool, unused for
  now).
- [x] Extend `CameraService.update_camera()` to validate + persist
  these fields.
- [x] New service: `RecordingScheduler` — background thread, 60 s
  tick, per camera decides "recording wanted now?" based on mode +
  schedule + current wall time in server TZ, starts/stops the
  recorder ffmpeg accordingly. Gracefully handles mode changes
  mid-tick.
- [x] Wire the scheduler into `app.__init__` startup.
- [x] Unit tests covering: off never records, continuous always
  records, schedule honours time windows including overnight
  (`end < start`), mode change mid-interval flips correctly.

### 7. On-demand coordinator

- [x] New endpoint `POST /internal/on-demand/<id>/{start,stop}` —
  bound to 127.0.0.1 only (enforced by Flask blueprint + nginx
  config), no auth (localhost trust).
- [x] Shell script `/opt/monitor/bin/mediamtx-on-demand.sh` that
  MediaMTX invokes for `runOnDemand`; curls the internal endpoint.
- [x] Coordinator logic: on viewer start → call camera
  `stream_start`; on close-after grace → if scheduler doesn't need
  the stream for recording, call camera `stream_stop`. Otherwise
  leave it running. This is the central "is anyone still needing
  this stream?" gate.
- [x] Unit tests.

### 8. MediaMTX config

- [x] Amend `mediamtx.yml`: per-path `runOnDemand:` + `runOnDemandCloseAfter: 15s`
  (if MediaMTX version supports it; otherwise use `runOnDemandPublisher`).
  Investigate which hook fires: we want "on first reader" (WebRTC viewer
  opens), not "on first publisher". `sourceOnDemand` fits if we change
  to a pull model; for push model, MediaMTX emits a `runOnConnect` /
  `runOnReady` event we can hook differently. **Needs hardware
  verification (step 13) to confirm which hook MediaMTX 1.11.3 fires
  on the running box.**

### 9. Loop recorder

- [x] New service: `LoopRecorder` — scans media mount every 60 s, if
  free space below watermark (default 10 %), deletes oldest recording
  segments until free space back above watermark + hysteresis.
- [x] Emits `RECORDING_ROTATED` audit event per deletion.
- [x] Unit tests with a temp-dir fixture.

### 10. Dashboard + settings UI

- [x] Live page: WebRTC WHEP player via nginx `/webrtc/<id>/whep`,
  falling back to HLS (HLS.js or Safari native) on WHEP negotiation
  failure. Added "Starting stream... (camera warming up)" overlay for
  the on-demand warm-up (~3-5 s).
- [x] Dashboard tiles: show cached snapshot + "live"-pulse badge when
  `stream_state == running` (from heartbeat); falls back to legacy
  `streaming` boolean for older cameras. Clicking tile routes to
  `/live/<id>` which triggers the on-demand start via MediaMTX hook.
- [x] New settings tab per camera: **Recording**
  - radio: Off / Continuous / Schedule / Motion (disabled, "coming soon")
  - if Schedule: window editor (7 day rows, checkbox + start/end time
    inputs, `+ Window` button per day, &times; to remove)
  - 24 × 7 grid preview of when recording will be active
  - read-only timezone display (server TZ)
- [x] Settings tab: **Storage** — augmented with mount path,
  total/used/free GB, oldest/newest segment timestamps, low-watermark
  (percent), hysteresis (percent), Save button. Round-trips through
  `/api/v1/settings`.

### 11. OpenAPI + contracts

- [x] Add the three new camera control endpoints
  (`/api/v1/control/stream/{start,stop,state}`) to camera OpenAPI; add
  `Heartbeat` schema with `stream_state` + `recording_state`. Bumped
  `info.version` to `1.1`.
- [x] Add `/internal/on-demand/{id}/{start,stop}` to server OpenAPI
  marked `x-internal: true`, plus `PUT /api/v1/cameras/{id}` with a
  new `CameraUpdate` request schema mirroring the server's
  validation. Bumped `info.version` to `1.1`.
- [x] Add new fields to `Camera` schema: `recording_mode`,
  `recording_schedule`, `recording_motion_enabled`,
  `desired_stream_state`, `stream_state`.
- [x] Contract tests pass.

### 12. Tests (full sweep)

- [ ] Unit: camera stream control, heartbeat payload, recording
  scheduler, loop recorder, on-demand coordinator.
- [ ] Integration: viewer opens → main starts → viewer closes → main
  stops; recorder lifecycle across mode changes.
- [ ] Contract: OpenAPI.
- [ ] Security: new endpoints have the expected auth (HMAC for
  camera, localhost-only for internal).
- [ ] Regression: everything that was green on main still green.

### 13. Hardware verification

- [ ] Deploy to `<server-ip>` (server) + `<camera-ip>` (camera).
- [ ] Confirm idle: recording = off, no dashboard open → camera is
  not streaming, `iftop` on server shows zero RTSP traffic from
  camera IP.
- [ ] Open live page → first-frame latency measured, recorded in PR
  body.
- [ ] Set recording = schedule (10-minute window 5 min from now) →
  confirm recorder starts at top of window, stops at end, segments
  land on disk, scheduler log entries present.
- [ ] Fill disk to 90 % → confirm loop recorder deletes oldest.
- [ ] Full regression pass on existing pair / unpair flow.

### 14. Docs

- [ ] Update `docs/history/baseline/architecture.md` streaming section.
- [ ] Update `docs/history/baseline/requirements.md` with recording-mode behaviour.
- [ ] ADR-0017 finalised with implementation section pointing at the
  real file paths.
- [ ] ADR index entry.

### 15. Ship

- [ ] Commit in logical slices (not one mega-commit), push, open PR
  with test-plan checklist + hardware verification log + screenshots
  of the new settings UI.

## Resumption

- Current status: exec plan written, about to write ADR-0017.
- Last completed step: repo survey + scope decision (single-stream
  MVP, dual-encoder deferred).
- Next step: ADR-0017 draft.
- Branch: `feat/on-demand-streaming` off `main` @ `f0d941a`.
- Devices: camera `<camera-ip>`, server `<server-ip>`.
- Commands to resume:
  ```bash
  cd <workspace>
  git checkout feat/on-demand-streaming
  git log --oneline -5
  ```
- Open blockers: validate MediaMTX `runOnDemand` vs `runOnConnect`
  hook semantics against the version we ship (step 8).

## Validation

Local:
```bash
cd app/camera && python -m pytest -q
cd app/server && python -m pytest -q
ruff check app/camera app/server
ruff format --check app/camera app/server
python scripts/ai/validate_repo_ai_setup.py
python scripts/ai/check_doc_links.py
```

Coverage gates (enforced by CI):
```bash
cd app/camera && python -m pytest --cov-fail-under=70
cd app/server && python -m pytest --cov-fail-under=80
```

Hardware:
```bash
bash scripts/deploy-dev-app.sh --camera <camera-ip> --server <server-ip>
ssh root@<server-ip> 'iftop -i eth0 -f "host <camera-ip>"'  # idle → ~0
# open https://<server>/live/cam-xxx → first-frame measured with
#   browser devtools network tab
```

## Risks

1. **MediaMTX `runOnDemand` is publisher-side, not reader-side.** If
   the hook fires only on first publish, not first subscribe, the
   on-demand trigger won't fit the push model where the camera is the
   publisher. Mitigation: validate early (step 8); fall back to an
   alternate design — camera is always *registered* but quiescent;
   MediaMTX `runOnReady` or an nginx-level hook on WHEP endpoints
   triggers the start call. Worst case: a server-owned websocket on
   the live page triggers the start directly, no MediaMTX hook
   needed. Each fallback is smaller than the primary path.
2. **Camera reboot while streaming.** After the SIGTERM watchdog fix,
   reboot is reliable, but the camera must come back up respecting
   its last persisted `desired_stream_state`. Default to `stopped`
   on first boot; persist every explicit change. Unit tested.
3. **SD-card write wear**. Continuous recording at 4 Mbps = 43 GB/day.
   Loop recorder bounds total bytes written to ≈ disk size per retention
   cycle, but raw bitrate is still the same. User accepts this: loop
   policy with size-bounded retention is what they asked for. Document
   recommended high-endurance card in `docs/guides/hardware-setup.md`.
4. **Schedule clock drift on camera.** Schedule evaluation is entirely
   server-side; camera isn't asked to know local time. Eliminates
   whole class of clock-skew bugs.
5. **Race between scheduler wanting the stream and on-demand wanting
   it off.** Solved by the coordinator's "anyone still need this?"
   gate — stop is only issued if neither the scheduler nor a live
   viewer currently needs the stream.
6. **Existing E2E tests** expect the HLS `<video>` element in
   `live.html`. WebRTC swap will break them. Plan: update E2E in the
   same PR, not separately.

## Completion Criteria

- [ ] PR merged to `main`.
- [ ] ADR-0017 accepted.
- [ ] All camera + server tests green, coverage gates met.
- [ ] Hardware evidence in the PR: idle `iftop` zero, live
  first-frame ≤ 5 s, schedule window honoured, loop deletion
  observed.
- [ ] `docs/history/baseline/architecture.md` reflects the new flow.
- [ ] No regressions on pair / unpair / config-push.

---

## Change log

- 2026-04-17: v1.0 drafted. Scope locked to single-stream MVP
  (dual-encoder deferred). Plan approved for execution.
- 2026-04-17: Steps 2-11 landed on branch `feat/on-demand-streaming`.
  Camera firmware (control endpoints, persisted stream state,
  lifecycle integration, heartbeat extension) and server services
  (control client, streaming service rewrite, `RecordingScheduler`,
  `LoopRecorder`, on-demand coordinator, MediaMTX hook) are in and
  unit + integration tests are green. Docs + UI slice landed same
  day: `openapi/{server,camera}.yaml` bumped to 1.1 with the new
  schemas + paths; `live.html` advertises the on-demand warm-up;
  dashboard tile badge reads `stream_state` then falls back to
  `streaming`; settings grew a **Recording** tab (per-camera mode +
  schedule + 24×7 preview) and extended the **Storage** tab with
  loop-recording watermark / hysteresis and oldest/newest segment.
  Hardware verification (step 13) and PR (step 15) still pending.

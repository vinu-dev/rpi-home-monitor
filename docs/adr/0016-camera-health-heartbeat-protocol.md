# ADR-0016: Camera Health and Heartbeat Protocol

**Status:** Accepted
**Date:** 2026-04-16
**Deciders:** vinu-dev

---

## Context

### Problem

The server could display incorrect camera state indefinitely.
A camera that crashed, lost WiFi, or ran out of battery would still appear as
`status=online` and `streaming=true` on the dashboard because:

1. `last_seen` was only updated from mDNS (Avahi) announcements — unreliable
   in some environments, and not tied to actual stream activity.
2. `DiscoveryService.check_offline()` existed but was never called
   automatically, so cameras were never marked offline at runtime.
3. No live streaming state was tracked — the server inferred streaming from
   whether an ffmpeg process was running, not from what the camera reported.

Users saw "streaming" on the dashboard even when no frames were arriving,
leading to confusion and missed alerts.

### Goal

- Server must **never** show `streaming=true` for a camera that has not
  reported itself as streaming within the last 45 seconds.
- Server must mark a camera `offline` automatically when no contact is made
  within 30 seconds.
- Status must be driven by what the camera says, not by what the server
  last remembered.

---

## Decision

### Protocol overview

Two complementary mechanisms work together:

```
Camera                               Server
  │                                    │
  │  POST /heartbeat every ~15s        │
  │  (HMAC-SHA256 signed)              │
  ├───────────────────────────────────>│  update last_seen, streaming,
  │                                    │  cpu_temp, memory_percent
  │<─── 200 {ok:true}                 │
  │     OR 200 {ok:true,              │  (pending_config included when
  │            pending_config:{...}}   │   server has unsent config push)
  │                                    │
  │  GET /api/v1/control/status        │
  │<───────────────────────────────────│  server polls camera on-demand
  │  {streaming, config, health}       │  (via CameraControlClient, mTLS)
  │───────────────────────────────────>│
  │                                    │
  │  (background, every 10s)           │
  │                                    │◄── staleness checker
  │                                    │    if now - last_seen > 30s:
  │                                    │      status = offline
  │                                    │      streaming = False
```

### Camera → Server: Heartbeat

**Endpoint:** `POST /api/v1/cameras/heartbeat`
**Auth:** HMAC-SHA256 — same scheme as `config-notify` (ADR-0015):
  `signature = HMAC(secret, camera_id:timestamp:sha256(body))`
**Frequency:** Every 15 seconds (±3s random jitter to spread load)
**Timeout:** 300-second replay window (same as `config-notify`)

**Request payload:**
```json
{
  "camera_id": "cam-a1b2",
  "timestamp": 1712345678,
  "streaming": true,
  "cpu_temp": 48.5,
  "memory_percent": 42,
  "uptime_seconds": 3600,
  "stream_config": {
    "width": 1920, "height": 1080, "fps": 25,
    "bitrate": 4000000, "h264_profile": "high",
    "keyframe_interval": 30, "rotation": 0,
    "hflip": false, "vflip": false
  }
}
```

**Server processing:**
1. Verify HMAC and timestamp freshness.
2. If `config_sync != "pending"`: accept `stream_config` and mark synced.
3. If `config_sync == "pending"`: keep server's stored params (they are the
   desired state) and return them in the response for the camera to apply.
4. Update: `status=online`, `last_seen=now`, `streaming`, health fields.
5. Audit `CAMERA_ONLINE` if camera was previously `offline`.
6. Save to store.

**Response — normal:**
```json
{"ok": true}
```

**Response — with pending config:**
```json
{
  "ok": true,
  "pending_config": {
    "width": 1280, "height": 720, "fps": 30,
    "bitrate": 2000000, "h264_profile": "main",
    "keyframe_interval": 30, "rotation": 0,
    "hflip": false, "vflip": false
  }
}
```

When `pending_config` is returned, the camera applies it immediately
(via `ControlHandler.set_config(..., origin="server")`). The `origin=server`
flag prevents a ping-pong notification back to the server.

### Server → Camera: On-demand Status Query

`CameraControlClient.get_status(camera_ip)` calls
`GET /api/v1/control/status` on the camera's status server (mTLS auth).

Returns:
```json
{
  "streaming": true,
  "config": { ... },
  "stream_manager_running": true
}
```

This endpoint already existed as part of ADR-0015 and is available for
server-initiated checks (e.g., after a failed heartbeat, on dashboard load).

### Server Staleness Checker

A daemon thread started in `_startup()` runs every 10 seconds:

```python
app.discovery_service.check_offline()
```

`check_offline()` iterates online cameras and marks any with
`now - last_seen > OFFLINE_TIMEOUT (30s)` as `offline`, also setting
`streaming = False`. The 30s timeout means at most 2 missed heartbeats
(each 15s) before a camera is declared offline — fast enough to surface
real outages without false positives on a busy LAN.

### Data model changes

Four new fields on `Camera`:

| Field              | Type    | Source     | Notes                              |
|--------------------|---------|------------|------------------------------------|
| `streaming`        | `bool`  | heartbeat  | Cleared to `False` on offline      |
| `cpu_temp`         | `float` | heartbeat  | °C, displayed on dashboard         |
| `memory_percent`   | `int`   | heartbeat  | 0–100, displayed on dashboard      |
| `uptime_seconds`   | `int`   | heartbeat  | Seconds since last camera boot     |

### Dashboard changes

- `streaming` badge (green `● streaming` / gray `○ not streaming`) per camera.
- `last_seen` shown as relative time ("3s ago", "2m ago") with warning colour
  when stale (>30s orange, >45s red).
- CPU temp and memory % from the most recent heartbeat.
- Camera list refresh interval reduced from 30s → 15s to stay in sync with
  heartbeat cadence.

---

## Alternatives Considered

### Server polls camera periodically instead of camera pushing

Rejected: requires server to maintain per-camera timers and open outbound
connections to each camera IP. Harder to scale and requires the server to
know when each camera booted. Push is simpler and already used for
`config-notify`.

### WebSocket or SSE for streaming status

Rejected: the camera's status server is a minimal `http.server`-based
implementation. Adding WebSocket/SSE would significantly complicate it.
Periodic short-lived HTTPS requests are adequate and match the existing
mTLS-authenticated control channel pattern.

### mDNS alone for liveness

Rejected: mDNS (Avahi) announces every 30s by default and is not a reliable
liveness signal. It also does not carry streaming state or health metrics.

---

## Consequences

### Positive

- Server status is always fresh — camera must actively check in.
- Stale data problem eliminated: `streaming=False` is the safe default;
  only set to `True` by a recent, authenticated heartbeat.
- Pending config pushes are reliably delivered even if the camera was
  temporarily unreachable when the admin changed a setting.
- Dashboard shows actionable health info (CPU temp, memory) without any
  additional API call.
- Consistent HMAC auth reuses the pattern from ADR-0015, no new key material.

### Negative / Trade-offs

- ~15s of extra LAN traffic per camera (small: ~600 bytes per heartbeat).
- Camera must be paired to heartbeat (no heartbeat while in `pending` state).
- 30-second stale threshold means a very brief WiFi glitch (~15–30s) could
  flip a camera to `offline` unnecessarily. Acceptable for a home monitor.

---

## Implementation

| Component | File |
|-----------|------|
| Camera heartbeat sender | `app/camera/camera_streamer/heartbeat.py` (new) |
| Lifecycle hook | `app/camera/camera_streamer/lifecycle.py` |
| Server heartbeat endpoint | `app/server/monitor/api/cameras.py` |
| Server service method | `app/server/monitor/services/camera_service.py` |
| Staleness checker wiring | `app/server/monitor/__init__.py` |
| Streaming flag on offline | `app/server/monitor/services/discovery.py` |
| Data model | `app/server/monitor/models.py` |
| Dashboard | `app/server/monitor/templates/dashboard.html` |
| CSS | `app/server/monitor/static/css/style.css` |
| Camera tests | `app/camera/tests/unit/test_heartbeat.py` (new) |
| Server unit tests | `app/server/tests/unit/test_camera_service.py` |
| Server contract tests | `app/server/tests/contracts/test_api_contracts.py` |
| Staleness tests | `app/server/tests/unit/test_svc_discovery.py` |

## See also

- [ADR-0017](0017-on-demand-viewer-driven-streaming.md) — extends the
  heartbeat payload with `stream_state` and `recording_state` fields so
  the server can detect drift between desired and actual stream state.

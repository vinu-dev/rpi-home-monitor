# ADR-0015: Server-to-Camera Control Channel

**Status:** Proposed
**Date:** 2026-04-15
**Deciders:** Vinu
**Relates to:** ADR-0004 (camera lifecycle), ADR-0006 (modular monolith), ADR-0009 (mTLS pairing)

## Context

The server can receive video from paired cameras (RTSPS push) and display health status, but it **cannot send commands back**. The server stores camera settings (resolution, fps, recording_mode) in `cameras.json`, but changes never reach the actual camera. An admin who changes resolution on the dashboard is editing a local database entry — the camera keeps streaming at its original settings.

This gap blocks several product goals:

- **Remote camera configuration** — change resolution, framerate, bitrate, rotation from the dashboard without logging into each camera individually
- **Coordinated recording** — server tells camera to switch to a lower resolution when disk is full, or to a higher one when motion is detected (Phase 2)
- **Fleet management** — apply settings across multiple cameras from one place (Phase 2)
- **Live image controls** — brightness, contrast, saturation, exposure adjustments without restarting the stream

### What we need

A private, authenticated channel where the server (RPi 4B) can push configuration to the camera (RPi Zero 2W) and receive confirmation of the result.

## Research Summary

### Industry patterns

| System | How it controls cameras |
|--------|------------------------|
| **Frigate NVR** | Does NOT push settings. Consumes whatever stream the camera already provides. Camera config is done through the camera's own ONVIF/web UI. |
| **Synology Surveillance Station** | Same — camera's own UI for encoding settings. NVR selects from available streams. |
| **Blue Iris** | Same pattern. Camera owners set resolution on the camera. |
| **ONVIF (Profile S/T)** | SOAP/XML over HTTP. Controls resolution, encoding, bitrate, PTZ, imaging. Designed for vendor interoperability across thousands of camera models. |
| **ESPHome** | Hub (Home Assistant) connects to device's TCP server, pushes commands, receives state. Hub-initiated, device-executes. |
| **Tasmota** | MQTT pub/sub — hub publishes to `cmnd/<device>/COMMAND`, device subscribes and executes. |
| **Home Assistant** | Mix of HTTP REST, MQTT, WebSocket depending on integration. |

### Protocol comparison (for LAN-only embedded Linux)

| Protocol | Pros | Cons | Fit |
|----------|------|------|-----|
| **HTTP REST** | Already have HTTPS server on camera (port 443), Python stdlib, mTLS ready, no new dependencies | Synchronous, no server-push notification | Best |
| **MQTT** | Pub/sub, async, QoS levels | Requires broker process (mosquitto), new dependency, complexity for 1-4 cameras | Overkill |
| **CoAP** | Low overhead, UDP-based | Loses TCP reliability, new library, no mTLS (uses DTLS) | Poor fit |
| **gRPC** | Typed APIs, streaming | Heavy runtime for Zero 2W (512 MB), protobuf dependency | Overkill |
| **WebSocket** | Persistent connection, bidirectional | Camera's Python `http.server` doesn't support it, would need library | Unnecessary |
| **RTSP SET_PARAMETER** | In-band with video | Rarely implemented, MediaMTX doesn't support it, read-only SDP | Not viable |

### libcamera parameter changeability

| Parameter type | Can change at runtime? | Interruption |
|----------------|----------------------|--------------|
| **Controls** (brightness, contrast, saturation, exposure, AWB, sharpness, gain) | Yes — `set_controls()` per-frame | None |
| **Configuration** (resolution, sensor mode, framerate, pixel format) | Requires stop/reconfigure/start | ~1-2s stream gap |

Since we use `libcamera-vid` (CLI, not picamera2 library), all parameter changes require restarting the `libcamera-vid` process. The stream gap is ~2-5s (sensor init + TCP reconnect + ffmpeg probe).

## Decision

### 1. Protocol: HTTP REST on the existing camera HTTPS server

Add new API endpoints to the camera's existing status server (`status_server.py`, port 443). The server (RPi 4B) pushes configuration by making HTTPS requests to the camera. No new protocols, no broker, no additional ports.

**Why this is the right choice:**

- Camera already runs an HTTPS server with session auth and self-signed TLS
- Both devices are on the same LAN with sub-millisecond latency
- The ESPHome pattern (hub connects to device's server) maps directly to our architecture
- Adding REST endpoints to `status_server.py` costs zero new infrastructure
- mTLS can be layered on for the control channel (server presents its cert, camera verifies against CA)

### 2. Authentication: mTLS with server certificate verification

The control channel uses **mutual TLS** — the same trust infrastructure established during pairing (ADR-0009):

- **Server → Camera requests:** Server presents its `server.crt` (signed by server CA). Camera verifies against `ca.crt` received during pairing.
- **No session cookies needed:** Certificate identity replaces username/password for machine-to-machine calls.
- **Human admin access** (via camera status page) continues using password + session cookies on the same server.

This means the camera's HTTPS server accepts two authentication methods:
1. **Session cookie** — for human admin via browser (existing)
2. **mTLS client certificate** — for server control channel (new)

Request routing:

```
Camera HTTPS (port 443)
  ├── /login, /, /status, /api/status, /api/wifi, /api/password
  │     → session cookie auth (human admin, existing)
  ├── /pair
  │     → PIN auth (pairing ceremony, existing)
  └── /api/v1/control/*
        → mTLS auth (server only, new)
```

### 3. Camera-side API endpoints

```
GET  /api/v1/control/config
     → Returns current camera configuration (all parameters)
     Auth: mTLS (server cert)

PUT  /api/v1/control/config
     Body: {"width": 1920, "height": 1080, "fps": 25, "bitrate": 4000000, ...}
     → Validates parameters, applies changes, restarts stream if needed
     → Returns: {"applied": {...}, "restart_required": true, "status": "ok"}
     Auth: mTLS (server cert)

GET  /api/v1/control/capabilities
     → Returns supported parameter ranges for this camera hardware
     → {"width": [640, 1280, 1920], "height": [480, 720, 1080],
        "fps": {"min": 1, "max": 30}, "bitrate": {"min": 500000, "max": 8000000}, ...}
     Auth: mTLS (server cert)

GET  /api/v1/control/status
     → Returns live operational status (streaming, health, errors)
     → Superset of existing /api/status with stream pipeline details
     Auth: mTLS (server cert)

POST /api/v1/control/restart-stream
     → Force restart of the streaming pipeline (no config change)
     Auth: mTLS (server cert)
```

### 4. Controllable parameters

#### Stream parameters (require pipeline restart, ~2-5s gap)

| Parameter | Key | Type | Values | Default |
|-----------|-----|------|--------|---------|
| Width | `width` | int | 640, 1280, 1920 | 1920 |
| Height | `height` | int | 480, 720, 1080 | 1080 |
| Framerate | `fps` | int | 1-30 | 25 |
| Bitrate | `bitrate` | int | 500000-8000000 (bps) | 4000000 |
| H.264 profile | `h264_profile` | string | "baseline", "main", "high" | "high" |
| Keyframe interval | `keyframe_interval` | int | 1-120 (frames) | 30 |
| Rotation | `rotation` | int | 0, 180 | 0 |
| Horizontal flip | `hflip` | bool | true/false | false |
| Vertical flip | `vflip` | bool | true/false | false |

#### Image controls (future, when migrating to picamera2 — no restart)

| Parameter | Key | Type | Range | Default |
|-----------|-----|------|-------|---------|
| Brightness | `brightness` | float | -1.0 to 1.0 | 0.0 |
| Contrast | `contrast` | float | 0.0 to 2.0 | 1.0 |
| Saturation | `saturation` | float | 0.0 to 2.0 | 1.0 |
| Sharpness | `sharpness` | float | 0.0 to 2.0 | 1.0 |
| Auto white balance | `awb_mode` | string | "auto", "daylight", "cloudy", ... | "auto" |
| Exposure mode | `exposure_mode` | string | "auto", "short", "long" | "auto" |

Image controls are Phase 2 (require picamera2 migration). Documenting them now for forward compatibility.

### 5. Server-side integration

The server's `CameraService.update()` method currently saves settings to `cameras.json` but never pushes them. The change:

```python
# In camera_service.py update() — after saving to store:
if self._needs_camera_push(data):
    result = self._push_config_to_camera(camera, data)
    if not result.ok:
        # Save succeeded, push failed — mark camera as "config_pending"
        camera.config_sync = "pending"
        self._store.save_camera(camera)
```

New `CameraControlClient` service (server-side):

```python
class CameraControlClient:
    """Push configuration to cameras via their control API.

    Uses server mTLS credentials to authenticate with the camera.
    """
    def __init__(self, cert_service):
        self._cert_service = cert_service

    def get_config(self, camera_ip: str) -> dict:
        """GET /api/v1/control/config with mTLS."""

    def set_config(self, camera_ip: str, params: dict) -> dict:
        """PUT /api/v1/control/config with mTLS."""

    def get_capabilities(self, camera_ip: str) -> dict:
        """GET /api/v1/control/capabilities with mTLS."""
```

### 6. Config sync model

Camera configuration has a single source of truth: **the camera itself**. The server stores a cached copy for display and for pushing desired state.

```
Admin changes resolution on dashboard
  → Server saves desired state to cameras.json
  → Server pushes PUT /api/v1/control/config to camera
  → Camera validates, applies, persists to camera.conf
  → Camera responds with applied config
  → Server updates cameras.json with confirmed state
  → If push fails: server marks config_sync="pending", retries on next health check
```

**Conflict resolution:** Camera always wins. If the camera rejects a parameter (e.g., unsupported resolution for its sensor), the server accepts the rejection and updates its stored value to match what the camera reports.

### 7. Security hardening

| Measure | Implementation |
|---------|---------------|
| **mTLS authentication** | Camera verifies server cert against `ca.crt` from pairing. Only the paired server can control this camera. |
| **Input validation** | Whitelist of allowed parameter names and value ranges. Reject unknown fields. |
| **Rate limiting** | Max 1 config change per 5 seconds per camera. Prevents rapid restart loops. |
| **Audit logging** | Camera logs every config change to `/data/logs/control.log` (timestamp, parameter, old → new, requester cert CN). Server logs to `/data/logs/audit.log`. |
| **Replay protection** | Include monotonic `request_id` (incrementing integer) in each request. Camera rejects requests with `request_id` <= last seen. Reset on reboot is acceptable (mTLS prevents replay across sessions). |
| **No escalation** | Control endpoints cannot change WiFi, passwords, or trigger factory reset. Those remain admin-password-protected only. |

### 8. Camera-side implementation sketch

New module: `app/camera/camera_streamer/control.py`

```python
class ControlHandler:
    """Handles server control API requests.

    Validates mTLS client certificate, parses parameters,
    applies changes to ConfigManager, restarts stream if needed.
    """
    def __init__(self, config, stream_manager, capabilities):
        self._config = config
        self._stream = stream_manager
        self._capabilities = capabilities
        self._last_request_id = 0
        self._last_change_time = 0
        self._rate_limit_seconds = 5
```

Integration into `status_server.py`:

```python
# In StatusHandler.do_GET / do_PUT:
if self.path.startswith("/api/v1/control/"):
    if not self._require_mtls():  # verify client cert
        return
    # Route to ControlHandler
```

mTLS verification in the camera's HTTPS server:

```python
def _require_mtls(self):
    """Verify the request comes from a client with a cert signed by our CA."""
    # ssl.SSLSocket.getpeercert() returns cert info if client presented one
    # Camera's SSL context needs: ctx.verify_mode = ssl.CERT_OPTIONAL
    # (CERT_OPTIONAL so browser clients without certs still work on other endpoints)
    peer_cert = self.request.getpeercert()
    if not peer_cert:
        self._json_response({"error": "Client certificate required"}, 401)
        return False
    # Verify issuer matches our CA (defense in depth — TLS already verified chain)
    return True
```

### 9. Network and firewall considerations

The camera's nftables firewall (ADR-0009) already allows inbound HTTPS (port 443) from the server IP. No firewall changes needed.

```
# Existing camera nftables rule:
tcp dport 443 ip saddr $SERVER_IP accept  # status page + control API
```

### 10. Parameter validation rules

```python
STREAM_PARAMS = {
    "width":              {"type": int, "allowed": [640, 1280, 1920]},
    "height":             {"type": int, "allowed": [480, 720, 1080]},
    "fps":                {"type": int, "min": 1, "max": 30},
    "bitrate":            {"type": int, "min": 500000, "max": 8000000},
    "h264_profile":       {"type": str, "allowed": ["baseline", "main", "high"]},
    "keyframe_interval":  {"type": int, "min": 1, "max": 120},
    "rotation":           {"type": int, "allowed": [0, 180]},
    "hflip":              {"type": bool},
    "vflip":              {"type": bool},
}

RESOLUTION_PAIRS = {
    (640, 480), (1280, 720), (1920, 1080),
}
```

Width and height must form a valid resolution pair. Sending `{"width": 1920, "height": 480}` is rejected.

### 11. Stream restart behavior

When a stream parameter changes:

1. Camera receives `PUT /api/v1/control/config`
2. Validates all parameters
3. Persists new values to `camera.conf`
4. Calls `stream_manager.stop()`
5. Waits for ffmpeg + libcamera-vid to terminate (~1s)
6. Calls `stream_manager.start()` (picks up new config values)
7. Returns response to server
8. Total interruption: ~2-5s (sensor init 3s + ffmpeg probe 1-2s)

The server should expect the RTSPS stream to drop and reconnect. MediaMTX handles this gracefully — the stream source disappears and reappears. HLS clients see a brief stall; WebRTC clients reconnect automatically.

## Alternatives Considered

### A. MQTT broker between server and cameras

Adds mosquitto process, configuration, another port (1883/8883), TLS setup for MQTT, and a subscription model. For 1-4 cameras on a LAN with <1ms latency, pub/sub adds complexity without benefit. MQTT shines at scale (100+ devices, fan-out, unreliable networks) — none of which apply here.

### B. Camera polls server for config changes

Camera periodically GETs desired config from server. Simpler for the camera (no inbound connections needed), but: increases latency (polling interval), wastes bandwidth, and the camera already accepts inbound HTTPS. Polling is the right pattern when devices are behind NAT or firewalls — our cameras are on the same LAN with firewall rules already allowing server access.

### C. ONVIF implementation

Would provide interoperability with third-party NVRs, but ONVIF is a SOAP/XML protocol with complex WSDL schemas. We control both endpoints — the interop benefit is zero. ONVIF implementation effort is disproportionate to the value for a system with 1-4 custom cameras.

### D. WebSocket persistent connection

Provides server-push and bidirectional communication. But the camera's `http.server.HTTPServer` doesn't support WebSocket upgrade — would need `websockets` library or a switch to a framework. REST request/response is sufficient for configuration push (not a real-time streaming use case).

### E. gRPC with protobuf

Strongly typed APIs with code generation. But: adds protobuf dependency, `grpcio` is heavy (~30 MB) for Zero 2W's constrained storage, and HTTP REST with JSON is equally expressive for our parameter set. gRPC's streaming RPCs would be useful if we needed continuous telemetry — but health monitoring already works via mDNS + polling.

## Consequences

### Positive

- **Zero new infrastructure** — reuses existing HTTPS server, mTLS certs, port 443
- **Familiar pattern** — REST API consistent with existing camera and server endpoints
- **Strong auth** — mTLS means only the paired server can control the camera
- **Auditable** — every change logged on both sides
- **Forward-compatible** — image controls (brightness, etc.) added later with same API
- **Testable** — HTTP endpoints are trivially testable with `pytest` + `requests`

### Negative

- **~2-5s stream gap on parameter changes** — unavoidable with libcamera-vid CLI (mitigated: users expect brief interruption when changing resolution)
- **Camera must be reachable** — server needs network path to camera IP (already true for health checks; blocked if camera is offline → config_sync="pending")
- **Two auth methods on one server** — session cookies (humans) + mTLS (server). Adds routing complexity in `status_server.py` (mitigated: clear URL prefix separation `/api/v1/control/*`)
- **Server stores cached copy** — config can drift if camera is modified directly via its status page (mitigated: server refreshes from camera on health check cycle)

## Implementation Plan

### Phase 1 (this PR)

1. Add `control.py` module to camera with parameter validation and stream restart logic
2. Add mTLS verification to camera's HTTPS server (CERT_OPTIONAL mode)
3. Add `/api/v1/control/config`, `/api/v1/control/capabilities`, `/api/v1/control/status` endpoints
4. Add `CameraControlClient` to server
5. Wire `CameraService.update()` to push config to camera after saving
6. Add `config_sync` field to Camera model ("synced", "pending", "error")
7. Add rate limiting and audit logging
8. Tests: unit + integration for both sides, contract tests for the API

### Phase 2 (future)

- Image controls (brightness, contrast, etc.) after picamera2 migration
- Bulk config push (apply settings to all cameras)
- Config sync indicator on dashboard (green check / yellow warning)
- Server-initiated stream quality adaptation (auto-lower resolution when disk is low)

## File Changes (Estimated)

| File | Change |
|------|--------|
| `app/camera/camera_streamer/control.py` | **New** — ControlHandler, validation, capabilities |
| `app/camera/camera_streamer/status_server.py` | Add mTLS support, route `/api/v1/control/*` |
| `app/camera/camera_streamer/stream.py` | Add `restart()` method |
| `app/camera/camera_streamer/config.py` | Add bitrate, h264_profile, rotation, flip params to DEFAULTS |
| `app/server/monitor/services/camera_control_client.py` | **New** — HTTP client with mTLS |
| `app/server/monitor/services/camera_service.py` | Push config on update, sync state tracking |
| `app/server/monitor/models.py` | Add `config_sync` field to Camera |
| `app/camera/camera_streamer/server_notifier.py` | **New** — HMAC-signed camera→server config push |
| `app/camera/camera_streamer/templates/status.html` | Editable stream settings form |
| `app/server/monitor/api/cameras.py` | `POST /config-notify` endpoint with HMAC auth |
| `app/camera/tests/` | Unit + contract tests for control API + notifier |
| `app/server/tests/` | Unit + contract tests for control client + config-notify |

## 8. Bidirectional Sync

### Camera→Server Notification

When the camera admin changes stream settings via the camera's own status page,
the camera notifies the server using HMAC-SHA256 authentication:

1. Camera applies config locally via `ControlHandler.set_config(origin="local")`
2. Camera POSTs to `https://<server_ip>/api/v1/cameras/config-notify`
3. Request signed with `HMAC-SHA256(pairing_secret, camera_id:timestamp:sha256(body))`
4. Server verifies HMAC + 5-minute timestamp window
5. Server updates stored camera config, marks `config_sync=synced`

### Ping-Pong Prevention

`ControlHandler.set_config()` accepts an `origin` parameter:
- `origin="server"` (default) — change came from server push, no notification
- `origin="local"` — change from camera GUI, triggers server notification

### Conflict Resolution

Camera is always source of truth. If both sides change simultaneously,
the camera's rate limiter (5s cooldown) rejects the server push. The camera's
local change succeeds and its notification overwrites the server's stored copy.

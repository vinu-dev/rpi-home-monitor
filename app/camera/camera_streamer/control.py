"""
Camera control API handler — processes configuration commands from the server.

The server pushes stream parameter changes via HTTPS to this handler.
Authentication is via mTLS (server presents its certificate signed by
the same CA used during pairing). See ADR-0015.

Controllable parameters (all require stream pipeline restart):
  width, height, fps, bitrate, h264_profile, keyframe_interval,
  rotation, hflip, vflip

The set of valid (width, height) and the per-resolution max framerate
come from ``camera_streamer.sensor_info`` — the connected sensor is
identified at construction time and its catalogued modes are used as
the validation table. This module no longer hardcodes OV5647 modes;
plugging in an IMX219 / IMX477 / IMX708 surfaces a different mode set
to the server and the dashboard.

Stream start/stop control (ADR-0017):
The handler also owns the persisted desired stream state at
``/data/config/stream_state`` (one line: ``running`` or ``stopped``).
The server drives start/stop via HMAC-authenticated HTTP endpoints; the
camera writes the file atomically so a power cut cannot lose intent or
resurrect a stopped camera on reboot.
"""

import json
import logging
import os
import tempfile
import time

from camera_streamer.sensor_info import (
    KNOWN_SENSOR_MODES,
    SensorCapabilities,
    detect_sensor_capabilities,
)

log = logging.getLogger("camera-streamer.control")


# Parameter schema with sensor-independent bounds. Per-resolution and
# per-fps validation comes from the live ``SensorCapabilities`` rather
# than this static schema. The ``allowed`` lists for ``width`` /
# ``height`` are derived from the sensor's modes inside
# ``ControlHandler``; the schema entries below are placeholders kept so
# the validation loop has a uniform structure.
PARAM_SCHEMA: dict[str, dict] = {
    "width": {"type": int},
    "height": {"type": int},
    "fps": {"type": int, "min": 1},
    "bitrate": {"type": int, "min": 500000, "max": 8000000},
    "h264_profile": {"type": str, "allowed": ["baseline", "main", "high"]},
    "keyframe_interval": {"type": int, "min": 1, "max": 120},
    "rotation": {"type": int, "allowed": [0, 180]},
    "hflip": {"type": bool},
    "vflip": {"type": bool},
    # Motion sensitivity — 1 (least) to 10 (most). Mapped to MotionDetector
    # thresholds at motion-pipeline start. See config.motion_sensitivity
    # + motion_runner.motion_config_from_sensitivity.
    "motion_sensitivity": {"type": int, "min": 1, "max": 10},
    # Motion detection on/off gate. Maps to the MOTION_DETECTION
    # config key on-device; stream.py gates the motion pipeline
    # on it. The server's Camera model uses the longer name
    # ``recording_motion_enabled`` for the same boolean and
    # renames before pushing (see camera_service._translate_stream_params_for_wire).
    "motion_detection": {"type": bool},
    # Image-quality controls (#182). Accepted as a JSON-decoded dict;
    # per-key validation runs against the sensor's image_controls
    # catalogue. Stored in camera.conf as a JSON string under
    # IMAGE_QUALITY. Empty dict clears all overrides.
    "image_quality": {"type": dict},
}

# Highest framerate the schema will accept regardless of resolution.
# Cross-field validation (per-resolution max) tightens this further.
ABSOLUTE_FPS_MAX = 240

# Rate limit: minimum seconds between config changes
RATE_LIMIT_SECONDS = 5

# Default location of the persisted desired stream state file (ADR-0017).
DEFAULT_STREAM_STATE_PATH = "/data/config/stream_state"
VALID_STREAM_STATES = ("running", "stopped")


def _legacy_sensor_modes(caps: SensorCapabilities) -> dict[tuple[int, int], int]:
    """Return the (w, h) → max_fps mapping in the legacy dict shape.

    Several call sites (heartbeat builder, tests) historically read the
    module-level ``SENSOR_MODES`` dict directly. Building the same
    shape on demand from the live capabilities preserves that
    interface without a static table.
    """
    return {(m.width, m.height): int(m.max_fps) for m in caps.modes}


# Module-level alias retained for backward compatibility with callers
# that import ``SENSOR_MODES`` directly (e.g. ``test_control.py``).
# Initialised to the OV5647 table — the Pi camera the home monitor
# originally shipped with — so a fresh import without a configured
# ControlHandler sees a sensible default. Each instantiated
# ``ControlHandler`` rewrites this in :meth:`_update_module_alias`
# to match the actually-detected sensor.
SENSOR_MODES: dict[tuple[int, int], int] = {
    (m.width, m.height): int(m.max_fps) for m in KNOWN_SENSOR_MODES["ov5647"]
}


class ControlHandler:
    """Handles server control API requests.

    Validates parameters against the connected sensor's catalogued modes
    (looked up via :mod:`camera_streamer.sensor_info`), applies changes
    to ConfigManager, and restarts the stream if needed.

    Args:
        config: ConfigManager instance.
        stream_manager: StreamManager instance (or None in tests).
        stream_state_path: Path to the persisted desired stream state file
            (ADR-0017). Default ``/data/config/stream_state``.
        sensor_capabilities: Optional pre-built ``SensorCapabilities``
            for tests. Production leaves this ``None`` and lets the
            handler call ``detect_sensor_capabilities()`` at startup.
    """

    def __init__(
        self,
        config,
        stream_manager=None,
        stream_state_path=DEFAULT_STREAM_STATE_PATH,
        *,
        sensor_capabilities: SensorCapabilities | None = None,
    ):
        self._config = config
        self._stream = stream_manager
        self._last_request_id = 0
        self._last_change_time = 0.0
        self._stream_state_path = stream_state_path
        # Detect the sensor at construction. ``Picamera2.global_camera_info()``
        # does not lock the camera, so this is safe to call before the
        # streaming pipeline opens its own ``Picamera2()`` instance.
        if sensor_capabilities is None:
            sensor_capabilities = detect_sensor_capabilities()
        self._sensor = sensor_capabilities
        self._update_module_alias()
        # Load the persisted desired state so a boot-time lookup is cheap
        # and the in-memory value is always authoritative for the server.
        self._desired_stream_state = self._load_stream_state()

    def _update_module_alias(self) -> None:
        """Refresh the module-level ``SENSOR_MODES`` to match this handler.

        Backward-compat hook: tests and heartbeat code that import
        ``SENSOR_MODES`` directly continue to see a dict consistent with
        the live sensor.
        """
        global SENSOR_MODES
        SENSOR_MODES = _legacy_sensor_modes(self._sensor)

    @property
    def sensor_capabilities(self) -> SensorCapabilities:
        """Capabilities snapshot used by this handler."""
        return self._sensor

    @property
    def desired_stream_state(self):
        """Persisted desired stream state (``running`` or ``stopped``)."""
        return self._desired_stream_state

    def _load_stream_state(self):
        """Read the persisted desired state, defaulting to ``stopped``.

        Missing file or any unexpected content collapses to ``stopped`` —
        a freshly-paired or corrupted camera must not start streaming
        without an explicit server request (ADR-0017 §1).
        """
        try:
            with open(self._stream_state_path) as f:
                value = f.read().strip()
        except OSError:
            return "stopped"
        if value in VALID_STREAM_STATES:
            return value
        return "stopped"

    def _write_stream_state(self, desired):
        """Atomically persist the desired state.

        Tempfile in the same directory + ``os.replace`` guarantees readers
        never see a partial write even if power is lost mid-update.
        """
        parent = os.path.dirname(self._stream_state_path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".stream_state.", dir=parent, text=True)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(desired)
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, self._stream_state_path)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def get_stream_state(self):
        """Return desired stream state and current live streaming flag."""
        return {
            "state": self._desired_stream_state,
            "running": bool(self._stream and self._stream.is_streaming),
        }

    def set_stream_state(self, desired):
        """Set the desired stream state and drive the pipeline accordingly.

        Persists the value atomically before touching the stream pipeline
        so a crash between write and start/stop leaves the on-disk intent
        consistent with what the server asked for. Idempotent: repeated
        ``start`` calls are safe — the underlying StreamManager reports
        already-running and we do not respawn.
        """
        if desired not in VALID_STREAM_STATES:
            return None, "desired must be 'running' or 'stopped'", 400

        self._write_stream_state(desired)
        self._desired_stream_state = desired

        if self._stream is not None:
            currently = bool(self._stream.is_streaming)
            if desired == "running" and not currently:
                self._stream.start()
            elif desired == "stopped" and currently:
                self._stream.stop()

        return (
            {
                "state": desired,
                "running": bool(self._stream and self._stream.is_streaming),
            },
            "",
            200,
        )

    def get_capabilities(self):
        """Return the full capability descriptor for this camera.

        Wire shape (consumed by the server-side dashboard):
          - ``sensor``           — uppercase display name (or "Unknown")
          - ``sensor_model``     — lowercase libcamera model (or null)
          - ``sensor_modes``     — list of {width, height, max_fps}
          - ``detection_method`` — how the sensor was identified
          - ``parameters``       — generic schema (sensor-agnostic
                                   bounds; per-mode constraints are
                                   enforced by ``set_config``)
        """
        sensor_dict = self._sensor.to_dict()
        valid_resolutions = self._sensor.valid_resolutions()
        max_fps = self._max_fps_overall()
        return {
            "sensor": self._sensor.display_name(),
            **sensor_dict,
            "parameters": {
                "width": {
                    "type": "int",
                    "allowed": sorted({w for w, _ in valid_resolutions}),
                },
                "height": {
                    "type": "int",
                    "allowed": sorted({h for _, h in valid_resolutions}),
                },
                "fps": {"type": "int", "min": 1, "max": max_fps},
                "bitrate": {"type": "int", "min": 500000, "max": 8000000},
                "h264_profile": {
                    "type": "string",
                    "allowed": ["baseline", "main", "high"],
                },
                "keyframe_interval": {"type": "int", "min": 1, "max": 120},
                "rotation": {"type": "int", "allowed": [0, 180]},
                "hflip": {"type": "bool"},
                "vflip": {"type": "bool"},
                "motion_sensitivity": {"type": "int", "min": 1, "max": 10},
                "motion_detection": {"type": "bool"},
            },
        }

    def get_config(self):
        """Return current stream configuration."""
        return {
            "width": self._config.width,
            "height": self._config.height,
            "fps": self._config.fps,
            "bitrate": self._config.bitrate,
            "h264_profile": self._config.h264_profile,
            "keyframe_interval": self._config.keyframe_interval,
            "rotation": self._config.rotation,
            "hflip": self._config.hflip,
            "vflip": self._config.vflip,
            "motion_sensitivity": self._config.motion_sensitivity,
            "motion_detection": self._config.motion_detection,
            "image_quality": self._config.image_quality,
        }

    def set_config(self, params, request_id=0, origin="server"):
        """Validate and apply configuration changes.

        Args:
            params: Dict of parameter names to new values.
            request_id: Monotonic request ID for replay protection.

        Returns:
            (result_dict, error_string, http_status_code)
        """
        # Replay protection
        if request_id and request_id <= self._last_request_id:
            return None, "Stale request_id (replay rejected)", 409

        # Rate limiting
        now = time.monotonic()
        elapsed = now - self._last_change_time
        if self._last_change_time > 0 and elapsed < RATE_LIMIT_SECONDS:
            wait = RATE_LIMIT_SECONDS - elapsed
            return None, f"Rate limited, retry after {wait:.0f}s", 429

        if not params:
            return None, "No parameters provided", 400

        # Validate all params first (reject entire request on any error)
        error = self._validate_params(params)
        if error:
            return None, error, 400

        # Check which params actually changed
        current = self.get_config()
        changes = {}
        for key, value in params.items():
            if current.get(key) != value:
                changes[key] = value

        if not changes:
            return (
                {"applied": {}, "restart_required": False, "status": "unchanged"},
                "",
                200,
            )

        # Apply changes to config
        config_updates = {}
        for key, value in changes.items():
            config_key = key.upper()
            if isinstance(value, bool):
                config_updates[config_key] = str(value).lower()
            elif isinstance(value, dict):
                # image_quality is a dict — JSON-encode for config-file
                # storage. ConfigManager re-decodes via the
                # ``image_quality`` property (see config.py).
                config_updates[config_key] = json.dumps(value)
            else:
                config_updates[config_key] = str(value)

        old_values = {k: current[k] for k in changes}
        self._config.update(**config_updates)

        # Log the change
        for key, new_val in changes.items():
            log.info("Config changed: %s = %s (was %s)", key, new_val, old_values[key])

        # Restart stream if needed
        restart_required = True
        restarted = False
        if self._stream:
            restarted = self._stream.restart()

        # Update state
        if request_id:
            self._last_request_id = request_id
        self._last_change_time = time.monotonic()

        return (
            {
                "applied": changes,
                "restart_required": restart_required,
                "restarted": restarted,
                "status": "ok",
                "origin": origin,
            },
            "",
            200,
        )

    def get_status(self):
        """Return live operational status."""
        streaming = False
        if self._stream:
            streaming = self._stream.is_streaming

        return {
            "streaming": streaming,
            "consecutive_failures": self._stream.consecutive_failures
            if self._stream
            else 0,
            "config": self.get_config(),
            "camera_id": self._config.camera_id,
            "paired": self._config.has_client_cert,
            "desired_stream_state": self._desired_stream_state,
        }

    # --- Validation helpers ---------------------------------------------

    def _max_fps_overall(self) -> int:
        """Highest max_fps across all modes for the schema bound."""
        if not self._sensor.modes:
            return ABSOLUTE_FPS_MAX
        return int(max(m.max_fps for m in self._sensor.modes))

    def _validate_params(self, params):
        """Validate parameters against schema and the live sensor.

        Returns error string or empty string on success.
        """
        unknown = set(params.keys()) - set(PARAM_SCHEMA.keys())
        if unknown:
            return f"Unknown parameters: {', '.join(sorted(unknown))}"

        # Sensor-derived bounds for width / height / fps. Falls through
        # to the generic schema for everything else.
        valid_resolutions = self._sensor.valid_resolutions()
        widths = sorted({w for w, _ in valid_resolutions})
        heights = sorted({h for _, h in valid_resolutions})
        max_fps_overall = self._max_fps_overall()

        for key, value in params.items():
            schema = PARAM_SCHEMA[key]
            expected_type = schema["type"]

            if not isinstance(value, expected_type):
                return f"{key}: expected {expected_type.__name__}, got {type(value).__name__}"

            # Sensor-derived allowed lists for w/h.
            if key == "width" and widths and value not in widths:
                return f"{key}: must be one of {widths}, got {value}"
            if key == "height" and heights and value not in heights:
                return f"{key}: must be one of {heights}, got {value}"

            if "allowed" in schema and value not in schema["allowed"]:
                return f"{key}: must be one of {schema['allowed']}, got {value}"

            if "min" in schema and value < schema["min"]:
                return f"{key}: minimum is {schema['min']}, got {value}"

            if "max" in schema and value > schema["max"]:
                return f"{key}: maximum is {schema['max']}, got {value}"

            if key == "fps" and value > max_fps_overall:
                return f"{key}: maximum is {max_fps_overall}, got {value}"

            # image_quality dict: validate each entry against the
            # IMAGE_CONTROL_CATALOGUE the sensor advertises. Unknown
            # keys are dropped silently from the saved dict (the
            # dashboard only offers what the camera reports). Bad
            # values per known key are an error — we'd rather reject
            # the PUT than silently store garbage.
            if key == "image_quality":
                err = self._validate_image_quality(value)
                if err:
                    return err

        # Cross-field validation: width+height must form a valid mode
        # for this sensor.
        width = params.get("width", self._config.width)
        height = params.get("height", self._config.height)
        if ("width" in params or "height" in params) and (
            width,
            height,
        ) not in valid_resolutions:
            valid = ", ".join(f"{w}x{h}" for w, h in sorted(valid_resolutions))
            return f"Invalid resolution {width}x{height}. Valid: {valid}"

        # Cross-field: fps must not exceed the chosen mode's max framerate.
        fps = params.get("fps", self._config.fps)
        max_for_res = self._sensor.max_fps_for(width, height)
        if max_for_res is not None and fps > max_for_res:
            return f"FPS {fps} exceeds maximum {int(max_for_res)} for {width}x{height}"

        return ""

    def _validate_image_quality(self, payload: dict) -> str:
        """Validate an ``image_quality`` dict against the camera's catalogue.

        Returns error string (caller propagates as 400) or empty string
        on success. Unknown keys are NOT errors — they're silently
        ignored when the dict is JSON-encoded into camera.conf below.
        Known keys must respect their advertised bounds.
        """
        catalogue = self._sensor.image_controls or {}
        for k, v in payload.items():
            spec = catalogue.get(k)
            if spec is None:
                # Unknown key — drop silently (sensor swap, future bindings).
                continue
            kind = spec.get("kind")
            if kind in ("linear", "multiplier"):
                try:
                    v_f = float(v)
                except (TypeError, ValueError):
                    return f"image_quality.{k}: expected number, got {type(v).__name__}"
                lo = spec.get("min")
                hi = spec.get("max")
                if lo is not None and v_f < lo:
                    return f"image_quality.{k}: minimum is {lo}, got {v_f}"
                if hi is not None and v_f > hi:
                    return f"image_quality.{k}: maximum is {hi}, got {v_f}"
            elif kind == "enum":
                if not isinstance(v, str):
                    return f"image_quality.{k}: expected string, got {type(v).__name__}"
                allowed = spec.get("choices") or []
                if v not in allowed:
                    return f"image_quality.{k}: must be one of {allowed}, got {v!r}"
        return ""


def parse_control_request(body):
    """Parse a control API request body.

    Returns (params_dict, request_id, error_string).
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None, 0, "Invalid JSON"

    if not isinstance(data, dict):
        return None, 0, "Expected JSON object"

    request_id = data.pop("request_id", 0)
    if not isinstance(request_id, int):
        return None, 0, "request_id must be integer"

    return data, request_id, ""

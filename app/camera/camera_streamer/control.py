"""
Camera control API handler — processes configuration commands from the server.

The server pushes stream parameter changes via HTTPS to this handler.
Authentication is via mTLS (server presents its certificate signed by
the same CA used during pairing). See ADR-0015.

Controllable parameters (all require stream pipeline restart):
  width, height, fps, bitrate, h264_profile, keyframe_interval,
  rotation, hflip, vflip

OV5647 sensor modes usable for H264 streaming (libcamera on RPi Zero 2W):
  640x480   @ up to 58 fps
  1296x972  @ up to 43 fps
  1920x1080 @ up to 30 fps

Note: 2592x1944 (full 5MP) is a valid sensor mode but the Pi Zero 2W
cannot encode it fast enough for real-time H264 streaming — ffmpeg
fails to detect codec parameters. Excluded from allowed resolutions.
"""

import json
import logging
import time

log = logging.getLogger("camera-streamer.control")

# OV5647 sensor: validated resolution+fps combinations.
# Max FPS per resolution from libcamera --list-cameras output.
SENSOR_MODES = {
    (640, 480): 58,
    (1296, 972): 43,
    (1920, 1080): 30,
}

VALID_RESOLUTIONS = set(SENSOR_MODES.keys())

PARAM_SCHEMA = {
    "width": {"type": int, "allowed": [w for w, _ in VALID_RESOLUTIONS]},
    "height": {"type": int, "allowed": [h for _, h in VALID_RESOLUTIONS]},
    "fps": {"type": int, "min": 1, "max": 58},
    "bitrate": {"type": int, "min": 500000, "max": 8000000},
    "h264_profile": {"type": str, "allowed": ["baseline", "main", "high"]},
    "keyframe_interval": {"type": int, "min": 1, "max": 120},
    "rotation": {"type": int, "allowed": [0, 180]},
    "hflip": {"type": bool},
    "vflip": {"type": bool},
}

# Rate limit: minimum seconds between config changes
RATE_LIMIT_SECONDS = 5


class ControlHandler:
    """Handles server control API requests.

    Validates parameters against OV5647 hardware capabilities,
    applies changes to ConfigManager, and restarts stream if needed.

    Args:
        config: ConfigManager instance.
        stream_manager: StreamManager instance (or None in tests).
    """

    def __init__(self, config, stream_manager=None):
        self._config = config
        self._stream = stream_manager
        self._last_request_id = 0
        self._last_change_time = 0.0

    def get_capabilities(self):
        """Return supported parameter ranges for this camera hardware."""
        return {
            "sensor": "OV5647",
            "sensor_modes": [
                {
                    "width": w,
                    "height": h,
                    "max_fps": max_fps,
                }
                for (w, h), max_fps in sorted(SENSOR_MODES.items())
            ],
            "parameters": {
                "width": {
                    "type": "int",
                    "allowed": sorted({w for w, _ in VALID_RESOLUTIONS}),
                },
                "height": {
                    "type": "int",
                    "allowed": sorted({h for _, h in VALID_RESOLUTIONS}),
                },
                "fps": {"type": "int", "min": 1, "max": 58},
                "bitrate": {"type": "int", "min": 500000, "max": 8000000},
                "h264_profile": {
                    "type": "string",
                    "allowed": ["baseline", "main", "high"],
                },
                "keyframe_interval": {"type": "int", "min": 1, "max": 120},
                "rotation": {"type": "int", "allowed": [0, 180]},
                "hflip": {"type": "bool"},
                "vflip": {"type": "bool"},
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
            config_updates[config_key] = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )

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
        }

    def _validate_params(self, params):
        """Validate parameters against schema and hardware constraints.

        Returns error string or empty string on success.
        """
        unknown = set(params.keys()) - set(PARAM_SCHEMA.keys())
        if unknown:
            return f"Unknown parameters: {', '.join(sorted(unknown))}"

        for key, value in params.items():
            schema = PARAM_SCHEMA[key]
            expected_type = schema["type"]

            if not isinstance(value, expected_type):
                return f"{key}: expected {expected_type.__name__}, got {type(value).__name__}"

            if "allowed" in schema and value not in schema["allowed"]:
                return f"{key}: must be one of {schema['allowed']}, got {value}"

            if "min" in schema and value < schema["min"]:
                return f"{key}: minimum is {schema['min']}, got {value}"

            if "max" in schema and value > schema["max"]:
                return f"{key}: maximum is {schema['max']}, got {value}"

        # Cross-field validation: width+height must form valid resolution pair
        width = params.get("width", self._config.width)
        height = params.get("height", self._config.height)
        if ("width" in params or "height" in params) and (
            width,
            height,
        ) not in VALID_RESOLUTIONS:
            valid = ", ".join(f"{w}x{h}" for w, h in sorted(VALID_RESOLUTIONS))
            return f"Invalid resolution {width}x{height}. Valid: {valid}"

        # Cross-field: fps must not exceed sensor mode max
        fps = params.get("fps", self._config.fps)
        if (width, height) in SENSOR_MODES:
            max_fps = SENSOR_MODES[(width, height)]
            if fps > max_fps:
                return f"FPS {fps} exceeds maximum {max_fps} for {width}x{height}"

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

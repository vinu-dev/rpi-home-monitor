"""
Configuration manager.

Reads camera config from /data/config/camera.conf.
This file persists across OTA updates (on the data partition).

Config values:
  SERVER_IP      - RPi 4B server IP address
  SERVER_PORT    - RTSPS port (default: 8554)
  STREAM_NAME    - RTSP stream path
  WIDTH          - Video width (default: 1920)
  HEIGHT         - Video height (default: 1080)
  FPS            - Framerate (default: 25)
  CAMERA_ID      - Derived from hardware serial if not set
"""

import hashlib
import logging
import os
import secrets

log = logging.getLogger("camera-streamer.config")

# Defaults
DEFAULTS = {
    "SERVER_IP": "",
    "SERVER_PORT": "8554",
    "STREAM_NAME": "stream",
    "WIDTH": "1920",
    "HEIGHT": "1080",
    "FPS": "25",
    "BITRATE": "4000000",
    "H264_PROFILE": "high",
    "KEYFRAME_INTERVAL": "30",
    "ROTATION": "0",
    "HFLIP": "false",
    "VFLIP": "false",
    "CAMERA_ID": "",
    "ADMIN_USERNAME": "admin",  # default username
    "ADMIN_PASSWORD": "",  # salt:hash (PBKDF2-SHA256)
    # Motion detection (docs/archive/exec-plans/motion-detection.md). When true,
    # the streaming ffmpeg tees a downsampled grayscale stream to a FIFO
    # and MotionRunner drives the detector off it. Events are POSTed to
    # the paired server via HMAC. Requires numpy on-device.
    "MOTION_DETECTION": "false",
    # Motion sensitivity on a 1 (least) - 10 (most) scale. The camera
    # maps this into concrete MotionDetector thresholds in motion_runner;
    # 5 = current shipping defaults. Changed via the control-channel
    # ``motion_sensitivity`` param so operators can tune per-camera from
    # the server's Settings UI without editing camera.conf by hand.
    "MOTION_SENSITIVITY": "5",
    # Motion-mode pre-roll (#160). Phase 1 ships the bounded ring-buffer
    # plumbing behind a default-off kill switch until hardware soak proves
    # the Picamera2 CircularOutput path stable on the supported cameras.
    "MOTION_PREROLL_ENABLED": "false",
    "MOTION_PREROLL_SECONDS": "3",
    # Image-quality controls (#182). JSON-encoded dict mapping libcamera
    # control names to user-set values, e.g.
    #   {"Sharpness": 1.5, "Contrast": 1.2, "NoiseReductionMode": "Fast"}
    # Keys absent from the dict fall through to libcamera defaults — only
    # user-customised values are persisted. Pushed by the server via the
    # existing control channel; applied by ``picam_backend`` after
    # ``start_recording`` via ``Picamera2.set_controls``.
    "IMAGE_QUALITY": "{}",
}


# REQ: SWR-035; RISK: RISK-012; SEC: SC-012; TEST: TC-033
class ConfigManager:
    """Load and manage camera configuration."""

    def __init__(self, data_dir=None):
        self._data_dir = data_dir or os.environ.get("CAMERA_DATA_DIR", "/data")
        self._config_path = os.path.join(self._data_dir, "config", "camera.conf")
        self._default_path = "/opt/camera/camera.conf.default"
        self._values = dict(DEFAULTS)

    @property
    def server_ip(self):
        return self._values["SERVER_IP"]

    @property
    def server_port(self):
        return int(self._values["SERVER_PORT"])

    @property
    def stream_name(self):
        return self._values["STREAM_NAME"]

    @property
    def width(self):
        return int(self._values["WIDTH"])

    @property
    def height(self):
        return int(self._values["HEIGHT"])

    @property
    def fps(self):
        return int(self._values["FPS"])

    @property
    def bitrate(self):
        return int(self._values["BITRATE"])

    @property
    def h264_profile(self):
        return self._values["H264_PROFILE"]

    @property
    def keyframe_interval(self):
        return int(self._values["KEYFRAME_INTERVAL"])

    @property
    def rotation(self):
        return int(self._values["ROTATION"])

    @property
    def hflip(self):
        return self._values["HFLIP"].lower() == "true"

    @property
    def vflip(self):
        return self._values["VFLIP"].lower() == "true"

    @property
    def motion_detection(self):
        """True if the motion-detection pipeline should run (numpy required)."""
        return str(self._values.get("MOTION_DETECTION", "false")).lower() == "true"

    @property
    def motion_sensitivity(self) -> int:
        """Per-camera motion sensitivity, 1 (lowest) … 10 (highest).

        Mapped to concrete MotionDetector thresholds at start_recording
        time — see ``motion_runner.motion_config_from_sensitivity``.
        """
        try:
            v = int(self._values.get("MOTION_SENSITIVITY", 5))
        except (TypeError, ValueError):
            v = 5
        return max(1, min(10, v))

    @property
    def motion_pre_roll_enabled(self) -> bool:
        """True if motion-mode recordings should flush a bounded pre-roll."""
        return (
            str(self._values.get("MOTION_PREROLL_ENABLED", "false")).lower() == "true"
        )

    @property
    def motion_pre_roll_seconds(self) -> int:
        """Configured motion pre-roll duration in seconds."""
        try:
            v = int(self._values.get("MOTION_PREROLL_SECONDS", 3))
        except (TypeError, ValueError):
            v = 3
        return max(0, v)

    @property
    def image_quality(self) -> dict:
        """User-customised image-quality controls (#182).

        JSON-decoded dict mapping libcamera control name → value.
        Empty dict means "no overrides — use libcamera defaults".
        Defensively returns an empty dict on any decode error so a
        malformed value can't crash the streamer.
        """
        import json

        raw = self._values.get("IMAGE_QUALITY", "{}")
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("IMAGE_QUALITY is not valid JSON (%r) — ignoring", raw)
            return {}
        if not isinstance(data, dict):
            log.warning("IMAGE_QUALITY is not a JSON object (%r) — ignoring", data)
            return {}
        return data

    @property
    def camera_id(self):
        cid = self._values["CAMERA_ID"]
        if not cid:
            cid = _get_hardware_serial()
            self._values["CAMERA_ID"] = cid
        return cid

    @property
    def rtsp_url(self):
        """Build the RTSP URL for streaming to server.

        Uses camera_id as the stream path so the server can identify
        which camera is sending the stream (multi-camera support).
        """
        if not self.server_ip:
            return ""
        # Use camera_id as stream path for server-side identification
        path = self.camera_id or self.stream_name
        return f"rtsp://{self.server_ip}:{self.server_port}/{path}"

    @property
    def rtsps_url(self):
        """Build the RTSPS URL for mTLS streaming to server.

        Uses rtsps:// scheme and port 8322 (MediaMTX RTSPS default).
        Only valid when client certs exist (camera is paired).
        """
        if not self.server_ip:
            return ""
        path = self.camera_id or self.stream_name
        return f"rtsps://{self.server_ip}:8322/{path}"

    @property
    def server_https_url(self):
        """Server HTTPS base URL for API calls (registration, pairing).

        Returns empty string when server_ip is not configured.
        Handles both bare hostnames and URLs with existing schemes.
        """
        if not self.server_ip:
            return ""
        if "://" in self.server_ip:
            return self.server_ip.rstrip("/")
        return f"https://{self.server_ip}"

    @property
    def has_client_cert(self):
        """Return True if client certificate exists (camera is paired)."""
        return os.path.isfile(os.path.join(self.certs_dir, "client.crt"))

    @property
    def certs_dir(self):
        return os.path.join(self._data_dir, "certs")

    @property
    def config_dir(self):
        return os.path.join(self._data_dir, "config")

    @property
    def data_dir(self):
        return self._data_dir

    @property
    def admin_username(self):
        """Return the admin username."""
        return self._values["ADMIN_USERNAME"]

    @property
    def admin_password(self):
        """Return the raw salt:hash string."""
        return self._values["ADMIN_PASSWORD"]

    @property
    def has_password(self):
        """Return True if an admin password has been set."""
        return bool(self._values["ADMIN_PASSWORD"])

    def set_password(self, plaintext):
        """Hash and store an admin password using PBKDF2-SHA256."""
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", plaintext.encode(), salt.encode(), 100000)
        self._values["ADMIN_PASSWORD"] = f"{salt}:{h.hex()}"

    def check_password(self, plaintext):
        """Verify a password against the stored hash."""
        stored = self._values["ADMIN_PASSWORD"]
        if not stored or ":" not in stored:
            return False
        salt, expected_hash = stored.split(":", 1)
        h = hashlib.pbkdf2_hmac("sha256", plaintext.encode(), salt.encode(), 100000)
        return secrets.compare_digest(h.hex(), expected_hash)

    @property
    def is_configured(self):
        """Return True if server IP is set (minimum for streaming)."""
        return bool(self.server_ip)

    def load(self):
        """Load config from file, falling back to defaults."""
        self._ensure_config_exists()
        if os.path.isfile(self._config_path):
            self._parse_config(self._config_path)
            log.info("Config loaded from %s", self._config_path)
        else:
            log.warning("No config file found, using defaults")

        # Auto-generate camera ID from hardware serial
        if not self._values["CAMERA_ID"]:
            self._values["CAMERA_ID"] = _get_hardware_serial()

        log.info(
            "Camera %s — server=%s:%s, %sx%s@%sfps",
            self.camera_id,
            self.server_ip or "(not configured)",
            self.server_port,
            self.width,
            self.height,
            self.fps,
        )
        return self

    def save(self):
        """Write current config back to file."""
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w") as f:
            for key, val in self._values.items():
                f.write(f"{key}={val}\n")
        log.info("Config saved to %s", self._config_path)

    def update(self, **kwargs):
        """Update config values and save."""
        for key, val in kwargs.items():
            ukey = key.upper()
            if ukey in DEFAULTS:
                self._values[ukey] = str(val)
        self.save()

    def _ensure_config_exists(self):
        """Copy default config to /data if no config exists yet.

        Safety guard: refuse to write defaults unless ``self._data_dir``
        is a real mountpoint distinct from ``/``. Otherwise a /data
        mount failure during boot would silently factory-reset the
        camera — the previous paired config on the real /data partition
        would be hidden by a rootfs-backed overlay, ``_ensure_config``
        would see no file, and defaults would be written to the
        overlay. The camera would then enter setup mode / AP mode and
        drop off the LAN. See ADR-0008 for the OTA persistence contract.
        """
        if os.path.isfile(self._config_path):
            return
        if not self._is_data_persisted():
            log.error(
                "Refusing to initialise %s: %s is not a separate mounted "
                "filesystem (stub overlay on rootfs). This indicates a "
                "boot-time /data mount failure; writing defaults here "
                "would factory-reset the camera on next boot.",
                self._config_path,
                self._data_dir,
            )
            return
        if os.path.isfile(self._default_path):
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._default_path) as src:
                content = src.read()
            with open(self._config_path, "w") as dst:
                dst.write(content)
            log.info("Default config copied to %s", self._config_path)

    def _is_data_persisted(self):
        """Return True iff ``self._data_dir`` is on a different device than /.

        Used to detect when /data failed to mount and is silently
        falling through to the rootfs directory. Tests may override
        this by subclassing; production relies on st_dev comparison.
        """
        # The env-var override exists so tests and local dev can opt
        # out when using a plain directory as /data.
        if os.environ.get("CAMERA_SKIP_MOUNT_CHECK") == "1":
            return True
        # The mount contract is Linux-specific. On non-POSIX hosts we
        # cannot infer a valid /data mount from Windows path semantics,
        # so fail closed unless the explicit escape hatch is enabled.
        if os.name != "posix":
            return False
        try:
            return os.stat(self._data_dir).st_dev != os.stat("/").st_dev
        except OSError:
            return False

    def _parse_config(self, path):
        """Parse KEY=VALUE config file (shell-style, ignoring comments)."""
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key in DEFAULTS:
                    self._values[key] = val


def _get_hardware_serial():
    """Read the RPi hardware serial from /proc/cpuinfo."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    serial = line.split(":")[-1].strip()
                    return f"cam-{serial[-8:]}"
    except (OSError, IndexError):
        pass
    # Fallback: use hostname
    import socket

    return f"cam-{socket.gethostname()}"

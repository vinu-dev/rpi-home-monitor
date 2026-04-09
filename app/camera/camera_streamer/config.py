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
import os
import logging

log = logging.getLogger("camera-streamer.config")

# Defaults
DEFAULTS = {
    "SERVER_IP": "",
    "SERVER_PORT": "8554",
    "STREAM_NAME": "stream",
    "WIDTH": "1920",
    "HEIGHT": "1080",
    "FPS": "25",
    "CAMERA_ID": "",
}


class ConfigManager:
    """Load and manage camera configuration."""

    def __init__(self, data_dir=None):
        self._data_dir = data_dir or os.environ.get(
            "CAMERA_DATA_DIR", "/data"
        )
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
    def certs_dir(self):
        return os.path.join(self._data_dir, "certs")

    @property
    def config_dir(self):
        return os.path.join(self._data_dir, "config")

    @property
    def data_dir(self):
        return self._data_dir

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
        """Copy default config to /data if no config exists yet."""
        if os.path.isfile(self._config_path):
            return
        if os.path.isfile(self._default_path):
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._default_path, "r") as src:
                content = src.read()
            with open(self._config_path, "w") as dst:
                dst.write(content)
            log.info("Default config copied to %s", self._config_path)

    def _parse_config(self, path):
        """Parse KEY=VALUE config file (shell-style, ignoring comments)."""
        with open(path, "r") as f:
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
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    serial = line.split(":")[-1].strip()
                    return f"cam-{serial[-8:]}"
    except (OSError, IndexError):
        pass
    # Fallback: use hostname
    import socket
    return f"cam-{socket.gethostname()}"

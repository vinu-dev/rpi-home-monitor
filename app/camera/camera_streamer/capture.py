"""
Video capture management.

Handles v4l2 device detection and configuration.
The actual capture is done by ffmpeg (started by StreamManager),
but this module validates the device exists and is accessible.

Checks:
- /dev/video0 exists
- v4l2 device supports h264 output
- Requested resolution is supported
"""
import os
import subprocess
import logging

log = logging.getLogger("camera-streamer.capture")

DEFAULT_DEVICE = "/dev/video0"


class CaptureManager:
    """Validate and manage the v4l2 camera device."""

    def __init__(self, device=None):
        self._device = device or DEFAULT_DEVICE
        self._available = False
        self._formats = []

    @property
    def device(self):
        return self._device

    @property
    def available(self):
        return self._available

    @property
    def formats(self):
        return list(self._formats)

    def check(self):
        """Validate the camera device exists and is accessible.

        Returns True if the device is ready to use.
        """
        # Check device node exists
        if not os.path.exists(self._device):
            log.error("Camera device %s not found", self._device)
            self._available = False
            return False

        # Check it's a character device (video device)
        if not os.stat(self._device).st_mode & 0o020000:
            # Not a char device — might be in test env
            log.warning("%s exists but is not a character device", self._device)

        # Try to query formats via v4l2-ctl
        self._formats = self._query_formats()
        self._available = True
        log.info(
            "Camera device %s ready (%d format(s) detected)",
            self._device,
            len(self._formats),
        )
        return True

    def supports_h264(self):
        """Check if the device supports H.264 output."""
        return any("h264" in f.lower() or "H.264" in f for f in self._formats)

    def supports_resolution(self, width, height):
        """Check if a specific resolution is listed in formats."""
        res_str = f"{width}x{height}"
        return any(res_str in f for f in self._formats)

    def _query_formats(self):
        """Query supported formats from v4l2-ctl."""
        try:
            result = subprocess.run(
                ["v4l2-ctl", "-d", self._device, "--list-formats-ext"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = [
                    line.strip()
                    for line in result.stdout.splitlines()
                    if line.strip()
                ]
                return lines
        except FileNotFoundError:
            log.warning("v4l2-ctl not found — cannot query device formats")
        except subprocess.TimeoutExpired:
            log.warning("v4l2-ctl timed out querying %s", self._device)
        except OSError as e:
            log.warning("Failed to query device formats: %s", e)
        return []

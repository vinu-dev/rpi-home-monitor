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

import logging
import os
import subprocess

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
        log.info("Checking camera device %s ...", self._device)

        # List all video devices for debugging
        video_devs = (
            [f"/dev/{d}" for d in os.listdir("/dev") if d.startswith("video")]
            if os.path.isdir("/dev")
            else []
        )
        log.info("Video devices found: %s", video_devs or "NONE")

        # Check device node exists
        if not os.path.exists(self._device):
            log.error(
                "Camera device %s not found. Available: %s. "
                "Check ribbon cable is connected and camera overlay is enabled "
                "(dtoverlay=ov5647 for PiHut ZeroCam in config.txt)",
                self._device,
                video_devs or "none",
            )
            self._available = False
            return False

        # Check it's a character device (video device)
        mode = os.stat(self._device).st_mode
        if not mode & 0o020000:
            # Not a char device — might be in test env
            log.warning(
                "%s exists but is not a character device (mode=%o)", self._device, mode
            )

        # Try to query formats via v4l2-ctl
        self._formats = self._query_formats()
        if self._formats:
            log.info("Camera formats:\n  %s", "\n  ".join(self._formats[:20]))
        else:
            log.warning(
                "No formats detected for %s — v4l2-ctl may not be installed "
                "or camera driver not loaded. Check: lsmod | grep ov5647",
                self._device,
            )

        h264_ok = self.supports_h264()
        libcam = self.has_libcamera()
        if h264_ok:
            log.info(
                "Camera device %s ready — %d format(s), native H.264=YES",
                self._device,
                len(self._formats),
            )
        elif libcam:
            log.info(
                "Camera device %s ready — %d format(s), native H.264=NO, "
                "libcamera-vid available (will handle ISP + encode)",
                self._device,
                len(self._formats),
            )
        else:
            log.warning(
                "Camera device %s — no native H.264 and no libcamera-vid! "
                "Streaming will likely fail.",
                self._device,
            )
        self._available = True
        return True

    def supports_h264(self):
        """Check if the device supports native H.264 output."""
        return any("h264" in f.lower() or "H.264" in f for f in self._formats)

    def has_libcamera(self):
        """Check if libcamera-vid is available for ISP-based capture."""
        import shutil

        return shutil.which("libcamera-vid") is not None

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
                    line.strip() for line in result.stdout.splitlines() if line.strip()
                ]
                return lines
        except FileNotFoundError:
            log.warning("v4l2-ctl not found — cannot query device formats")
        except subprocess.TimeoutExpired:
            log.warning("v4l2-ctl timed out querying %s", self._device)
        except OSError as e:
            log.warning("Failed to query device formats: %s", e)
        return []

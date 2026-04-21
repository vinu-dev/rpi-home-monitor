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
        # Short, user-facing error message populated by ``check()``.
        # Surfaced on the dashboard camera card + camera status page
        # via the heartbeat so operators know a freshly-paired camera
        # is missing its sensor module (common cause: ribbon cable
        # not seated, or Zero 2W plugged in without a camera module).
        self._last_error = ""

    @property
    def device(self):
        return self._device

    @property
    def available(self):
        return self._available

    @property
    def formats(self):
        return list(self._formats)

    @property
    def last_error(self) -> str:
        """Short user-facing description of the last hardware fault.

        Empty string when the last ``check()`` succeeded or was not yet
        run. Consumed by HeartbeatSender to surface in the dashboard
        + camera status page.
        """
        return self._last_error

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
            self._last_error = (
                "No camera module detected. Check the ribbon cable is "
                "seated firmly and /boot/config.txt has dtoverlay=ov5647 "
                "(for the PiHut ZeroCam) or the overlay for your sensor."
            )
            return False

        # Existence isn't enough: on a Pi Zero 2W without a sensor,
        # /dev/video10-31 still exist (libcamera subdevices like
        # bcm2835-codec and bcm2835-isp). We need to verify the node
        # actually reports *Video Capture* — otherwise the streamer
        # later fails at libcamera/picamera2 start with a cryptic
        # "list index out of range" and the dashboard banner never
        # fires because hardware_ok was left True.
        if not self._reports_video_capture(self._device):
            log.error(
                "Camera device %s is not a Video Capture node "
                "(likely libcamera subdevice without a sensor). "
                "Available: %s",
                self._device,
                video_devs or "none",
            )
            self._available = False
            self._last_error = (
                "No camera module detected. A video device node was "
                "found but it does not report a capture sensor. Check "
                "the ribbon cable is seated firmly and "
                "/boot/config.txt has dtoverlay=ov5647 (PiHut ZeroCam) "
                "or the overlay for your sensor."
            )
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
        self._last_error = ""
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

    def _reports_video_capture(self, device: str) -> bool:
        """True iff ``v4l2-ctl --info`` shows Video Capture in Device Caps.

        Device Caps (the "what this specific node does" block) is
        the correct thing to read — the top-level Capabilities
        line mirrors every node on the hardware and always contains
        "Video Capture" on a board with a capture-capable driver,
        even on nodes that are actually M2M or Output.

        Returns ``True`` on tool failure so we don't regress a
        working sensor into a false negative when v4l2-ctl is
        missing from the image; downstream picamera2 checks will
        catch real problems at stream start.
        """
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--device", device, "--info"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except FileNotFoundError:
            log.warning(
                "v4l2-ctl not available — skipping capture-capability "
                "verification on %s",
                device,
            )
            return True
        except subprocess.TimeoutExpired:
            log.warning("v4l2-ctl timed out probing %s", device)
            return True
        except OSError as e:
            log.warning("v4l2-ctl OSError on %s: %s", device, e)
            return True

        if result.returncode != 0:
            return False

        in_device_caps = False
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Device Caps"):
                in_device_caps = True
                continue
            if in_device_caps:
                if not line.startswith(("\t", " ")):
                    break
                if "Video Capture" in stripped:
                    return True
        return False

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

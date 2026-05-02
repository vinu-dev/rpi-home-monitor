# REQ: SWR-012; RISK: RISK-001, RISK-008; TEST: TC-005, TC-018
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
import threading

from camera_streamer.faults import (
    FAULT_CAMERA_H264_UNSUPPORTED,
    FAULT_CAMERA_SENSOR_MISSING,
    Fault,
    make_fault,
)

log = logging.getLogger("camera-streamer.capture")

DEFAULT_DEVICE = "/dev/video0"

# User-facing banner string — kept in one place so the two failure
# branches (no V4L2 Video Capture node; V4L2 node present but
# libcamera finds no sensor) surface the same message. Re-used as
# the text of the dashboard + camera status page warning banners.
#
# Sensor-agnostic: the image ships with auto-detect + overlays for
# every Pi-officially-supported sensor (OV5647, IMX219, IMX477, IMX708),
# so the operator-actionable signal is "the cable, not the overlay".
_NO_CAMERA_ERROR = (
    "No camera module detected. Check the ribbon cable is seated "
    "firmly at both ends and reboot. The image supports OV5647, "
    "IMX219, IMX477 and IMX708 sensors via firmware auto-detect."
)


class CaptureManager:
    """Validate and manage the v4l2 camera device."""

    def __init__(self, device=None):
        self._device = device or DEFAULT_DEVICE
        self._available = False
        self._formats = []
        # Short, user-facing error message populated by ``check()``.
        # Kept as a plain string for backward compat with the first
        # hardware-status slice; richer callers should consume
        # ``faults`` instead.
        self._last_error = ""
        # List of ``Fault`` records for the structured wire format.
        # Heartbeat serialises each entry to a dict and the server
        # stores them so the dashboard can render severity + code
        # per-fault instead of a single boolean. Empty list = healthy.
        self._faults: list[Fault] = []
        # External faults raised by other subsystems (e.g. the
        # boot-time server-name resolver in #199) that don't own a
        # CaptureManager themselves but want to surface a hardware-fault
        # entry on the heartbeat. Keyed by ``code`` so adding a fault
        # with a code already present overwrites — same idempotency
        # contract as the internal ``check()`` path.
        self._external_faults: dict[str, Fault] = {}
        self._faults_lock = threading.Lock()

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

    @property
    def faults(self) -> list[Fault]:
        """Active hardware faults from the last ``check()`` plus externals.

        Empty list when healthy. Each entry has ``code`` + ``severity``
        + ``message`` (see ``faults.py``). Heartbeat serialises the
        list into the wire payload so the server dashboard can render
        per-fault banners with severity colouring.

        Returns the union of ``check()``-managed internal faults and
        externally raised faults (``add_fault``/``clear_fault``). The
        thread-safety boundary lives on ``_external_faults`` only —
        ``_faults`` is set once during the synchronous startup
        ``check()`` and is never mutated thereafter, so concurrent
        readers see a stable list.
        """
        with self._faults_lock:
            external = list(self._external_faults.values())
        return list(self._faults) + external

    def add_fault(self, fault: Fault) -> None:
        """Raise an external fault.

        Idempotent on ``code`` — re-adding the same code overwrites the
        prior entry rather than duplicating, matching the wire-shape
        contract that the dashboard expects (one row per fault code).

        Used by long-lived background tasks (e.g. the server-name
        resolver in #199) to bubble a hardware-fault badge onto the
        next heartbeat without owning their own fault registry.
        """
        if fault is None:
            return
        with self._faults_lock:
            self._external_faults[fault.code] = fault

    def clear_fault(self, code: str) -> None:
        """Drop an externally raised fault by code. No-op if absent.

        Symmetric counterpart to ``add_fault`` — the resolver clears
        its own fault on a successful late-resolution so the dashboard
        badge disappears when the underlying condition recovers,
        rather than persisting until the camera reboots.
        """
        if not code:
            return
        with self._faults_lock:
            self._external_faults.pop(code, None)

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
                "Check ribbon cable is connected; firmware auto-detect "
                "should pick the right overlay for any supported sensor "
                "(OV5647 / IMX219 / IMX477 / IMX708)",
                self._device,
                video_devs or "none",
            )
            self._available = False
            self._last_error = _NO_CAMERA_ERROR
            self._faults = [
                make_fault(
                    FAULT_CAMERA_SENSOR_MISSING,
                    context={"device": self._device},
                )
            ]
            return False

        # Existence isn't enough on a Pi Zero 2W:
        #
        #  a) /dev/video10-31 are libcamera subdevices (bcm2835-codec
        #     M2M, bcm2835-isp Output) that exist regardless of
        #     whether a sensor is attached. Those fail the V4L2
        #     "Video Capture" capability check below.
        #
        #  b) /dev/video14 (unicam capture node) is registered the
        #     moment ``dtoverlay=ov5647`` loads, even with the
        #     ribbon cable unplugged. It reports "Video Capture" in
        #     Device Caps — V4L2 alone isn't enough. The definitive
        #     probe for "is a real sensor attached" on Pi is
        #     ``libcamera-hello --list-cameras``: prints "No cameras
        #     available!" (exit 0) when the I2C enumerator finds no
        #     sensor, otherwise lists the sensor names.
        #
        # We run (a) first (cheap, no ISP spin-up) then fall through
        # to (b) which is the expensive-but-accurate check.
        if not self._reports_video_capture(self._device):
            log.error(
                "Camera device %s is not a Video Capture node "
                "(likely libcamera subdevice without a sensor). "
                "Available: %s",
                self._device,
                video_devs or "none",
            )
            self._available = False
            self._last_error = _NO_CAMERA_ERROR
            self._faults = [
                make_fault(
                    FAULT_CAMERA_SENSOR_MISSING,
                    context={"device": self._device},
                )
            ]
            return False

        # libcamera-hello is authoritative on the Pi: no sensor → no
        # enumerated camera. Tolerates missing tool (return True) so
        # non-Pi test environments that happen to have real V4L2
        # capture don't get falsely flagged.
        if not self._libcamera_reports_sensor():
            log.error(
                "libcamera-hello reports no sensor attached despite "
                "%s being a Video Capture node. Ribbon cable likely "
                "unplugged or sensor not on the expected I2C bus.",
                self._device,
            )
            self._available = False
            self._last_error = _NO_CAMERA_ERROR
            self._faults = [
                make_fault(
                    FAULT_CAMERA_SENSOR_MISSING,
                    context={"device": self._device},
                )
            ]
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
                "or camera driver not loaded. Run `dmesg | grep -iE "
                "'imx219|ov5647|imx477|imx708'` to see which sensor (if any) "
                "the kernel probed.",
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
        # Happy path — clear faults, emit a non-fatal warning fault if
        # the sensor is there but can't give us H.264. Surfaces as a
        # yellow badge without changing online status.
        if h264_ok or libcam:
            self._faults = []
        else:
            self._faults = [make_fault(FAULT_CAMERA_H264_UNSUPPORTED)]
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

    def _libcamera_reports_sensor(self) -> bool:
        """True iff ``libcamera-hello --list-cameras`` enumerates a sensor.

        On Pi, the I2C enumerator runs when this tool is invoked:
        with a connected sensor it prints one or more ``Available
        cameras`` lines; without, it prints
        ``No cameras available!`` and exits 0. We treat the tool
        missing or hung as "sensor present" so non-Pi test hosts
        or images without libcamera-hello don't trigger a false
        negative.
        """
        try:
            result = subprocess.run(
                ["libcamera-hello", "--list-cameras"],
                capture_output=True,
                text=True,
                timeout=6,
            )
        except FileNotFoundError:
            # Fall back to rpicam-hello (newer Pi image split the
            # name). Same output shape.
            try:
                result = subprocess.run(
                    ["rpicam-hello", "--list-cameras"],
                    capture_output=True,
                    text=True,
                    timeout=6,
                )
            except FileNotFoundError:
                log.warning(
                    "Neither libcamera-hello nor rpicam-hello found — "
                    "cannot verify sensor presence"
                )
                return True
            except subprocess.TimeoutExpired:
                log.warning("rpicam-hello --list-cameras timed out")
                return True
            except OSError as e:
                log.warning("rpicam-hello OSError: %s", e)
                return True
        except subprocess.TimeoutExpired:
            log.warning("libcamera-hello --list-cameras timed out")
            return True
        except OSError as e:
            log.warning("libcamera-hello OSError: %s", e)
            return True

        combined = (result.stdout or "") + (result.stderr or "")
        if "No cameras available" in combined:
            return False
        # Positive signal: the tool lists at least one sensor. We accept
        # either the "Available cameras" header or an indexed entry like
        # "0 : <sensor> [..." for any supported sensor name.
        if "Available cameras" in combined:
            return True
        if any(s in combined for s in ("ov5647", "imx219", "imx477", "imx708")):
            return True
        # Neither marker — assume present to avoid false negatives
        # when the tool's output format changes across versions.
        return True

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

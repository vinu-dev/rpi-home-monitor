"""
Platform provider for hardware abstraction.

Detects hardware-specific paths and capabilities at startup.
All hardware access in the application reads from Platform
instead of hardcoding device paths.

Detection priority:
  1. Environment variables (explicit override)
  2. Hardware probing (auto-detect)
  3. Sensible defaults (RPi assumed)

Environment variables:
  CAMERA_DEVICE       — camera device path (default: /dev/video0)
  CAMERA_LED_PATH     — LED sysfs directory (default: /sys/class/leds/ACT)
  CAMERA_THERMAL_PATH — thermal sensor file (default: /sys/class/thermal/thermal_zone0/temp)
  CAMERA_WIFI_IFACE   — WiFi interface name (default: wlan0)
  CAMERA_HOSTNAME_PREFIX — hostname prefix (default: rpi-divinu-cam)
"""

import glob
import logging
import os

log = logging.getLogger("camera-streamer.platform")


class Platform:
    """Hardware abstraction provider.

    Detects device paths, LED location, thermal sensor, and WiFi
    interface. All values can be overridden via environment variables.
    """

    def __init__(
        self,
        camera_device: str = "/dev/video0",
        led_path: str | None = "/sys/class/leds/ACT",
        thermal_path: str | None = "/sys/class/thermal/thermal_zone0/temp",
        wifi_interface: str = "wlan0",
        hostname_prefix: str = "rpi-divinu-cam",
    ):
        self.camera_device = camera_device
        self.led_path = led_path
        self.thermal_path = thermal_path
        self.wifi_interface = wifi_interface
        self.hostname_prefix = hostname_prefix

    @classmethod
    def detect(cls) -> "Platform":
        """Auto-detect platform from environment variables and hardware.

        Priority: env vars > hardware probing > defaults.
        """
        camera_device = os.environ.get("CAMERA_DEVICE", _probe_camera_device())
        led_path = os.environ.get("CAMERA_LED_PATH", _probe_led_path())
        thermal_path = os.environ.get("CAMERA_THERMAL_PATH", _probe_thermal_path())
        wifi_interface = os.environ.get("CAMERA_WIFI_IFACE", _probe_wifi_interface())
        hostname_prefix = os.environ.get("CAMERA_HOSTNAME_PREFIX", "rpi-divinu-cam")

        platform = cls(
            camera_device=camera_device,
            led_path=led_path if led_path else None,
            thermal_path=thermal_path if thermal_path else None,
            wifi_interface=wifi_interface,
            hostname_prefix=hostname_prefix,
        )
        log.info(
            "Platform detected: camera=%s, led=%s, thermal=%s, wifi=%s, prefix=%s",
            platform.camera_device,
            platform.led_path or "none",
            platform.thermal_path or "none",
            platform.wifi_interface,
            platform.hostname_prefix,
        )
        return platform

    def has_led(self) -> bool:
        """Return True if an LED sysfs path is available."""
        if not self.led_path:
            return False
        return os.path.isdir(self.led_path)

    def has_thermal(self) -> bool:
        """Return True if a thermal sensor is available."""
        if not self.thermal_path:
            return False
        return os.path.isfile(self.thermal_path)

    def has_camera(self) -> bool:
        """Return True if the camera device node exists."""
        return os.path.exists(self.camera_device)


def _probe_camera_device() -> str:
    """Find the first available video device."""
    for path in sorted(glob.glob("/dev/video*")):
        return path
    return "/dev/video0"


def _probe_led_path() -> str | None:
    """Find the ACT or status LED sysfs path."""
    candidates = [
        "/sys/class/leds/ACT",
        "/sys/class/leds/led0",
        "/sys/class/leds/default-on",
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def _probe_thermal_path() -> str | None:
    """Find the CPU thermal sensor."""
    candidates = sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp"))
    if candidates:
        return candidates[0]
    return None


def _probe_wifi_interface() -> str:
    """Find the first wireless interface."""
    try:
        wireless_dir = "/sys/class/net"
        if os.path.isdir(wireless_dir):
            for iface in sorted(os.listdir(wireless_dir)):
                wireless_marker = os.path.join(wireless_dir, iface, "wireless")
                if os.path.isdir(wireless_marker):
                    return iface
    except OSError:
        pass
    return "wlan0"

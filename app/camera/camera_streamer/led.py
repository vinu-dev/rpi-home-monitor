"""
LED status indicator for camera.

Controls the onboard LED via sysfs to show device state.
Silently fails if LED is not accessible (e.g. in tests or containers).

Patterns:
  setup_mode()  -- slow blink (1s on / 1s off) -- waiting for WiFi config
  connecting()  -- fast blink (200ms on / 200ms off) -- trying to connect
  connected()   -- solid ON -- running normally
  error()       -- very fast blink (100ms on / 100ms off) -- something wrong
  off()         -- LED off

Usage:
  # With Platform injection (preferred):
  led = LedController(platform.led_path)

  # Module-level convenience (backward compatibility):
  import camera_streamer.led as led
  led.setup_mode()
"""

import logging
import os

log = logging.getLogger("camera-streamer.led")


class LedController:
    """Control an LED via sysfs with fail-silent behavior.

    Args:
        led_path: sysfs directory for the LED (e.g. /sys/class/leds/ACT).
                  Pass None to create a no-op controller.
    """

    def __init__(self, led_path: str | None = None):
        self._path = led_path

    @property
    def available(self) -> bool:
        """Return True if the LED sysfs path exists."""
        if not self._path:
            return False
        return os.path.isdir(self._path)

    def _write(self, filename: str, value: str) -> None:
        """Write a value to an LED sysfs file. Fails silently."""
        if not self._path:
            return
        try:
            path = os.path.join(self._path, filename)
            with open(path, "w") as f:
                f.write(str(value))
        except OSError as e:
            log.debug("LED write failed (%s=%s): %s", filename, value, e)

    def setup_mode(self) -> None:
        """Slow blink -- hotspot active, waiting for user to configure."""
        log.debug("LED: setup_mode (slow blink)")
        self._write("trigger", "timer")
        self._write("delay_on", "1000")
        self._write("delay_off", "1000")

    def connecting(self) -> None:
        """Fast blink -- attempting WiFi connection."""
        log.debug("LED: connecting (fast blink)")
        self._write("trigger", "timer")
        self._write("delay_on", "200")
        self._write("delay_off", "200")

    def connected(self) -> None:
        """Solid ON -- connected and running normally."""
        log.debug("LED: connected (solid on)")
        self._write("trigger", "none")
        self._write("brightness", "1")

    def error(self) -> None:
        """Very fast blink -- error state, needs attention."""
        log.debug("LED: error (very fast blink)")
        self._write("trigger", "timer")
        self._write("delay_on", "100")
        self._write("delay_off", "100")

    def off(self) -> None:
        """LED off."""
        log.debug("LED: off")
        self._write("trigger", "none")
        self._write("brightness", "0")


# ---- Module-level convenience API (backward compatibility) ----
# Default instance uses the standard RPi ACT LED path.
# Modules that import `led` and call `led.setup_mode()` still work.
_default = LedController("/sys/class/leds/ACT")


def setup_mode():
    """Slow blink -- hotspot active, waiting for user to configure."""
    _default.setup_mode()


def connecting():
    """Fast blink -- attempting WiFi connection."""
    _default.connecting()


def connected():
    """Solid ON -- connected and running normally."""
    _default.connected()


def error():
    """Very fast blink -- error state, needs attention."""
    _default.error()


def off():
    """LED off."""
    _default.off()


def set_controller(controller: LedController) -> None:
    """Replace the default controller (called from main.py after Platform.detect)."""
    global _default
    _default = controller

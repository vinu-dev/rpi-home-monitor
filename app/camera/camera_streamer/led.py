"""
LED status indicator for camera.

Controls the onboard ACT LED via sysfs to show device state.
Silently fails if LED is not accessible (e.g. in tests or containers).

Patterns:
  setup_mode()  — slow blink (1s on / 1s off) — waiting for WiFi config
  connecting()  — fast blink (200ms on / 200ms off) — trying to connect
  connected()   — solid ON — running normally
  error()       — very fast blink (100ms on / 100ms off) — something wrong
  off()         — LED off
"""
import os
import logging

log = logging.getLogger("camera-streamer.led")

# ACT LED sysfs path (standard on RPi 4B and Zero 2W)
LED_PATH = "/sys/class/leds/ACT"


def _write(filename, value):
    """Write a value to an LED sysfs file. Fails silently."""
    try:
        path = os.path.join(LED_PATH, filename)
        with open(path, "w") as f:
            f.write(str(value))
    except (OSError, IOError) as e:
        log.debug("LED write failed (%s=%s): %s", filename, value, e)


def setup_mode():
    """Slow blink — hotspot active, waiting for user to configure."""
    log.debug("LED: setup_mode (slow blink)")
    _write("trigger", "timer")
    _write("delay_on", "1000")
    _write("delay_off", "1000")


def connecting():
    """Fast blink — attempting WiFi connection."""
    log.debug("LED: connecting (fast blink)")
    _write("trigger", "timer")
    _write("delay_on", "200")
    _write("delay_off", "200")


def connected():
    """Solid ON — connected and running normally."""
    log.debug("LED: connected (solid on)")
    _write("trigger", "none")
    _write("brightness", "1")


def error():
    """Very fast blink — error state, needs attention."""
    log.debug("LED: error (very fast blink)")
    _write("trigger", "timer")
    _write("delay_on", "100")
    _write("delay_off", "100")


def off():
    """LED off."""
    log.debug("LED: off")
    _write("trigger", "none")
    _write("brightness", "0")

# REQ: SWR-042, SWR-050; RISK: RISK-018, RISK-022; SEC: SC-019; TEST: TC-039, TC-044
"""Shared appliance status LED policy.

This module is copied into both product packages:

  /opt/monitor/monitor/status_led.py
  /opt/camera/camera_streamer/status_led.py

It deliberately depends only on the Python standard library so it can run
from early boot scripts, privileged helpers, and the camera app itself.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("home-monitor.status-led")

LED_CLASS_DIR = "/sys/class/leds"
DEFAULT_DATA_DIR = "/data"
PRODUCT_LED_NAMES = {"act"}
POWER_LED_NAMES = {"pwr", "power"}


@dataclass(frozen=True)
class LedPattern:
    trigger: str
    brightness: str | None = None
    delay_on: str | None = None
    delay_off: str | None = None


PATTERNS: dict[str, LedPattern] = {
    "off": LedPattern("none", brightness="0"),
    "healthy": LedPattern("none", brightness="1"),
    "booting": LedPattern("timer", delay_on="500", delay_off="500"),
    "setup": LedPattern("timer", delay_on="1000", delay_off="1000"),
    "pairing": LedPattern("timer", delay_on="400", delay_off="1200"),
    "connecting": LedPattern("timer", delay_on="200", delay_off="200"),
    "ota_installing": LedPattern("timer", delay_on="1800", delay_off="200"),
    "ota_rebooting": LedPattern("timer", delay_on="150", delay_off="150"),
    "ota_validating": LedPattern("timer", delay_on="500", delay_off="500"),
    "reset": LedPattern("timer", delay_on="100", delay_off="100"),
    "error": LedPattern("timer", delay_on="100", delay_off="900"),
}

STATE_ALIASES = {
    "boot": "boot",
    "connected": "healthy",
    "running": "healthy",
    "normal": "healthy",
    "setup_mode": "setup",
    "installing": "ota_installing",
    "rebooting": "ota_rebooting",
    "validating": "ota_validating",
}


def normalize_state(state: str) -> str:
    key = str(state or "").strip().lower().replace("-", "_")
    return STATE_ALIASES.get(key, key)


def activation_path(role: str, data_dir: str = DEFAULT_DATA_DIR) -> str:
    role = "camera" if role == "camera" else "server"
    return os.path.join(data_dir, "ota", f"{role}-activation.json")


def read_activation(role: str, data_dir: str = DEFAULT_DATA_DIR) -> dict[str, Any]:
    try:
        with open(activation_path(role, data_dir), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def mark_activation(
    role: str,
    target_version: str = "",
    data_dir: str = DEFAULT_DATA_DIR,
) -> None:
    path = activation_path(role, data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "role": "camera" if role == "camera" else "server",
        "target_version": str(target_version or ""),
        "marked_at": int(time.time()),
    }
    fd, tmp_path = tempfile.mkstemp(prefix=".activation.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def clear_activation(role: str, data_dir: str = DEFAULT_DATA_DIR) -> None:
    try:
        os.unlink(activation_path(role, data_dir))
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.debug("Could not clear activation marker: %s", exc)


def discover_led_path(
    *,
    led_class_dir: str = LED_CLASS_DIR,
    preferred: str | None = None,
) -> str:
    if preferred and os.path.isdir(preferred):
        return preferred
    try:
        entries = list(os.scandir(led_class_dir))
    except OSError:
        return preferred or os.path.join(led_class_dir, "ACT")

    by_name = {entry.name.lower(): entry.path for entry in entries if entry.is_dir()}
    for name in ("act", "status"):
        if name in by_name:
            return by_name[name]
    return by_name.get("default-on", preferred or os.path.join(led_class_dir, "ACT"))


class StatusLedController:
    """Control the single product status LED through Linux LED sysfs."""

    def __init__(
        self,
        led_path: str | None = None,
        *,
        role: str = "server",
        led_class_dir: str = LED_CLASS_DIR,
        data_dir: str = DEFAULT_DATA_DIR,
        force: bool = False,
    ) -> None:
        self._led_class_dir = led_class_dir
        self._path = discover_led_path(
            led_class_dir=led_class_dir,
            preferred=led_path,
        )
        self._role = "camera" if role == "camera" else "server"
        self._data_dir = data_dir
        self._force = force

    @property
    def available(self) -> bool:
        return bool(self._path) and os.path.isdir(self._path)

    def _maybe_chmod(self, path: str) -> None:
        if not self._force:
            return
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass

    def _write_path(self, path: str, value: str) -> None:
        try:
            self._maybe_chmod(path)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(str(value))
        except OSError as exc:
            log.debug("LED write failed (%s=%s): %s", path, value, exc)

    def _write(self, filename: str, value: str) -> None:
        if not self._path:
            return
        self._write_path(os.path.join(self._path, filename), value)

    def quiet_non_product_leds(self) -> None:
        try:
            entries = list(os.scandir(self._led_class_dir))
        except OSError:
            return
        product = Path(self._path or "").name.lower()
        for entry in entries:
            name = entry.name.lower()
            if not entry.is_dir() or name == product:
                continue
            if name in PRODUCT_LED_NAMES or name in POWER_LED_NAMES:
                continue
            self._quiet_led(entry.path)

    def _quiet_led(self, led_path: str) -> None:
        self._write_path(os.path.join(led_path, "trigger"), "none")
        self._write_path(os.path.join(led_path, "brightness"), "0")

    def set_state(self, state: str) -> None:
        state = normalize_state(state)
        if state == "boot":
            state = (
                "ota_validating"
                if read_activation(self._role, self._data_dir)
                else "booting"
            )
        pattern = PATTERNS.get(state)
        if pattern is None:
            log.debug("Ignoring unknown LED state: %s", state)
            return
        self._write("trigger", pattern.trigger)
        if pattern.delay_on is not None:
            self._write("delay_on", pattern.delay_on)
        if pattern.delay_off is not None:
            self._write("delay_off", pattern.delay_off)
        if pattern.brightness is not None:
            self._write("brightness", pattern.brightness)

    def off(self) -> None:
        self.set_state("off")

    def healthy(self, *, clear_marker: bool = False) -> None:
        if clear_marker:
            clear_activation(self._role, self._data_dir)
        self.set_state("healthy")

    def setup(self) -> None:
        self.set_state("setup")

    def pairing(self) -> None:
        self.set_state("pairing")

    def connecting(self) -> None:
        self.set_state("connecting")

    def error(self) -> None:
        self.set_state("error")

    def ota_installing(self) -> None:
        self.set_state("ota_installing")

    def ota_rebooting(self) -> None:
        self.set_state("ota_rebooting")

    def ota_validating(self) -> None:
        self.set_state("ota_validating")

    def reset(self) -> None:
        self.set_state("reset")


def set_state(
    state: str,
    *,
    role: str = "server",
    led_path: str | None = None,
    data_dir: str = DEFAULT_DATA_DIR,
    init: bool = False,
    force: bool = False,
    clear_marker: bool = False,
) -> None:
    controller = StatusLedController(
        led_path,
        role=role,
        data_dir=data_dir,
        force=force,
    )
    if init:
        controller.quiet_non_product_leds()
    if clear_marker:
        clear_activation(role, data_dir)
    controller.set_state(state)

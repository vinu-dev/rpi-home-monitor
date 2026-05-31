# REQ: SWR-042; RISK: RISK-022; TEST: TC-039
"""Camera LED compatibility wrapper around the shared status LED policy."""

from __future__ import annotations

from camera_streamer.status_led import StatusLedController


class LedController(StatusLedController):
    """Control the camera product LED via Linux LED sysfs."""

    def __init__(self, led_path: str | None = None):
        super().__init__(led_path, role="camera")

    def setup_mode(self) -> None:
        self.setup()

    def connected(self) -> None:
        self.healthy(clear_marker=True)


_default = LedController("/sys/class/leds/ACT")


def setup_mode() -> None:
    _default.setup_mode()


def connecting() -> None:
    _default.connecting()


def pairing() -> None:
    _default.pairing()


def connected() -> None:
    _default.connected()


def ota_installing() -> None:
    _default.ota_installing()


def ota_rebooting() -> None:
    _default.ota_rebooting()


def ota_validating() -> None:
    _default.ota_validating()


def reset() -> None:
    _default.reset()


def error() -> None:
    _default.error()


def off() -> None:
    _default.off()


def set_controller(controller: LedController) -> None:
    global _default
    _default = controller

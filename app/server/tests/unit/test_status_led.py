# REQ: SWR-042; RISK: RISK-022; TEST: TC-039
"""Tests for the server status LED policy module."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from monitor import status_led
from monitor.status_led import StatusLedController


def _make_led(root: Path, name: str) -> Path:
    led = root / name
    led.mkdir(parents=True)
    for field in ("trigger", "brightness", "delay_on", "delay_off"):
        (led / field).write_text("", encoding="utf-8")
    return led


def test_normalize_state_aliases():
    assert status_led.normalize_state("setup-mode") == "setup"
    assert status_led.normalize_state("connected") == "healthy"
    assert status_led.normalize_state("rebooting") == "ota_rebooting"
    assert status_led.normalize_state("boot") == "boot"


def test_activation_marker_roundtrip(tmp_path):
    status_led.mark_activation("server", "1.2.3", data_dir=str(tmp_path))

    marker = status_led.read_activation("server", data_dir=str(tmp_path))
    assert marker["role"] == "server"
    assert marker["target_version"] == "1.2.3"
    assert isinstance(marker["marked_at"], int)

    status_led.clear_activation("server", data_dir=str(tmp_path))
    assert status_led.read_activation("server", data_dir=str(tmp_path)) == {}


def test_read_activation_ignores_invalid_json(tmp_path):
    path = Path(status_led.activation_path("server", str(tmp_path)))
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert status_led.read_activation("server", data_dir=str(tmp_path)) == {}


def test_discover_led_path_prefers_act(tmp_path):
    _make_led(tmp_path, "default-on")
    act = _make_led(tmp_path, "ACT")

    assert status_led.discover_led_path(led_class_dir=str(tmp_path)) == str(act)


def test_discover_led_path_falls_back_when_led_class_unreadable(tmp_path):
    preferred = tmp_path / "preferred"

    assert status_led.discover_led_path(
        led_class_dir=str(tmp_path / "missing"),
        preferred=str(preferred),
    ) == str(preferred)
    assert status_led.discover_led_path(
        led_class_dir=str(tmp_path / "missing"),
    ).endswith("ACT")


def test_discover_led_path_uses_default_on_when_no_act(tmp_path):
    default = _make_led(tmp_path, "default-on")

    assert status_led.discover_led_path(led_class_dir=str(tmp_path)) == str(default)


def test_set_state_writes_solid_and_timer_patterns(tmp_path):
    led = _make_led(tmp_path, "ACT")
    ctrl = StatusLedController(str(led), data_dir=str(tmp_path))

    ctrl.healthy()
    assert (led / "trigger").read_text(encoding="utf-8") == "none"
    assert (led / "brightness").read_text(encoding="utf-8") == "1"

    ctrl.ota_installing()
    assert (led / "trigger").read_text(encoding="utf-8") == "timer"
    assert (led / "delay_on").read_text(encoding="utf-8") == "1800"
    assert (led / "delay_off").read_text(encoding="utf-8") == "200"


def test_boot_without_activation_uses_booting_pattern(tmp_path):
    led = _make_led(tmp_path, "ACT")
    ctrl = StatusLedController(str(led), data_dir=str(tmp_path))

    ctrl.set_state("boot")

    assert (led / "trigger").read_text(encoding="utf-8") == "timer"
    assert (led / "delay_on").read_text(encoding="utf-8") == "500"
    assert (led / "delay_off").read_text(encoding="utf-8") == "500"


def test_boot_with_activation_uses_validating_pattern(tmp_path):
    led = _make_led(tmp_path, "ACT")
    status_led.mark_activation("server", "1.2.3", data_dir=str(tmp_path))
    ctrl = StatusLedController(str(led), data_dir=str(tmp_path))

    ctrl.set_state("boot")

    assert (led / "trigger").read_text(encoding="utf-8") == "timer"
    assert (led / "delay_on").read_text(encoding="utf-8") == "500"
    assert (led / "delay_off").read_text(encoding="utf-8") == "500"


def test_healthy_can_clear_activation_marker(tmp_path):
    led = _make_led(tmp_path, "ACT")
    status_led.mark_activation("server", "1.2.3", data_dir=str(tmp_path))

    StatusLedController(str(led), data_dir=str(tmp_path)).healthy(clear_marker=True)

    assert status_led.read_activation("server", data_dir=str(tmp_path)) == {}
    assert (led / "brightness").read_text(encoding="utf-8") == "1"


def test_mark_activation_cleans_partial_file_on_failure(tmp_path):
    with (
        patch("monitor.status_led.json.dump", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        status_led.mark_activation("server", "1.2.3", data_dir=str(tmp_path))

    marker_dir = tmp_path / "ota"
    assert not list(marker_dir.glob(".activation.*"))


def test_clear_activation_ignores_missing_file_and_logs_oserror(tmp_path):
    status_led.clear_activation("server", data_dir=str(tmp_path))

    with patch("monitor.status_led.os.unlink", side_effect=OSError("denied")):
        status_led.clear_activation("server", data_dir=str(tmp_path))


def test_unavailable_led_and_write_errors_are_nonfatal(tmp_path):
    missing = tmp_path / "missing-led"
    ctrl = StatusLedController(str(missing), data_dir=str(tmp_path))

    assert ctrl.available is False
    ctrl.set_state("healthy")

    led = tmp_path / "ACT"
    led.mkdir()
    StatusLedController(str(led), data_dir=str(tmp_path)).set_state("healthy")


def test_unknown_state_is_ignored(tmp_path):
    led = _make_led(tmp_path, "ACT")

    StatusLedController(str(led), data_dir=str(tmp_path)).set_state("mystery")

    assert (led / "trigger").read_text(encoding="utf-8") == ""


def test_convenience_methods_apply_expected_patterns(tmp_path):
    led = _make_led(tmp_path, "ACT")
    ctrl = StatusLedController(str(led), data_dir=str(tmp_path))

    for method in (
        ctrl.setup,
        ctrl.pairing,
        ctrl.connecting,
        ctrl.error,
        ctrl.ota_rebooting,
        ctrl.ota_validating,
        ctrl.reset,
    ):
        method()
        assert (led / "trigger").read_text(encoding="utf-8") in {"none", "timer"}


def test_quiet_non_product_leds_leaves_act_and_power(tmp_path):
    act = _make_led(tmp_path, "ACT")
    power = _make_led(tmp_path, "PWR")
    other = _make_led(tmp_path, "mmc0")

    StatusLedController(str(act), led_class_dir=str(tmp_path)).quiet_non_product_leds()

    assert (other / "trigger").read_text(encoding="utf-8") == "none"
    assert (other / "brightness").read_text(encoding="utf-8") == "0"
    assert (power / "trigger").read_text(encoding="utf-8") == ""


def test_force_mode_chmods_before_writes(tmp_path):
    led = _make_led(tmp_path, "ACT")
    ctrl = StatusLedController(str(led), data_dir=str(tmp_path), force=True)

    with patch("os.chmod") as chmod:
        ctrl.off()

    chmod.assert_any_call(os.path.join(str(led), "trigger"), 0o666)
    chmod.assert_any_call(os.path.join(str(led), "brightness"), 0o666)


def test_module_set_state_init_quiets_other_leds_and_clears_marker(tmp_path):
    act = _make_led(tmp_path / "leds", "ACT")
    marker = Path(status_led.activation_path("server", str(tmp_path)))
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"role": "server"}), encoding="utf-8")

    with patch.object(StatusLedController, "quiet_non_product_leds") as quiet:
        status_led.set_state(
            "healthy",
            led_path=str(act),
            data_dir=str(tmp_path),
            init=True,
            clear_marker=True,
        )

    quiet.assert_called_once()
    assert not marker.exists()
    assert (act / "brightness").read_text(encoding="utf-8") == "1"

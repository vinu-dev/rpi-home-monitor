"""Tests for camera_streamer.led module."""

import os
from unittest.mock import mock_open, patch

from camera_streamer import led
from camera_streamer.led import LedController, set_controller


class TestLedControllerWrite:
    """Test the LedController._write method."""

    @patch("builtins.open", mock_open())
    def test_write_success(self):
        """Should write value to sysfs file."""
        ctrl = LedController("/sys/class/leds/ACT")
        ctrl._write("trigger", "timer")
        open.assert_called_once_with(
            os.path.join("/sys/class/leds/ACT", "trigger"), "w"
        )

    @patch("builtins.open", side_effect=OSError("Permission denied"))
    def test_write_fails_silently(self, mock_file):
        """Should not raise on permission error."""
        ctrl = LedController("/sys/class/leds/ACT")
        ctrl._write("trigger", "timer")  # No exception

    def test_write_noop_when_no_path(self):
        """Should do nothing when led_path is None."""
        ctrl = LedController(None)
        ctrl._write("trigger", "timer")  # No exception, no file access


class TestLedControllerPatterns:
    """Test LedController pattern methods."""

    def setup_method(self):
        self.ctrl = LedController("/sys/class/leds/ACT")

    @patch.object(LedController, "_write")
    def test_setup_mode(self, mock_write):
        """setup_mode should set slow blink."""
        self.ctrl.setup_mode()
        mock_write.assert_any_call("trigger", "timer")
        mock_write.assert_any_call("delay_on", "1000")
        mock_write.assert_any_call("delay_off", "1000")

    @patch.object(LedController, "_write")
    def test_connecting(self, mock_write):
        """connecting should set fast blink."""
        self.ctrl.connecting()
        mock_write.assert_any_call("trigger", "timer")
        mock_write.assert_any_call("delay_on", "200")
        mock_write.assert_any_call("delay_off", "200")

    @patch.object(LedController, "_write")
    def test_connected(self, mock_write):
        """connected should set solid on."""
        self.ctrl.connected()
        mock_write.assert_any_call("trigger", "none")
        mock_write.assert_any_call("brightness", "1")

    @patch.object(LedController, "_write")
    def test_error(self, mock_write):
        """error should set very fast blink."""
        self.ctrl.error()
        mock_write.assert_any_call("trigger", "timer")
        mock_write.assert_any_call("delay_on", "100")
        mock_write.assert_any_call("delay_off", "100")

    @patch.object(LedController, "_write")
    def test_off(self, mock_write):
        """off should turn LED off."""
        self.ctrl.off()
        mock_write.assert_any_call("trigger", "none")
        mock_write.assert_any_call("brightness", "0")


class TestLedControllerAvailability:
    """Test LedController.available property."""

    def test_available_none_path(self):
        ctrl = LedController(None)
        assert ctrl.available is False

    @patch("os.path.isdir", return_value=True)
    def test_available_exists(self, mock_isdir):
        ctrl = LedController("/sys/class/leds/ACT")
        assert ctrl.available is True

    @patch("os.path.isdir", return_value=False)
    def test_available_missing(self, mock_isdir):
        ctrl = LedController("/sys/class/leds/ACT")
        assert ctrl.available is False


class TestModuleLevelApi:
    """Test backward-compatible module-level functions."""

    @patch.object(LedController, "_write")
    def test_module_setup_mode(self, mock_write):
        led.setup_mode()
        mock_write.assert_any_call("trigger", "timer")
        mock_write.assert_any_call("delay_on", "1000")

    @patch.object(LedController, "_write")
    def test_module_connecting(self, mock_write):
        led.connecting()
        mock_write.assert_any_call("delay_on", "200")

    @patch.object(LedController, "_write")
    def test_module_connected(self, mock_write):
        led.connected()
        mock_write.assert_any_call("brightness", "1")

    @patch.object(LedController, "_write")
    def test_module_error(self, mock_write):
        led.error()
        mock_write.assert_any_call("delay_on", "100")

    @patch.object(LedController, "_write")
    def test_module_off(self, mock_write):
        led.off()
        mock_write.assert_any_call("brightness", "0")


class TestSetController:
    """Test replacing the default controller."""

    def test_set_controller(self):
        original = led._default
        new_ctrl = LedController("/sys/class/leds/custom")
        set_controller(new_ctrl)
        assert led._default is new_ctrl
        # Restore original
        set_controller(original)

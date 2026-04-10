"""Tests for camera_streamer.led module."""
import os
import pytest
from unittest.mock import patch, mock_open, call

from camera_streamer import led


class TestLedWrite:
    """Test the low-level _write function."""

    @patch("builtins.open", mock_open())
    def test_write_success(self):
        """Should write value to sysfs file."""
        led._write("trigger", "timer")
        open.assert_called_once_with(
            os.path.join(led.LED_PATH, "trigger"), "w"
        )

    @patch("builtins.open", side_effect=OSError("Permission denied"))
    def test_write_fails_silently(self, mock_file):
        """Should not raise on permission error."""
        led._write("trigger", "timer")  # No exception


class TestLedPatterns:
    """Test LED pattern functions."""

    @patch("camera_streamer.led._write")
    def test_setup_mode(self, mock_write):
        """setup_mode should set slow blink."""
        led.setup_mode()
        mock_write.assert_any_call("trigger", "timer")
        mock_write.assert_any_call("delay_on", "1000")
        mock_write.assert_any_call("delay_off", "1000")

    @patch("camera_streamer.led._write")
    def test_connecting(self, mock_write):
        """connecting should set fast blink."""
        led.connecting()
        mock_write.assert_any_call("trigger", "timer")
        mock_write.assert_any_call("delay_on", "200")
        mock_write.assert_any_call("delay_off", "200")

    @patch("camera_streamer.led._write")
    def test_connected(self, mock_write):
        """connected should set solid on."""
        led.connected()
        mock_write.assert_any_call("trigger", "none")
        mock_write.assert_any_call("brightness", "1")

    @patch("camera_streamer.led._write")
    def test_error(self, mock_write):
        """error should set very fast blink."""
        led.error()
        mock_write.assert_any_call("trigger", "timer")
        mock_write.assert_any_call("delay_on", "100")
        mock_write.assert_any_call("delay_off", "100")

    @patch("camera_streamer.led._write")
    def test_off(self, mock_write):
        """off should turn LED off."""
        led.off()
        mock_write.assert_any_call("trigger", "none")
        mock_write.assert_any_call("brightness", "0")


class TestLedConstants:
    """Test LED configuration constants."""

    def test_led_path(self):
        """LED path should point to ACT LED."""
        assert "ACT" in led.LED_PATH

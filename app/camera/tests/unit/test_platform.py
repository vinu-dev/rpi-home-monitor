"""Tests for platform hardware abstraction."""

import os
from unittest.mock import patch

from camera_streamer.platform import (
    Platform,
    _probe_camera_device,
    _probe_led_path,
    _probe_thermal_path,
    _probe_wifi_interface,
)


class TestPlatformInit:
    """Test Platform constructor."""

    def test_defaults(self):
        p = Platform()
        assert p.camera_device == "/dev/video0"
        assert p.led_path == "/sys/class/leds/ACT"
        assert p.thermal_path == "/sys/class/thermal/thermal_zone0/temp"
        assert p.wifi_interface == "wlan0"
        assert p.hostname_prefix == "rpi-divinu-cam"

    def test_custom_values(self):
        p = Platform(
            camera_device="/dev/video1",
            led_path="/sys/class/leds/led0",
            thermal_path=None,
            wifi_interface="wlan1",
            hostname_prefix="my-cam",
        )
        assert p.camera_device == "/dev/video1"
        assert p.led_path == "/sys/class/leds/led0"
        assert p.thermal_path is None
        assert p.wifi_interface == "wlan1"
        assert p.hostname_prefix == "my-cam"


class TestPlatformDetect:
    """Test Platform.detect() with environment variables."""

    @patch.dict(
        os.environ,
        {
            "CAMERA_DEVICE": "/dev/video5",
            "CAMERA_LED_PATH": "/sys/class/leds/custom",
            "CAMERA_THERMAL_PATH": "/custom/thermal",
            "CAMERA_WIFI_IFACE": "wlan2",
            "CAMERA_HOSTNAME_PREFIX": "test-cam",
        },
    )
    def test_env_vars_override_probing(self):
        p = Platform.detect()
        assert p.camera_device == "/dev/video5"
        assert p.led_path == "/sys/class/leds/custom"
        assert p.thermal_path == "/custom/thermal"
        assert p.wifi_interface == "wlan2"
        assert p.hostname_prefix == "test-cam"

    @patch("camera_streamer.platform._probe_camera_device", return_value="/dev/video0")
    @patch("camera_streamer.platform._probe_led_path", return_value=None)
    @patch("camera_streamer.platform._probe_thermal_path", return_value=None)
    @patch("camera_streamer.platform._probe_wifi_interface", return_value="wlan0")
    def test_probing_fallback(self, mock_wifi, mock_thermal, mock_led, mock_cam):
        # Clear env vars if set
        env = {k: v for k, v in os.environ.items() if not k.startswith("CAMERA_")}
        with patch.dict(os.environ, env, clear=True):
            p = Platform.detect()
        assert p.camera_device == "/dev/video0"
        assert p.led_path is None
        assert p.thermal_path is None
        assert p.wifi_interface == "wlan0"

    @patch.dict(os.environ, {"CAMERA_LED_PATH": ""})
    @patch("camera_streamer.platform._probe_camera_device", return_value="/dev/video0")
    @patch("camera_streamer.platform._probe_thermal_path", return_value=None)
    @patch("camera_streamer.platform._probe_wifi_interface", return_value="wlan0")
    def test_empty_env_var_treated_as_none(self, mock_wifi, mock_thermal, mock_cam):
        p = Platform.detect()
        assert p.led_path is None


class TestPlatformCapabilities:
    """Test has_led, has_thermal, has_camera."""

    def test_has_led_none_path(self):
        p = Platform(led_path=None)
        assert p.has_led() is False

    @patch("os.path.isdir", return_value=True)
    def test_has_led_exists(self, mock_isdir):
        p = Platform(led_path="/sys/class/leds/ACT")
        assert p.has_led() is True

    @patch("os.path.isdir", return_value=False)
    def test_has_led_missing(self, mock_isdir):
        p = Platform(led_path="/sys/class/leds/ACT")
        assert p.has_led() is False

    def test_has_thermal_none_path(self):
        p = Platform(thermal_path=None)
        assert p.has_thermal() is False

    @patch("os.path.isfile", return_value=True)
    def test_has_thermal_exists(self, mock_isfile):
        p = Platform(thermal_path="/sys/class/thermal/thermal_zone0/temp")
        assert p.has_thermal() is True

    @patch("os.path.exists", return_value=True)
    def test_has_camera_exists(self, mock_exists):
        p = Platform(camera_device="/dev/video0")
        assert p.has_camera() is True

    @patch("os.path.exists", return_value=False)
    def test_has_camera_missing(self, mock_exists):
        p = Platform(camera_device="/dev/video0")
        assert p.has_camera() is False


class TestProbing:
    """Test hardware probing functions."""

    @patch("glob.glob", return_value=["/dev/video0", "/dev/video1"])
    def test_probe_camera_device_found(self, mock_glob):
        assert _probe_camera_device() == "/dev/video0"

    @patch("glob.glob", return_value=[])
    def test_probe_camera_device_fallback(self, mock_glob):
        assert _probe_camera_device() == "/dev/video0"

    @patch("os.path.isdir", side_effect=lambda p: p == "/sys/class/leds/ACT")
    def test_probe_led_path_act(self, mock_isdir):
        assert _probe_led_path() == "/sys/class/leds/ACT"

    @patch("os.path.isdir", return_value=False)
    def test_probe_led_path_none(self, mock_isdir):
        assert _probe_led_path() is None

    @patch("os.path.isdir", side_effect=lambda p: p == "/sys/class/leds/led0")
    def test_probe_led_path_led0_fallback(self, mock_isdir):
        assert _probe_led_path() == "/sys/class/leds/led0"

    @patch("glob.glob", return_value=["/sys/class/thermal/thermal_zone0/temp"])
    def test_probe_thermal_found(self, mock_glob):
        assert _probe_thermal_path() == "/sys/class/thermal/thermal_zone0/temp"

    @patch("glob.glob", return_value=[])
    def test_probe_thermal_none(self, mock_glob):
        assert _probe_thermal_path() is None

    @patch(
        "os.path.isdir",
        side_effect=lambda p: (
            p
            in [
                "/sys/class/net",
                "/sys/class/net/wlan0/wireless",
            ]
        ),
    )
    @patch("os.listdir", return_value=["eth0", "lo", "wlan0"])
    def test_probe_wifi_interface_found(self, mock_listdir, mock_isdir):
        assert _probe_wifi_interface() == "wlan0"

    @patch("os.path.isdir", side_effect=lambda p: p == "/sys/class/net")
    @patch("os.listdir", return_value=["eth0", "lo"])
    def test_probe_wifi_interface_fallback(self, mock_listdir, mock_isdir):
        assert _probe_wifi_interface() == "wlan0"

    @patch("os.path.isdir", return_value=False)
    def test_probe_wifi_no_sysfs(self, mock_isdir):
        assert _probe_wifi_interface() == "wlan0"

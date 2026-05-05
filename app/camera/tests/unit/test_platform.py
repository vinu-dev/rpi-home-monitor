# REQ: SWR-037; RISK: RISK-022; SEC: SC-020; TEST: TC-035
"""Tests for platform hardware abstraction."""

import os
from unittest.mock import patch

from camera_streamer.platform import (
    Platform,
    _probe_camera_device,
    _probe_led_path,
    _probe_thermal_path,
    _probe_throttle_path,
    _probe_vcgencmd_path,
    _probe_wifi_interface,
)


class TestPlatformInit:
    """Test Platform constructor."""

    def test_defaults(self):
        p = Platform()
        assert p.camera_device == "/dev/video0"
        assert p.led_path == "/sys/class/leds/ACT"
        assert p.thermal_path == "/sys/class/thermal/thermal_zone0/temp"
        assert p.vcgencmd_path is None
        assert p.throttle_path is None
        assert p.wifi_interface == "wlan0"
        assert p.hostname_prefix == "rpi-divinu-cam"

    def test_custom_values(self):
        p = Platform(
            camera_device="/dev/video1",
            led_path="/sys/class/leds/led0",
            thermal_path=None,
            vcgencmd_path="/usr/bin/vcgencmd",
            throttle_path="/sys/devices/platform/soc/test/throttled",
            wifi_interface="wlan1",
            hostname_prefix="my-cam",
        )
        assert p.camera_device == "/dev/video1"
        assert p.led_path == "/sys/class/leds/led0"
        assert p.thermal_path is None
        assert p.vcgencmd_path == "/usr/bin/vcgencmd"
        assert p.throttle_path == "/sys/devices/platform/soc/test/throttled"
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
            "CAMERA_VCGENCMD_PATH": "/custom/vcgencmd",
            "CAMERA_THROTTLED_PATH": "/custom/throttled",
            "CAMERA_WIFI_IFACE": "wlan2",
            "CAMERA_HOSTNAME_PREFIX": "test-cam",
        },
    )
    def test_env_vars_override_probing(self):
        p = Platform.detect()
        assert p.camera_device == "/dev/video5"
        assert p.led_path == "/sys/class/leds/custom"
        assert p.thermal_path == "/custom/thermal"
        assert p.vcgencmd_path == "/custom/vcgencmd"
        assert p.throttle_path == "/custom/throttled"
        assert p.wifi_interface == "wlan2"
        assert p.hostname_prefix == "test-cam"

    @patch("camera_streamer.platform._probe_camera_device", return_value="/dev/video0")
    @patch("camera_streamer.platform._probe_led_path", return_value=None)
    @patch("camera_streamer.platform._probe_thermal_path", return_value=None)
    @patch("camera_streamer.platform._probe_vcgencmd_path", return_value=None)
    @patch("camera_streamer.platform._probe_throttle_path", return_value=None)
    @patch("camera_streamer.platform._probe_wifi_interface", return_value="wlan0")
    def test_probing_fallback(
        self,
        mock_wifi,
        mock_throttle,
        mock_vcgencmd,
        mock_thermal,
        mock_led,
        mock_cam,
    ):
        # Clear env vars if set
        env = {k: v for k, v in os.environ.items() if not k.startswith("CAMERA_")}
        with patch.dict(os.environ, env, clear=True):
            p = Platform.detect()
        assert p.camera_device == "/dev/video0"
        assert p.led_path is None
        assert p.thermal_path is None
        assert p.vcgencmd_path is None
        assert p.throttle_path is None
        assert p.wifi_interface == "wlan0"

    @patch.dict(os.environ, {"CAMERA_LED_PATH": ""})
    @patch("camera_streamer.platform._probe_camera_device", return_value="/dev/video0")
    @patch("camera_streamer.platform._probe_thermal_path", return_value=None)
    @patch("camera_streamer.platform._probe_vcgencmd_path", return_value=None)
    @patch("camera_streamer.platform._probe_throttle_path", return_value=None)
    @patch("camera_streamer.platform._probe_wifi_interface", return_value="wlan0")
    def test_empty_env_var_treated_as_none(
        self, mock_wifi, mock_throttle, mock_vcgencmd, mock_thermal, mock_cam
    ):
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

    @patch("shutil.which", return_value="/usr/bin/vcgencmd")
    def test_has_throttle_via_vcgencmd(self, mock_which):
        p = Platform(vcgencmd_path="/usr/bin/vcgencmd")
        assert p.has_throttle() is True

    @patch("os.path.isfile", return_value=True)
    def test_has_throttle_via_sysfs(self, mock_isfile):
        p = Platform(throttle_path="/sys/devices/platform/soc/test/throttled")
        assert p.has_throttle() is True

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

    @patch("shutil.which", return_value="/usr/bin/vcgencmd")
    def test_probe_vcgencmd_path(self, mock_which):
        assert _probe_vcgencmd_path() == "/usr/bin/vcgencmd"

    @patch("os.path.isdir", return_value=True)
    @patch(
        "os.walk",
        return_value=[
            ("/sys/devices/platform/soc/firmware", [], ["throttled"]),
        ],
    )
    @patch("os.path.isfile", return_value=True)
    def test_probe_throttle_path_found(self, mock_isfile, mock_walk, mock_isdir):
        # os.path.join uses the host separator (backslash on Windows,
        # forward slash on Linux) — test against the joined form so
        # both CI and local Windows runs agree.
        expected = os.path.join("/sys/devices/platform/soc/firmware", "throttled")
        assert _probe_throttle_path() == expected

    @patch("os.path.isdir", return_value=True)
    @patch("os.walk", return_value=[])
    def test_probe_throttle_path_none(self, mock_walk, mock_isdir):
        assert _probe_throttle_path() is None

    @patch("os.path.isdir", return_value=False)
    def test_probe_throttle_path_no_soc_root(self, mock_isdir):
        """Non-Pi platforms (no /sys/devices/platform/soc) → return None
        without ever invoking the walk."""
        assert _probe_throttle_path() is None

    def test_probe_throttle_path_does_not_follow_symlinks(self):
        """Symlink loops in /sys/devices/platform/soc must not hang the
        probe. The bug that prompted this guard: Pi Zero 2W's
        bcm2835 serial driver has a `<dev>/driver/<dev>/...`
        self-referential chain in sysfs; the prior `glob.glob(pattern,
        recursive=True)` followed it forever (Python 3.12 docs: ** does
        follow symlinks) and pinned the camera-streamer at boot for
        minutes before systemd's watchdog killed it.
        """
        # The fix is structural — os.walk is called with
        # followlinks=False. Verify the call shape directly so
        # future refactors can't silently re-enable it.
        with (
            patch("os.path.isdir", return_value=True),
            patch("os.walk", return_value=[]) as mock_walk,
        ):
            _probe_throttle_path()
        assert mock_walk.call_count == 1
        # os.walk(top, topdown=True, onerror=None, followlinks=False)
        # — followlinks may be passed positionally or as a kwarg.
        kwargs = mock_walk.call_args.kwargs
        if "followlinks" in kwargs:
            assert kwargs["followlinks"] is False
        else:
            args = mock_walk.call_args.args
            # If passed positionally, the 4th arg is followlinks.
            assert len(args) < 4 or args[3] is False, (
                f"os.walk called with followlinks=True: args={args}"
            )

    def test_probe_throttle_path_caps_walked_directories(self):
        """Even on a non-loop sysfs forest, the probe must not walk
        unbounded — defence against future SoCs with thousands of
        nodes adding seconds to boot."""

        def fake_walk(*_args, **_kwargs):
            # Simulate a giant sysfs that keeps yielding empty dirs.
            for i in range(10_000):
                yield (f"/sys/devices/platform/soc/dir{i}", [], [])

        with (
            patch("os.path.isdir", return_value=True),
            patch("os.walk", side_effect=fake_walk),
        ):
            assert _probe_throttle_path() is None

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

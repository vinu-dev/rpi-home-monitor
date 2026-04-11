"""Tests for CameraLifecycle state machine."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from camera_streamer.lifecycle import CameraLifecycle, State


def _make_config(**overrides):
    defaults = dict(
        server_ip="192.168.1.100",
        camera_id="cam-test",
        data_dir="/tmp/test",
        is_configured=True,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _make_platform(**overrides):
    defaults = dict(
        camera_device="/dev/video0",
        wifi_interface="wlan0",
        led_path="/sys/class/leds/ACT",
        thermal_path="/sys/class/thermal/thermal_zone0",
        hostname_prefix="homecam",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestState:
    """Test State constants."""

    def test_all_states_defined(self):
        assert State.INIT == "init"
        assert State.SETUP == "setup"
        assert State.CONNECTING == "connecting"
        assert State.VALIDATING == "validating"
        assert State.RUNNING == "running"
        assert State.SHUTDOWN == "shutdown"


class TestInit:
    """Test INIT state — LED + platform logging."""

    @patch("camera_streamer.lifecycle.led")
    @patch("camera_streamer.lifecycle.LedController")
    def test_init_configures_led(self, MockLed, mock_led_mod):
        config = _make_config()
        platform = _make_platform()
        shutdown = [True]  # Shut down immediately after init

        lc = CameraLifecycle(config, platform, lambda: shutdown[0])

        # Manually run _do_init
        result = lc._do_init()
        assert result is True
        MockLed.assert_called_once_with(platform.led_path)
        mock_led_mod.set_controller.assert_called_once()


class TestSetup:
    """Test SETUP state — first-boot wizard."""

    @patch("camera_streamer.lifecycle.WifiSetupServer")
    def test_skips_when_already_done(self, MockSetup):
        config = _make_config()
        platform = _make_platform()

        setup = MagicMock()
        setup.needs_setup.return_value = False
        MockSetup.return_value = setup

        lc = CameraLifecycle(config, platform, lambda: False)
        result = lc._do_setup()

        assert result is True
        setup.start.assert_not_called()

    @patch("camera_streamer.lifecycle.WifiSetupServer")
    def test_runs_wizard_when_needed(self, MockSetup):
        config = _make_config()
        platform = _make_platform()

        call_count = [0]

        def needs_setup():
            call_count[0] += 1
            return call_count[0] < 3  # True twice, then False

        setup = MagicMock()
        setup.needs_setup.side_effect = needs_setup
        MockSetup.return_value = setup

        lc = CameraLifecycle(config, platform, lambda: False)
        result = lc._do_setup()

        assert result is True
        setup.start.assert_called_once()
        setup.stop.assert_called_once()
        config.load.assert_called()  # Reloads after setup

    @patch("camera_streamer.lifecycle.WifiSetupServer")
    def test_returns_false_on_shutdown_during_setup(self, MockSetup):
        config = _make_config()
        platform = _make_platform()

        setup = MagicMock()
        setup.needs_setup.return_value = True
        MockSetup.return_value = setup

        # Shutdown immediately
        lc = CameraLifecycle(config, platform, lambda: True)
        result = lc._do_setup()

        assert result is False
        setup.stop.assert_called_once()


class TestConnecting:
    """Test CONNECTING state — WiFi + server resolution."""

    def test_success(self):
        config = _make_config()
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)

        with (
            patch.object(lc, "_wait_for_wifi", return_value=True),
            patch.object(lc, "_resolve_server") as mock_resolve,
        ):
            result = lc._do_connecting()

        assert result is True
        mock_resolve.assert_called_once()

    def test_skips_resolve_when_unconfigured(self):
        config = _make_config(is_configured=False)
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)

        with (
            patch.object(lc, "_wait_for_wifi", return_value=True),
            patch.object(lc, "_resolve_server") as mock_resolve,
        ):
            result = lc._do_connecting()

        assert result is True
        mock_resolve.assert_not_called()

    def test_reverts_to_setup_on_wifi_failure(self):
        config = _make_config()
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)

        with (
            patch.object(lc, "_wait_for_wifi", return_value=False),
            patch.object(lc, "_revert_to_setup") as mock_revert,
        ):
            result = lc._do_connecting()

        assert result is False
        mock_revert.assert_called_once()


class TestValidating:
    """Test VALIDATING state — camera hardware check."""

    @patch("camera_streamer.lifecycle.CaptureManager")
    def test_passes_when_camera_ok(self, MockCapture):
        config = _make_config()
        platform = _make_platform()

        capture = MagicMock()
        capture.check.return_value = True
        capture.device = "/dev/video0"
        capture.supports_h264.return_value = True
        MockCapture.return_value = capture

        lc = CameraLifecycle(config, platform, lambda: False)
        result = lc._do_validating()

        assert result is True
        assert lc._capture is capture

    @patch("camera_streamer.lifecycle.CaptureManager")
    def test_continues_when_camera_fails(self, MockCapture):
        """Camera failure shouldn't block startup."""
        capture = MagicMock()
        capture.check.return_value = False
        MockCapture.return_value = capture

        config = _make_config()
        platform = _make_platform()

        lc = CameraLifecycle(config, platform, lambda: False)
        result = lc._do_validating()

        assert result is True  # Still returns True


class TestRunning:
    """Test RUNNING state — all services started."""

    @patch("camera_streamer.lifecycle.led")
    @patch("camera_streamer.lifecycle.HealthMonitor")
    @patch("camera_streamer.lifecycle.CameraStatusServer")
    @patch("camera_streamer.lifecycle.StreamManager")
    @patch("camera_streamer.lifecycle.DiscoveryService")
    def test_starts_all_services(
        self, MockDiscovery, MockStream, MockStatus, MockHealth, mock_led
    ):
        config = _make_config()
        platform = _make_platform()

        # Shutdown after first iteration
        calls = [0]

        def shutdown():
            calls[0] += 1
            return calls[0] > 1

        lc = CameraLifecycle(config, platform, shutdown)
        lc._capture = MagicMock()

        result = lc._do_running()

        assert result is True
        MockDiscovery.return_value.start.assert_called_once()
        MockStream.return_value.start.assert_called_once()
        MockStatus.return_value.start.assert_called_once()
        MockHealth.return_value.start.assert_called_once()
        mock_led.connected.assert_called_once()

    @patch("camera_streamer.lifecycle.led")
    @patch("camera_streamer.lifecycle.HealthMonitor")
    @patch("camera_streamer.lifecycle.CameraStatusServer")
    @patch("camera_streamer.lifecycle.StreamManager")
    @patch("camera_streamer.lifecycle.DiscoveryService")
    def test_skips_streaming_when_unconfigured(
        self, MockDiscovery, MockStream, MockStatus, MockHealth, mock_led
    ):
        config = _make_config(is_configured=False)
        platform = _make_platform()

        lc = CameraLifecycle(config, platform, lambda: True)
        lc._capture = MagicMock()

        lc._do_running()

        MockStream.return_value.start.assert_not_called()


class TestShutdown:
    """Test shutdown teardown."""

    def test_stops_all_services(self):
        config = _make_config()
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)

        lc._health = MagicMock()
        lc._stream = MagicMock()
        lc._status_server = MagicMock()
        lc._discovery = MagicMock()

        lc.shutdown()

        assert lc.state == State.SHUTDOWN
        lc._health.stop.assert_called_once()
        lc._stream.stop.assert_called_once()
        lc._status_server.stop.assert_called_once()
        lc._discovery.stop.assert_called_once()

    def test_handles_none_services(self):
        """Shutdown should work even if services were never started."""
        config = _make_config()
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)

        # All components are None by default
        lc.shutdown()  # Should not raise
        assert lc.state == State.SHUTDOWN


class TestFullLifecycle:
    """Test run() method with full state transitions."""

    @patch("camera_streamer.lifecycle.led")
    @patch("camera_streamer.lifecycle.LedController")
    @patch("camera_streamer.lifecycle.HealthMonitor")
    @patch("camera_streamer.lifecycle.CameraStatusServer")
    @patch("camera_streamer.lifecycle.StreamManager")
    @patch("camera_streamer.lifecycle.DiscoveryService")
    @patch("camera_streamer.lifecycle.CaptureManager")
    @patch("camera_streamer.lifecycle.WifiSetupServer")
    def test_full_run(
        self,
        MockSetup,
        MockCapture,
        MockDiscovery,
        MockStream,
        MockStatus,
        MockHealth,
        MockLedCtrl,
        mock_led,
    ):
        config = _make_config()
        platform = _make_platform()

        setup = MagicMock()
        setup.needs_setup.return_value = False
        MockSetup.return_value = setup

        capture = MagicMock()
        capture.check.return_value = True
        MockCapture.return_value = capture

        # Shutdown immediately in running state
        calls = [0]

        def shutdown():
            calls[0] += 1
            return calls[0] > 5  # Allow a few state transitions

        lc = CameraLifecycle(config, platform, shutdown)

        with (
            patch.object(lc, "_wait_for_wifi", return_value=True),
            patch.object(lc, "_resolve_server"),
        ):
            lc.run()

        assert lc.state == State.SHUTDOWN

    def test_immediate_shutdown(self):
        """Run with immediate shutdown should not crash."""
        config = _make_config()
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: True)
        lc.run()
        assert lc.state == State.SHUTDOWN


class TestWaitForWifi:
    """Test WiFi connectivity check."""

    @patch("camera_streamer.lifecycle.subprocess")
    def test_returns_true_when_ip_found(self, mock_subprocess):
        config = _make_config()
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)
        lc.WIFI_TIMEOUT = 5

        result = MagicMock()
        result.stdout = "IP4.ADDRESS[1]:192.168.1.50/24\n"
        mock_subprocess.run.return_value = result

        assert lc._wait_for_wifi() is True

    @patch("camera_streamer.lifecycle.subprocess")
    @patch("camera_streamer.lifecycle.time")
    def test_returns_false_on_timeout(self, mock_time, mock_subprocess):
        config = _make_config()
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)
        lc.WIFI_TIMEOUT = 2

        result = MagicMock()
        result.stdout = ""
        mock_subprocess.run.return_value = result

        assert lc._wait_for_wifi() is False

    def test_returns_true_on_shutdown(self):
        """Should not block shutdown."""
        config = _make_config()
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: True)
        lc.WIFI_TIMEOUT = 60

        assert lc._wait_for_wifi() is True


class TestResolveServer:
    """Test server address resolution."""

    @patch("camera_streamer.lifecycle.socket")
    def test_resolves_address(self, mock_socket):
        config = _make_config(server_ip="homemonitor.local")
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)

        mock_socket.gethostbyname.return_value = "192.168.1.100"
        lc._resolve_server()
        mock_socket.gethostbyname.assert_called_once_with("homemonitor.local")

    def test_handles_resolution_failure(self):
        import socket as real_socket

        config = _make_config(server_ip="homemonitor.local")
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)

        with patch("camera_streamer.lifecycle.socket") as mock_socket:
            mock_socket.gaierror = real_socket.gaierror
            mock_socket.gethostbyname.side_effect = real_socket.gaierror("DNS failed")
            # Should not raise
            lc._resolve_server()

    def test_skips_when_no_server_ip(self):
        config = _make_config(server_ip="")
        platform = _make_platform()
        lc = CameraLifecycle(config, platform, lambda: False)
        # Should not raise
        lc._resolve_server()


class TestRevertToSetup:
    """Test setup revert mechanism."""

    @patch("camera_streamer.lifecycle.subprocess")
    @patch("camera_streamer.lifecycle.os")
    def test_removes_stamp_and_restarts(self, mock_os, mock_subprocess):
        mock_os.path.isfile.return_value = True

        CameraLifecycle._revert_to_setup()

        mock_os.remove.assert_called_once_with("/data/.setup-done")
        mock_subprocess.run.assert_called_once()

    @patch("camera_streamer.lifecycle.subprocess")
    @patch("camera_streamer.lifecycle.os")
    def test_handles_missing_stamp(self, mock_os, mock_subprocess):
        mock_os.path.isfile.return_value = False

        CameraLifecycle._revert_to_setup()

        mock_os.remove.assert_not_called()

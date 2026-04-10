"""Tests for camera_streamer main entry point."""
import signal
from unittest.mock import patch, MagicMock

from camera_streamer.main import main, _handle_signal
import camera_streamer.main as main_module


class TestMain:
    """Test the main entry point."""

    def test_main_callable(self):
        assert callable(main)

    @patch("camera_streamer.main._resolve_server")
    @patch("camera_streamer.wifi_setup.WifiSetupServer")
    @patch("camera_streamer.health.HealthMonitor")
    @patch("camera_streamer.stream.StreamManager")
    @patch("camera_streamer.discovery.DiscoveryService")
    @patch("camera_streamer.capture.CaptureManager")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_startup_sequence(
        self, MockConfig, MockCapture, MockDiscovery,
        MockStream, MockHealth, MockSetup, mock_resolve
    ):
        """Main should initialize all components in order."""
        config = MagicMock()
        config.is_configured = True
        config.camera_id = "cam-test"
        config.data_dir = "/tmp/test"
        config.server_ip = "homemonitor.local"
        MockConfig.return_value = config
        config.load.return_value = config

        setup = MagicMock()
        setup.needs_setup.return_value = False
        MockSetup.return_value = setup

        capture = MagicMock()
        capture.check.return_value = True
        MockCapture.return_value = capture

        discovery = MagicMock()
        MockDiscovery.return_value = discovery

        stream = MagicMock()
        stream.start.return_value = True
        MockStream.return_value = stream

        health = MagicMock()
        MockHealth.return_value = health

        def trigger_shutdown(*args, **kwargs):
            main_module._shutdown = True
        health.start.side_effect = trigger_shutdown

        main_module._shutdown = False
        main()

        # Verify startup order
        config.load.assert_called()
        setup.needs_setup.assert_called()
        capture.check.assert_called_once()
        discovery.start.assert_called_once()
        stream.start.assert_called_once()
        health.start.assert_called_once()

        # Verify shutdown
        health.stop.assert_called_once()
        stream.stop.assert_called_once()
        discovery.stop.assert_called_once()

    @patch("camera_streamer.main._resolve_server")
    @patch("camera_streamer.wifi_setup.WifiSetupServer")
    @patch("camera_streamer.health.HealthMonitor")
    @patch("camera_streamer.stream.StreamManager")
    @patch("camera_streamer.discovery.DiscoveryService")
    @patch("camera_streamer.capture.CaptureManager")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_skips_stream_when_unconfigured(
        self, MockConfig, MockCapture, MockDiscovery,
        MockStream, MockHealth, MockSetup, mock_resolve
    ):
        """Should not start streaming if server not configured."""
        config = MagicMock()
        config.is_configured = False
        config.camera_id = "cam-test"
        config.data_dir = "/tmp/test"
        MockConfig.return_value = config
        config.load.return_value = config

        setup = MagicMock()
        setup.needs_setup.return_value = False
        MockSetup.return_value = setup

        capture = MagicMock()
        MockCapture.return_value = capture

        discovery = MagicMock()
        MockDiscovery.return_value = discovery

        stream = MagicMock()
        MockStream.return_value = stream

        health = MagicMock()
        MockHealth.return_value = health

        def trigger_shutdown(*args, **kwargs):
            main_module._shutdown = True
        health.start.side_effect = trigger_shutdown

        main_module._shutdown = False
        main()

        stream.start.assert_not_called()


class TestSignalHandler:
    """Test signal handling."""

    def test_handle_signal_sets_shutdown(self):
        """Signal handler should set _shutdown flag."""
        main_module._shutdown = False
        _handle_signal(signal.SIGTERM, None)
        assert main_module._shutdown is True

    def test_handle_sigint(self):
        """Should handle SIGINT same as SIGTERM."""
        main_module._shutdown = False
        _handle_signal(signal.SIGINT, None)
        assert main_module._shutdown is True

"""Tests for main.py setup-mode and edge cases."""
import signal
from unittest.mock import patch, MagicMock, call

from camera_streamer.main import main
import camera_streamer.main as main_module


class TestMainSetupMode:
    """Test main() behavior during first-boot setup."""

    @patch("camera_streamer.wifi_setup.WifiSetupServer")
    @patch("camera_streamer.health.HealthMonitor")
    @patch("camera_streamer.stream.StreamManager")
    @patch("camera_streamer.discovery.DiscoveryService")
    @patch("camera_streamer.capture.CaptureManager")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_waits_for_setup(
        self, MockConfig, MockCapture, MockDiscovery,
        MockStream, MockHealth, MockSetup
    ):
        """Main should block while waiting for setup to complete."""
        config = MagicMock()
        config.is_configured = True
        config.camera_id = "cam-test"
        config.data_dir = "/tmp/test"
        MockConfig.return_value = config
        config.load.return_value = config

        # needs_setup returns True first time, then False (setup completes)
        call_count = [0]
        def needs_setup_side_effect():
            call_count[0] += 1
            return call_count[0] < 3

        setup = MagicMock()
        setup.needs_setup.side_effect = needs_setup_side_effect
        setup.start.return_value = True
        MockSetup.return_value = setup

        capture = MagicMock()
        MockCapture.return_value = capture

        discovery = MagicMock()
        MockDiscovery.return_value = discovery

        stream = MagicMock()
        MockStream.return_value = stream

        health = MagicMock()
        MockHealth.return_value = health
        health.start.side_effect = lambda: setattr(main_module, '_shutdown', True)

        main_module._shutdown = False
        main()

        # Setup was started and stopped
        setup.start.assert_called_once()
        setup.stop.assert_called_once()
        # Config was reloaded after setup
        assert config.load.call_count >= 2

    @patch("camera_streamer.wifi_setup.WifiSetupServer")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_shutdown_during_setup(self, MockConfig, MockSetup):
        """Shutdown signal during setup should exit cleanly."""
        config = MagicMock()
        config.camera_id = "cam-test"
        config.data_dir = "/tmp/test"
        MockConfig.return_value = config
        config.load.return_value = config

        # needs_setup always True but we shut down
        def needs_and_shutdown():
            main_module._shutdown = True
            return True

        setup = MagicMock()
        setup.needs_setup.side_effect = needs_and_shutdown
        setup.start.return_value = True
        MockSetup.return_value = setup

        main_module._shutdown = False
        main()

        setup.stop.assert_called_once()


class TestMainCaptureFailure:
    """Test main() when camera device isn't available."""

    @patch("camera_streamer.wifi_setup.WifiSetupServer")
    @patch("camera_streamer.health.HealthMonitor")
    @patch("camera_streamer.stream.StreamManager")
    @patch("camera_streamer.discovery.DiscoveryService")
    @patch("camera_streamer.capture.CaptureManager")
    @patch("camera_streamer.config.ConfigManager")
    def test_continues_without_camera(
        self, MockConfig, MockCapture, MockDiscovery,
        MockStream, MockHealth, MockSetup
    ):
        """Should continue running even if camera check fails."""
        config = MagicMock()
        config.is_configured = True
        config.camera_id = "cam-test"
        config.data_dir = "/tmp/test"
        MockConfig.return_value = config
        config.load.return_value = config

        setup = MagicMock()
        setup.needs_setup.return_value = False
        MockSetup.return_value = setup

        capture = MagicMock()
        capture.check.return_value = False  # Camera not available
        MockCapture.return_value = capture

        discovery = MagicMock()
        MockDiscovery.return_value = discovery

        stream = MagicMock()
        MockStream.return_value = stream

        health = MagicMock()
        MockHealth.return_value = health
        health.start.side_effect = lambda: setattr(main_module, '_shutdown', True)

        main_module._shutdown = False
        main()

        # Should still start discovery and streaming
        discovery.start.assert_called_once()
        stream.start.assert_called_once()

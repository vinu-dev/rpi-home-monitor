"""Tests for camera_streamer main entry point."""

import signal
from unittest.mock import MagicMock, patch

import camera_streamer.main as main_module
from camera_streamer.main import _handle_signal, main


class TestMain:
    """Test the main entry point."""

    def test_main_callable(self):
        assert callable(main)

    @patch("camera_streamer.lifecycle.CameraLifecycle")
    @patch("camera_streamer.platform.Platform")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_creates_lifecycle_and_runs(
        self, MockConfig, MockPlatform, MockLifecycle
    ):
        """Main should load config, detect platform, create lifecycle, and run."""
        config = MagicMock()
        config.camera_id = "cam-test"
        MockConfig.return_value = config

        platform = MagicMock()
        MockPlatform.detect.return_value = platform

        lifecycle = MagicMock()
        MockLifecycle.return_value = lifecycle

        main_module._shutdown = False
        main()

        config.load.assert_called_once()
        MockPlatform.detect.assert_called_once()
        MockLifecycle.assert_called_once()
        lifecycle.run.assert_called_once()

    @patch("camera_streamer.lifecycle.CameraLifecycle")
    @patch("camera_streamer.platform.Platform")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_passes_shutdown_event(self, MockConfig, MockPlatform, MockLifecycle):
        """Shutdown event callable should reflect _shutdown flag."""
        config = MagicMock()
        config.camera_id = "cam-test"
        MockConfig.return_value = config
        MockPlatform.detect.return_value = MagicMock()

        lifecycle = MagicMock()
        MockLifecycle.return_value = lifecycle

        main_module._shutdown = False
        main()

        # Get the shutdown_event kwarg passed to CameraLifecycle
        call_kwargs = MockLifecycle.call_args[1]
        shutdown_fn = call_kwargs["shutdown_event"]

        main_module._shutdown = False
        assert shutdown_fn() is False

        main_module._shutdown = True
        assert shutdown_fn() is True


class TestSignalHandler:
    """Test signal handling."""

    def test_handle_signal_sets_shutdown(self):
        main_module._shutdown = False
        _handle_signal(signal.SIGTERM, None)
        assert main_module._shutdown is True

    def test_handle_sigint(self):
        main_module._shutdown = False
        _handle_signal(signal.SIGINT, None)
        assert main_module._shutdown is True

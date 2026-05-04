# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
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
    @patch("camera_streamer.watchdog_notifier.WatchdogNotifier")
    @patch("camera_streamer.platform.Platform")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_creates_lifecycle_and_runs(
        self, MockConfig, MockPlatform, MockWatchdog, MockLifecycle
    ):
        """Main should load config, detect platform, create lifecycle, and run."""
        config = MagicMock()
        config.camera_id = "cam-test"
        MockConfig.return_value = config

        platform = MagicMock()
        MockPlatform.detect.return_value = platform

        lifecycle = MagicMock()
        MockLifecycle.return_value = lifecycle
        watchdog = MagicMock()
        MockWatchdog.return_value = watchdog

        main_module._shutdown = False
        main()

        config.load.assert_called_once()
        MockPlatform.detect.assert_called_once()
        MockLifecycle.assert_called_once()
        watchdog.start.assert_called_once()
        watchdog.stop.assert_called_once_with(stopping=False)
        lifecycle.run.assert_called_once()

    @patch("camera_streamer.lifecycle.CameraLifecycle")
    @patch("camera_streamer.watchdog_notifier.WatchdogNotifier")
    @patch("camera_streamer.platform.Platform")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_passes_shutdown_event(
        self, MockConfig, MockPlatform, MockWatchdog, MockLifecycle
    ):
        """Shutdown event callable should reflect _shutdown flag."""
        config = MagicMock()
        config.camera_id = "cam-test"
        MockConfig.return_value = config
        MockPlatform.detect.return_value = MagicMock()
        MockWatchdog.return_value = MagicMock()

        lifecycle = MagicMock()
        MockLifecycle.return_value = lifecycle

        main_module._shutdown = False
        main()

        # Get the shutdown_event kwarg passed to CameraLifecycle
        call_kwargs = MockLifecycle.call_args[1]
        shutdown_fn = call_kwargs["shutdown_event"]
        assert call_kwargs["notifier"] is MockWatchdog.return_value

        main_module._shutdown = False
        assert shutdown_fn() is False

        main_module._shutdown = True
        assert shutdown_fn() is True


class TestSignalHandler:
    """Test signal handling."""

    def test_handle_signal_sets_shutdown(self):
        main_module._shutdown = False
        main_module._watchdog_notifier = MagicMock()
        with patch("camera_streamer.main.threading.Timer"):
            _handle_signal(signal.SIGTERM, None)
        assert main_module._shutdown is True
        main_module._watchdog_notifier.stop.assert_called_once_with(stopping=True)
        main_module._watchdog_notifier = None

    def test_handle_sigint(self):
        main_module._shutdown = False
        main_module._watchdog_notifier = MagicMock()
        with patch("camera_streamer.main.threading.Timer"):
            _handle_signal(signal.SIGINT, None)
        assert main_module._shutdown is True
        main_module._watchdog_notifier.stop.assert_called_once_with(stopping=True)
        main_module._watchdog_notifier = None

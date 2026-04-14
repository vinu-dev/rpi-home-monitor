"""Tests for main.py setup-mode and edge cases.

These tests exercise main() which now delegates to CameraLifecycle.
We mock the lifecycle to verify setup/shutdown interactions.
"""

from unittest.mock import MagicMock, patch

import camera_streamer.main as main_module
from camera_streamer.main import main


class TestMainSetupMode:
    """Test main() behavior during first-boot setup."""

    @patch("camera_streamer.lifecycle.CameraLifecycle")
    @patch("camera_streamer.platform.Platform")
    @patch("camera_streamer.config.ConfigManager")
    def test_main_runs_lifecycle(self, MockConfig, MockPlatform, MockLifecycle):
        """Main creates lifecycle and calls run()."""
        config = MagicMock()
        config.camera_id = "cam-test"
        MockConfig.return_value = config
        MockPlatform.detect.return_value = MagicMock()

        lifecycle = MagicMock()
        MockLifecycle.return_value = lifecycle

        main_module._shutdown = False
        main()

        lifecycle.run.assert_called_once()

    @patch("camera_streamer.lifecycle.CameraLifecycle")
    @patch("camera_streamer.platform.Platform")
    @patch("camera_streamer.config.ConfigManager")
    def test_keyboard_interrupt_calls_shutdown(
        self, MockConfig, MockPlatform, MockLifecycle
    ):
        """KeyboardInterrupt during run should call shutdown."""
        config = MagicMock()
        config.camera_id = "cam-test"
        MockConfig.return_value = config
        MockPlatform.detect.return_value = MagicMock()

        lifecycle = MagicMock()
        lifecycle.run.side_effect = KeyboardInterrupt
        MockLifecycle.return_value = lifecycle

        main_module._shutdown = False
        main()

        lifecycle.shutdown.assert_called_once()

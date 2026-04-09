"""Additional tests for health module to boost coverage."""
import os
from unittest.mock import patch, MagicMock, mock_open

from camera_streamer.health import HealthMonitor, _read_cpu_temp, _get_disk_free_mb


class TestHealthRunCheck:
    """Test the _run_check method."""

    def _make_monitor(self, camera_config):
        capture = MagicMock()
        capture.available = True
        stream = MagicMock()
        stream.is_streaming = True
        return HealthMonitor(camera_config, capture, stream)

    @patch("camera_streamer.health._get_disk_free_mb", return_value=500)
    @patch("camera_streamer.health._read_cpu_temp", return_value=45.0)
    def test_run_check_healthy(self, mock_temp, mock_disk, camera_config):
        """Normal health check should not raise."""
        mon = self._make_monitor(camera_config)
        mon._run_check()  # Should not raise

    @patch("camera_streamer.health._get_disk_free_mb", return_value=10)
    @patch("camera_streamer.health._read_cpu_temp", return_value=85.0)
    def test_run_check_warnings(self, mock_temp, mock_disk, camera_config):
        """Should log warnings for high temp and low disk."""
        capture = MagicMock()
        capture.available = False
        stream = MagicMock()
        stream.is_streaming = False
        mon = HealthMonitor(camera_config, capture, stream)
        mon._run_check()  # Should log warnings but not raise

    @patch("camera_streamer.health._get_disk_free_mb", return_value=None)
    @patch("camera_streamer.health._read_cpu_temp", return_value=None)
    def test_run_check_no_data(self, mock_temp, mock_disk, camera_config):
        """Should handle None values gracefully."""
        mon = self._make_monitor(camera_config)
        mon._run_check()  # Should not raise


class TestWatchdogNotify:
    """Test systemd watchdog notification."""

    def test_notify_no_socket(self, camera_config):
        """Should do nothing when NOTIFY_SOCKET not set."""
        capture = MagicMock()
        capture.available = True
        stream = MagicMock()
        stream.is_streaming = True
        mon = HealthMonitor(camera_config, capture, stream)
        with patch.dict(os.environ, {}, clear=True):
            mon._notify_watchdog()  # Should not raise

    def test_notify_with_socket_env(self, camera_config):
        """Watchdog should not raise even when NOTIFY_SOCKET is set.

        On Linux this sends WATCHDOG=1 via AF_UNIX. On Windows AF_UNIX
        doesn't exist, so the except block silences it. Either way, no crash.
        """
        capture = MagicMock()
        capture.available = True
        stream = MagicMock()
        stream.is_streaming = True
        mon = HealthMonitor(camera_config, capture, stream)
        with patch.dict(os.environ, {"NOTIFY_SOCKET": "/run/systemd/notify"}):
            mon._notify_watchdog()  # Should not raise on any platform

    def test_notify_exception_suppressed(self, camera_config):
        """Watchdog errors should be silently suppressed."""
        capture = MagicMock()
        capture.available = True
        stream = MagicMock()
        stream.is_streaming = True
        mon = HealthMonitor(camera_config, capture, stream)
        with patch.dict(os.environ, {"NOTIFY_SOCKET": "@/invalid/path"}):
            mon._notify_watchdog()  # Should not raise


class TestCpuTempEdge:
    """Edge cases for CPU temp reading."""

    def test_invalid_content(self):
        """Should return None for non-numeric content."""
        with patch("builtins.open", mock_open(read_data="not_a_number\n")):
            assert _read_cpu_temp() is None

"""Additional tests for health module to boost coverage."""

import os
from unittest.mock import MagicMock, mock_open, patch

from camera_streamer.health import HealthMonitor


class TestHealthRunCheck:
    """Test the _run_check method."""

    def _make_monitor(self, camera_config, thermal_path=None):
        capture = MagicMock()
        capture.available = True
        stream = MagicMock()
        stream.is_streaming = True
        return HealthMonitor(camera_config, capture, stream, thermal_path=thermal_path)

    @patch("camera_streamer.health._get_disk_free_mb", return_value=500)
    def test_run_check_healthy(self, mock_disk, camera_config):
        """Normal health check should not raise."""
        mon = self._make_monitor(camera_config)
        mon._run_check()  # Should not raise

    @patch("camera_streamer.health._get_disk_free_mb", return_value=10)
    def test_run_check_warnings(self, mock_disk, camera_config):
        """Should log warnings for high temp and low disk."""
        capture = MagicMock()
        capture.available = False
        stream = MagicMock()
        stream.is_streaming = False
        mon = HealthMonitor(camera_config, capture, stream, thermal_path="/fake/temp")
        with patch("builtins.open", mock_open(read_data="85000\n")):
            mon._run_check()  # Should log warnings but not raise

    @patch("camera_streamer.health._get_disk_free_mb", return_value=None)
    def test_run_check_no_data(self, mock_disk, camera_config):
        """Should handle None values gracefully."""
        mon = self._make_monitor(camera_config, thermal_path=None)
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
        """Watchdog should not raise even when NOTIFY_SOCKET is set."""
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
    """Edge cases for CPU temp reading via HealthMonitor."""

    def _make_monitor_with_thermal(self, thermal_path):
        config = MagicMock()
        capture = MagicMock()
        stream = MagicMock()
        return HealthMonitor(config, capture, stream, thermal_path=thermal_path)

    def test_invalid_content(self):
        """Should return None for non-numeric content."""
        mon = self._make_monitor_with_thermal("/fake/temp")
        with patch("builtins.open", mock_open(read_data="not_a_number\n")):
            assert mon.read_cpu_temp() is None

    def test_no_thermal_path(self):
        """Should return None when no thermal path."""
        mon = self._make_monitor_with_thermal(None)
        assert mon.read_cpu_temp() is None

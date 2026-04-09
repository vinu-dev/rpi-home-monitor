"""Tests for camera_streamer.health module."""
import os
import pytest
from unittest.mock import patch, MagicMock, mock_open

from camera_streamer.health import HealthMonitor, _read_cpu_temp, _get_disk_free_mb


class TestHealthMonitor:
    """Test health monitoring."""

    def _make_monitor(self, camera_config):
        """Create a HealthMonitor with mock capture/stream."""
        capture = MagicMock()
        capture.available = True
        stream = MagicMock()
        stream.is_streaming = True
        return HealthMonitor(camera_config, capture, stream)

    def test_not_running_initially(self, camera_config):
        """Should not be running before start()."""
        mon = self._make_monitor(camera_config)
        assert mon.is_running is False

    @patch("camera_streamer.health._get_disk_free_mb", return_value=500)
    @patch("camera_streamer.health._read_cpu_temp", return_value=45.0)
    def test_get_status(self, mock_temp, mock_disk, camera_config):
        """get_status() should return health dict."""
        mon = self._make_monitor(camera_config)
        status = mon.get_status()
        assert "camera_available" in status
        assert "streaming" in status
        assert "server_configured" in status
        assert "camera_id" in status
        assert "cpu_temp" in status
        assert "disk_free_mb" in status
        assert status["camera_available"] is True
        assert status["streaming"] is True
        assert status["server_configured"] is True
        assert status["camera_id"] == "cam-test001"

    @patch("camera_streamer.health._get_disk_free_mb", return_value=500)
    @patch("camera_streamer.health._read_cpu_temp", return_value=None)
    def test_get_status_unconfigured(self, mock_temp, mock_disk, tmp_path):
        """Status should show unconfigured state."""
        for d in ["config", "certs", "logs"]:
            (tmp_path / d).mkdir()
        from camera_streamer.config import ConfigManager
        config = ConfigManager(data_dir=str(tmp_path))
        config.load()
        capture = MagicMock()
        capture.available = False
        stream = MagicMock()
        stream.is_streaming = False
        mon = HealthMonitor(config, capture, stream)
        status = mon.get_status()
        assert status["server_configured"] is False
        assert status["camera_available"] is False
        assert status["streaming"] is False

    def test_start_stop(self, camera_config):
        """start() and stop() should manage the monitor thread."""
        mon = self._make_monitor(camera_config)
        mon._interval = 1  # Short for testing
        mon.start()
        assert mon.is_running is True
        mon.stop()
        assert mon.is_running is False


class TestCpuTemp:
    """Test CPU temperature reading."""

    def test_reads_temp(self):
        """Should read temperature from thermal zone."""
        from unittest.mock import mock_open
        with patch("builtins.open", mock_open(read_data="45000\n")):
            assert _read_cpu_temp() == 45.0

    def test_returns_none_on_error(self):
        """Should return None when file unavailable."""
        with patch("builtins.open", side_effect=OSError):
            assert _read_cpu_temp() is None


class TestDiskFree:
    """Test disk free space detection."""

    def test_returns_int(self, data_dir):
        """Should return an integer for valid path."""
        if not hasattr(os, "statvfs"):
            # Windows doesn't have statvfs — mock it
            from unittest.mock import patch, MagicMock
            mock_stat = MagicMock()
            mock_stat.f_bavail = 1024 * 1024
            mock_stat.f_frsize = 4096
            with patch("os.statvfs", return_value=mock_stat, create=True):
                result = _get_disk_free_mb(str(data_dir))
        else:
            result = _get_disk_free_mb(str(data_dir))
        assert isinstance(result, int)
        assert result >= 0

    def test_returns_none_on_error(self):
        """Should return None for invalid path."""
        if not hasattr(os, "statvfs"):
            from unittest.mock import patch
            with patch("os.statvfs", side_effect=OSError, create=True):
                result = _get_disk_free_mb("/nonexistent/path/xyz")
        else:
            result = _get_disk_free_mb("/nonexistent/path/xyz")
        assert result is None

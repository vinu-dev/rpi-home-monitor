"""Tests for the health monitoring service."""

from collections import namedtuple
from unittest.mock import mock_open, patch

from monitor.services.health import (
    get_cpu_temperature,
    get_cpu_usage,
    get_disk_usage,
    get_health_summary,
    get_memory_info,
    get_uptime,
)


class TestCPUTemperature:
    """Test CPU temperature reading."""

    @patch("monitor.services.health.Path.read_text", return_value="54200\n")
    def test_reads_temperature(self, mock_read):
        assert get_cpu_temperature() == 54.2

    @patch("monitor.services.health.Path.read_text", return_value="72500\n")
    def test_high_temperature(self, mock_read):
        assert get_cpu_temperature() == 72.5

    @patch("monitor.services.health.Path.read_text", side_effect=OSError)
    def test_returns_zero_on_error(self, mock_read):
        assert get_cpu_temperature() == 0.0


class TestCPUUsage:
    """Test CPU usage reading."""

    def test_returns_float(self):
        result = get_cpu_usage()
        assert isinstance(result, float)


class TestMemoryInfo:
    """Test memory info reading."""

    @patch(
        "builtins.open",
        mock_open(
            read_data=(
                "MemTotal:       16384000 kB\n"
                "MemFree:         2048000 kB\n"
                "MemAvailable:    8192000 kB\n"
                "Buffers:          512000 kB\n"
            )
        ),
    )
    def test_reads_memory(self):
        info = get_memory_info()
        assert info["total_mb"] == 16000
        assert info["free_mb"] == 8000
        assert info["used_mb"] == 8000
        assert info["percent"] == 50.0

    @patch("builtins.open", side_effect=OSError)
    def test_returns_zeros_on_error(self, mock_file):
        info = get_memory_info()
        assert info["total_mb"] == 0
        assert info["percent"] == 0.0


class TestDiskUsage:
    """Test disk usage reading."""

    @patch("shutil.disk_usage")
    def test_reads_disk(self, mock_du):
        # 100GB total, 40GB used, 60GB free
        DiskUsage = namedtuple("usage", ["total", "used", "free"])
        mock_du.return_value = DiskUsage(107374182400, 42949672960, 64424509440)
        info = get_disk_usage("/data")
        assert info["total_gb"] == 100.0
        assert info["used_gb"] == 40.0
        assert info["free_gb"] == 60.0
        assert info["percent"] == 40.0

    @patch("shutil.disk_usage", side_effect=OSError)
    def test_returns_zeros_on_error(self, mock_du):
        info = get_disk_usage("/nonexistent")
        assert info["total_gb"] == 0
        assert info["percent"] == 0.0

    def test_reads_real_path(self, tmp_path):
        """Test with a real path (tmp_path exists)."""
        info = get_disk_usage(str(tmp_path))
        assert info["total_gb"] > 0


class TestUptime:
    """Test uptime reading."""

    @patch(
        "monitor.services.health.Path.read_text", return_value="90061.23 360000.00\n"
    )
    def test_reads_uptime(self, mock_read):
        info = get_uptime()
        assert info["seconds"] == 90061
        assert "1d" in info["display"]
        assert "1h" in info["display"]

    @patch("monitor.services.health.Path.read_text", return_value="3661.00 7000.00\n")
    def test_hours_and_minutes(self, mock_read):
        info = get_uptime()
        assert info["seconds"] == 3661
        assert "1h" in info["display"]
        assert "1m" in info["display"]

    @patch("monitor.services.health.Path.read_text", return_value="120.50 240.00\n")
    def test_just_minutes(self, mock_read):
        info = get_uptime()
        assert info["seconds"] == 120
        assert info["display"] == "2m"

    @patch("monitor.services.health.Path.read_text", side_effect=OSError)
    def test_returns_zero_on_error(self, mock_read):
        info = get_uptime()
        assert info["seconds"] == 0


class TestHealthSummary:
    """Test the combined health summary."""

    @patch("monitor.services.health.get_cpu_temperature", return_value=55.0)
    @patch("monitor.services.health.get_cpu_usage", return_value=25.0)
    @patch(
        "monitor.services.health.get_memory_info",
        return_value={
            "total_mb": 4096,
            "used_mb": 2048,
            "free_mb": 2048,
            "percent": 50.0,
        },
    )
    @patch(
        "monitor.services.health.get_disk_usage",
        return_value={"total_gb": 100, "used_gb": 40, "free_gb": 60, "percent": 40.0},
    )
    @patch(
        "monitor.services.health.get_uptime",
        return_value={"seconds": 3600, "display": "1h 0m"},
    )
    def test_healthy_system(self, *mocks):
        summary = get_health_summary("/data")
        assert summary["status"] == "healthy"
        assert summary["warnings"] == []
        assert summary["cpu_temp_c"] == 55.0
        assert summary["memory"]["percent"] == 50.0

    @patch("monitor.services.health.get_cpu_temperature", return_value=75.0)
    @patch("monitor.services.health.get_cpu_usage", return_value=90.0)
    @patch(
        "monitor.services.health.get_memory_info",
        return_value={
            "total_mb": 4096,
            "used_mb": 3800,
            "free_mb": 296,
            "percent": 92.8,
        },
    )
    @patch(
        "monitor.services.health.get_disk_usage",
        return_value={"total_gb": 100, "used_gb": 90, "free_gb": 10, "percent": 90.0},
    )
    @patch(
        "monitor.services.health.get_uptime",
        return_value={"seconds": 3600, "display": "1h 0m"},
    )
    def test_system_with_warnings(self, *mocks):
        summary = get_health_summary("/data")
        assert summary["status"] == "warning"
        assert len(summary["warnings"]) == 3

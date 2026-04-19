"""Unit tests for monitor.services.health — all platform-specific I/O is mocked."""

from unittest.mock import MagicMock, mock_open, patch

from monitor.services.health import (
    get_cpu_temperature,
    get_cpu_usage,
    get_disk_usage,
    get_health_summary,
    get_memory_info,
    get_network_info,
    get_uptime,
)

# ===========================================================================
# get_cpu_temperature
# ===========================================================================


class TestGetCpuTemperature:
    def test_reads_and_converts_millidegrees(self, tmp_path):
        thermal = tmp_path / "thermal_zone0" / "temp"
        thermal.parent.mkdir(parents=True)
        thermal.write_text("55000\n")
        with patch("monitor.services.health.Path") as mock_path_cls:
            mock_path_cls.return_value = thermal
            result = get_cpu_temperature()
        assert result == 55.0

    def test_returns_zero_on_oserror(self):
        with patch("monitor.services.health.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.side_effect = OSError("no sysfs")
            result = get_cpu_temperature()
        assert result == 0.0

    def test_returns_zero_on_invalid_content(self, tmp_path):
        thermal = tmp_path / "temp"
        thermal.write_text("not-a-number\n")
        with patch("monitor.services.health.Path") as mock_path_cls:
            mock_path_cls.return_value = thermal
            result = get_cpu_temperature()
        assert result == 0.0


# ===========================================================================
# get_cpu_usage
# ===========================================================================


class TestGetCpuUsage:
    def test_first_call_returns_zero(self):
        import monitor.services.health as h

        h._prev_cpu_sample = None
        with patch(
            "monitor.services.health._read_cpu_times",
            return_value=(100.0, 0.0, 50.0, 850.0, 0.0, 0.0, 0.0),
        ):
            result = get_cpu_usage()
        assert result == 0.0

    def test_second_call_computes_delta(self):
        import monitor.services.health as h

        h._prev_cpu_sample = None
        # Prime the baseline
        with patch(
            "monitor.services.health._read_cpu_times",
            return_value=(100.0, 0.0, 50.0, 850.0, 0.0, 0.0, 0.0),
        ):
            get_cpu_usage()
        # Second sample: 50 more active ticks, 950 more idle → 5% usage
        with patch(
            "monitor.services.health._read_cpu_times",
            return_value=(150.0, 0.0, 100.0, 1800.0, 0.0, 0.0, 0.0),
        ):
            result = get_cpu_usage()
        # active delta = 100, idle delta = 950, total = 1050
        # usage = (100/1050)*100 ≈ 9.5
        assert 0 < result <= 100

    def test_returns_zero_when_no_proc_stat(self):
        import monitor.services.health as h

        h._prev_cpu_sample = None
        with patch("monitor.services.health._read_cpu_times", return_value=None):
            result = get_cpu_usage()
        assert result == 0.0

    def test_returns_zero_on_zero_total_delta(self):
        import monitor.services.health as h

        sample = (100.0, 0.0, 50.0, 850.0, 0.0, 0.0, 0.0)
        h._prev_cpu_sample = sample
        with patch("monitor.services.health._read_cpu_times", return_value=sample):
            result = get_cpu_usage()
        assert result == 0.0


# ===========================================================================
# get_memory_info
# ===========================================================================


class TestGetMemoryInfo:
    def test_parses_meminfo(self):
        fake_meminfo = "MemTotal:       4096000 kB\nMemAvailable:   2048000 kB\n"
        with patch("builtins.open", mock_open(read_data=fake_meminfo)):
            result = get_memory_info()
        assert result["total_mb"] == 4000
        assert result["free_mb"] == 2000
        assert result["used_mb"] == 2000
        assert 0 < result["percent"] <= 100

    def test_returns_zeros_on_oserror(self):
        with patch("builtins.open", side_effect=OSError("no proc")):
            result = get_memory_info()
        assert result == {"total_mb": 0, "used_mb": 0, "free_mb": 0, "percent": 0.0}

    def test_zero_total_returns_zero_percent(self):
        fake = "MemTotal: 0 kB\nMemAvailable: 0 kB\n"
        with patch("builtins.open", mock_open(read_data=fake)):
            result = get_memory_info()
        assert result["percent"] == 0.0


# ===========================================================================
# get_disk_usage
# ===========================================================================


class TestGetDiskUsage:
    def test_returns_disk_stats(self, tmp_path):
        with patch("monitor.services.health.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(
                total=100 * (1024**3),
                used=40 * (1024**3),
                free=60 * (1024**3),
            )
            result = get_disk_usage(str(tmp_path))
        assert result["total_gb"] == 100.0
        assert result["used_gb"] == 40.0
        assert result["free_gb"] == 60.0
        assert result["percent"] == 40.0

    def test_returns_zeros_on_oserror(self, tmp_path):
        with patch("monitor.services.health.shutil.disk_usage", side_effect=OSError):
            result = get_disk_usage(str(tmp_path))
        assert result == {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0.0}

    def test_zero_total_returns_zero_percent(self, tmp_path):
        with patch("monitor.services.health.shutil.disk_usage") as mock_du:
            mock_du.return_value = MagicMock(total=0, used=0, free=0)
            result = get_disk_usage(str(tmp_path))
        assert result["percent"] == 0.0


# ===========================================================================
# get_uptime
# ===========================================================================


class TestGetUptime:
    def test_parses_uptime_seconds(self, tmp_path):
        uptime_file = tmp_path / "uptime"
        uptime_file.write_text("3661.12 1234.56\n")
        with patch("monitor.services.health.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.return_value = "3661.12 1234.56\n"
            result = get_uptime()
        assert result["seconds"] == 3661
        assert "1h" in result["display"]
        assert "1m" in result["display"]

    def test_displays_days(self):
        seconds = 2 * 86400 + 3 * 3600 + 5 * 60  # 2d 3h 5m
        with patch("monitor.services.health.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.return_value = f"{seconds}.0 0.0\n"
            result = get_uptime()
        assert result["seconds"] == seconds
        assert "2d" in result["display"]

    def test_returns_zeros_on_oserror(self):
        with patch("monitor.services.health.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.side_effect = OSError
            result = get_uptime()
        assert result["seconds"] == 0
        assert "0m" in result["display"]

    def test_omits_zero_hours_from_display(self):
        with patch("monitor.services.health.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.return_value = "300.0 0.0\n"  # 5m
            result = get_uptime()
        assert "h" not in result["display"]
        assert "5m" in result["display"]


# ===========================================================================
# get_network_info
# ===========================================================================


class TestGetNetworkInfo:
    def test_returns_empty_list_when_no_fcntl(self):
        """Non-Linux systems (Windows, macOS) return empty list."""
        with patch.dict("sys.modules", {"fcntl": None}):
            result = get_network_info()
        assert result == []


# ===========================================================================
# get_health_summary
# ===========================================================================


class TestGetHealthSummary:
    def test_healthy_status_no_warnings(self, tmp_path):
        with (
            patch("monitor.services.health.get_cpu_temperature", return_value=55.0),
            patch(
                "monitor.services.health.get_memory_info",
                return_value={
                    "total_mb": 4000,
                    "used_mb": 2000,
                    "free_mb": 2000,
                    "percent": 50.0,
                },
            ),
            patch(
                "monitor.services.health.get_disk_usage",
                return_value={
                    "total_gb": 100,
                    "used_gb": 40,
                    "free_gb": 60,
                    "percent": 40.0,
                },
            ),
            patch(
                "monitor.services.health.get_uptime",
                return_value={"seconds": 3600, "display": "1h 0m"},
            ),
            patch("monitor.services.health.get_cpu_usage", return_value=30.0),
            patch("monitor.services.health.get_network_info", return_value=[]),
        ):
            result = get_health_summary(str(tmp_path))
        assert result["status"] == "healthy"
        assert result["warnings"] == []
        assert result["cpu_temp_c"] == 55.0

    def test_high_cpu_temp_produces_warning(self, tmp_path):
        with (
            patch("monitor.services.health.get_cpu_temperature", return_value=75.0),
            patch(
                "monitor.services.health.get_memory_info",
                return_value={"percent": 50.0},
            ),
            patch(
                "monitor.services.health.get_disk_usage", return_value={"percent": 40.0}
            ),
            patch(
                "monitor.services.health.get_uptime",
                return_value={"seconds": 0, "display": "0m"},
            ),
            patch("monitor.services.health.get_cpu_usage", return_value=10.0),
            patch("monitor.services.health.get_network_info", return_value=[]),
        ):
            result = get_health_summary(str(tmp_path))
        assert result["status"] == "warning"
        assert any("CPU temperature" in w for w in result["warnings"])

    def test_high_disk_usage_produces_warning(self, tmp_path):
        with (
            patch("monitor.services.health.get_cpu_temperature", return_value=40.0),
            patch(
                "monitor.services.health.get_memory_info",
                return_value={"percent": 50.0},
            ),
            patch(
                "monitor.services.health.get_disk_usage", return_value={"percent": 90.0}
            ),
            patch(
                "monitor.services.health.get_uptime",
                return_value={"seconds": 0, "display": "0m"},
            ),
            patch("monitor.services.health.get_cpu_usage", return_value=10.0),
            patch("monitor.services.health.get_network_info", return_value=[]),
        ):
            result = get_health_summary(str(tmp_path))
        assert result["status"] == "warning"
        assert any("Disk" in w for w in result["warnings"])

    def test_high_ram_usage_produces_warning(self, tmp_path):
        with (
            patch("monitor.services.health.get_cpu_temperature", return_value=40.0),
            patch(
                "monitor.services.health.get_memory_info",
                return_value={"percent": 95.0},
            ),
            patch(
                "monitor.services.health.get_disk_usage", return_value={"percent": 40.0}
            ),
            patch(
                "monitor.services.health.get_uptime",
                return_value={"seconds": 0, "display": "0m"},
            ),
            patch("monitor.services.health.get_cpu_usage", return_value=10.0),
            patch("monitor.services.health.get_network_info", return_value=[]),
        ):
            result = get_health_summary(str(tmp_path))
        assert result["status"] == "warning"
        assert any("RAM" in w for w in result["warnings"])

    def test_required_keys_present(self, tmp_path):
        with (
            patch("monitor.services.health.get_cpu_temperature", return_value=40.0),
            patch(
                "monitor.services.health.get_memory_info",
                return_value={"percent": 50.0},
            ),
            patch(
                "monitor.services.health.get_disk_usage", return_value={"percent": 40.0}
            ),
            patch(
                "monitor.services.health.get_uptime",
                return_value={"seconds": 0, "display": "0m"},
            ),
            patch("monitor.services.health.get_cpu_usage", return_value=0.0),
            patch("monitor.services.health.get_network_info", return_value=[]),
        ):
            result = get_health_summary(str(tmp_path))
        for key in (
            "cpu_temp_c",
            "cpu_usage_percent",
            "memory",
            "disk",
            "network",
            "uptime",
            "warnings",
            "status",
        ):
            assert key in result, f"Missing key: {key}"

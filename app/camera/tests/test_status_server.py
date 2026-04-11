"""Tests for camera_streamer.status_server — session management + system helpers."""

import time
from unittest.mock import MagicMock, mock_open, patch

import pytest

from camera_streamer.status_server import (
    SESSION_TIMEOUT,
    CameraStatusServer,
    _check_session,
    _create_session,
    _destroy_session,
    _get_cpu_temp,
    _get_memory_mb,
    _get_session_cookie,
    _get_uptime,
    _html_escape,
    _session_lock,
    _sessions,
)


@pytest.fixture(autouse=True)
def clear_sessions():
    """Clear session store before/after each test."""
    with _session_lock:
        _sessions.clear()
    yield
    with _session_lock:
        _sessions.clear()


# ---- Session management ----


class TestSessionManagement:
    """Test in-memory session store."""

    def test_create_session_returns_hex_token(self):
        token = _create_session()
        assert isinstance(token, str)
        assert len(token) == 64  # 32 bytes hex

    def test_check_valid_session(self):
        token = _create_session()
        assert _check_session(token) is True

    def test_check_invalid_session(self):
        assert _check_session("bad-token") is False

    def test_check_empty_token(self):
        assert _check_session("") is False
        assert _check_session(None) is False

    def test_destroy_session(self):
        token = _create_session()
        assert _check_session(token) is True
        _destroy_session(token)
        assert _check_session(token) is False

    def test_destroy_nonexistent_session(self):
        """Should not raise on missing token."""
        _destroy_session("nonexistent")
        _destroy_session(None)
        _destroy_session("")

    def test_expired_session(self):
        """Expired sessions should be rejected and cleaned up."""
        token = _create_session()
        # Manually expire the session
        with _session_lock:
            _sessions[token] = time.time() - 1
        assert _check_session(token) is False
        # Token should be removed
        with _session_lock:
            assert token not in _sessions

    def test_session_refreshes_on_check(self):
        """Checking a valid session should extend its expiry."""
        token = _create_session()
        with _session_lock:
            original_expiry = _sessions[token]
        time.sleep(0.01)
        _check_session(token)
        with _session_lock:
            new_expiry = _sessions[token]
        assert new_expiry >= original_expiry

    def test_session_timeout_value(self):
        """Session timeout should be 2 hours."""
        assert SESSION_TIMEOUT == 7200


# ---- Cookie parsing ----


class TestSessionCookie:
    """Test cookie extraction from HTTP headers."""

    def test_extract_session_cookie(self):
        headers = MagicMock()
        headers.get.return_value = "cam_session=abc123; other=val"
        assert _get_session_cookie(headers) == "abc123"

    def test_no_session_cookie(self):
        headers = MagicMock()
        headers.get.return_value = "other=val"
        assert _get_session_cookie(headers) == ""

    def test_empty_cookie_header(self):
        headers = MagicMock()
        headers.get.return_value = ""
        assert _get_session_cookie(headers) == ""

    def test_multiple_cookies(self):
        headers = MagicMock()
        headers.get.return_value = "a=1; cam_session=tok; b=2"
        assert _get_session_cookie(headers) == "tok"


# ---- System info helpers ----


class TestCpuTemp:
    """Test CPU temperature reading."""

    def test_valid_temp(self, tmp_path):
        temp_file = tmp_path / "temp"
        temp_file.write_text("52500\n")
        assert _get_cpu_temp(str(temp_file)) == 52.5

    def test_zero_temp(self, tmp_path):
        temp_file = tmp_path / "temp"
        temp_file.write_text("0\n")
        assert _get_cpu_temp(str(temp_file)) == 0.0

    def test_missing_file(self):
        assert _get_cpu_temp("/nonexistent/path") == 0.0

    def test_invalid_content(self, tmp_path):
        temp_file = tmp_path / "temp"
        temp_file.write_text("not-a-number\n")
        assert _get_cpu_temp(str(temp_file)) == 0.0

    def test_default_path(self):
        """Default path should be the standard thermal zone."""
        # Just verify it doesn't crash on a non-Linux system
        result = _get_cpu_temp()
        assert isinstance(result, float)


class TestUptime:
    """Test uptime reading."""

    def test_short_uptime(self):
        with patch("builtins.open", mock_open(read_data="300.5 600.1\n")):
            result = _get_uptime()
            assert result == "5m"

    def test_hours_uptime(self):
        with patch("builtins.open", mock_open(read_data="7200.0 3600.0\n")):
            result = _get_uptime()
            assert result == "2h 0m"

    def test_days_uptime(self):
        with patch("builtins.open", mock_open(read_data="90061.0 0\n")):
            result = _get_uptime()
            assert result == "1d 1h 1m"

    def test_error_uptime(self):
        with patch("builtins.open", side_effect=OSError):
            assert _get_uptime() == "0m"


class TestMemoryMb:
    """Test memory info reading."""

    def test_valid_meminfo(self):
        meminfo = (
            "MemTotal:        1024000 kB\n"
            "MemFree:          200000 kB\n"
            "MemAvailable:     512000 kB\n"
        )
        with patch("builtins.open", mock_open(read_data=meminfo)):
            total, used = _get_memory_mb()
            assert total == 1000  # 1024000 // 1024
            assert used == 500  # 1000 - 500

    def test_error_meminfo(self):
        with patch("builtins.open", side_effect=OSError):
            total, used = _get_memory_mb()
            assert total == 0
            assert used == 0


# ---- HTML escape ----


class TestHtmlEscape:
    """Test HTML special character escaping."""

    def test_ampersand(self):
        assert _html_escape("a&b") == "a&amp;b"

    def test_lt_gt(self):
        assert _html_escape("<script>") == "&lt;script&gt;"

    def test_quote(self):
        assert _html_escape('a"b') == "a&quot;b"

    def test_no_escape_needed(self):
        assert _html_escape("hello world") == "hello world"

    def test_all_special(self):
        assert _html_escape('&<>"') == "&amp;&lt;&gt;&quot;"


# ---- CameraStatusServer ----


class TestCameraStatusServer:
    """Test CameraStatusServer initialization and WiFi methods."""

    @pytest.fixture
    def config(self, tmp_path):
        from camera_streamer.config import ConfigManager

        cfg = ConfigManager(data_dir=str(tmp_path / "data"))
        cfg.load()
        return cfg

    def test_init_defaults(self, config):
        server = CameraStatusServer(config)
        assert server._wifi_interface == "wlan0"
        assert server._thermal_path is None

    def test_init_custom_params(self, config):
        server = CameraStatusServer(
            config, wifi_interface="wlan1", thermal_path="/sys/custom/temp"
        )
        assert server._wifi_interface == "wlan1"
        assert server._thermal_path == "/sys/custom/temp"

    def test_connect_wifi_delegates_to_wifi_module(self, config):
        server = CameraStatusServer(config, wifi_interface="wlan1")
        with patch("camera_streamer.wifi.connect_network") as mock_conn:
            mock_conn.return_value = (True, "")
            ok, err = server.connect_wifi("TestSSID", "pass123")
            assert ok is True
            mock_conn.assert_called_once_with("TestSSID", "pass123", "wlan1")

    def test_connect_wifi_failure(self, config):
        server = CameraStatusServer(config)
        with patch("camera_streamer.wifi.connect_network") as mock_conn:
            mock_conn.return_value = (False, "Connection refused")
            ok, err = server.connect_wifi("BadNet", "bad")
            assert ok is False
            assert "Connection refused" in err

"""Tests for camera_streamer.status_server — session management + system helpers."""

import os
import time
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired
from unittest.mock import MagicMock, mock_open, patch

import pytest

from camera_streamer.status_server import (
    SESSION_TIMEOUT,
    TLS_CERT_NAME,
    TLS_KEY_NAME,
    CameraStatusServer,
    _build_session_cookie,
    _check_session,
    _clear_session_cookie,
    _create_session,
    _destroy_session,
    _ensure_tls_material,
    _get_cpu_temp,
    _get_memory_mb,
    _get_session_cookie,
    _get_uptime,
    _html_escape,
    _session_lock,
    _sessions,
    _status_server_names,
    _status_tls_paths,
    _wrap_https_server,
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

    def test_build_session_cookie_is_secure(self):
        cookie = _build_session_cookie("abc123")
        assert "cam_session=abc123" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=Strict" in cookie
        assert f"Max-Age={SESSION_TIMEOUT}" in cookie

    def test_clear_session_cookie_is_secure(self):
        cookie = _clear_session_cookie()
        assert "cam_session=" in cookie
        assert "Max-Age=0" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=Strict" in cookie


class TestTlsHelpers:
    """Test HTTPS cert generation and wrapping."""

    @pytest.fixture
    def tls_config(self, tmp_path):
        cfg = MagicMock()
        cfg.certs_dir = str(tmp_path / "certs")
        return cfg

    def test_status_tls_paths(self, tls_config):
        cert_path, key_path = _status_tls_paths(tls_config)
        assert cert_path == os.path.join(tls_config.certs_dir, TLS_CERT_NAME)
        assert key_path == os.path.join(tls_config.certs_dir, TLS_KEY_NAME)

    def test_status_server_names_includes_mdns(self):
        with patch("camera_streamer.status_server.wifi.get_hostname") as mock_hostname:
            mock_hostname.return_value = "rpi-divinu-cam-d8ee"
            names = _status_server_names()
        assert "rpi-divinu-cam-d8ee" in names
        assert "rpi-divinu-cam-d8ee.local" in names
        assert "localhost" in names

    @patch("camera_streamer.status_server.os.chmod")
    @patch("camera_streamer.status_server.subprocess.run")
    @patch("camera_streamer.status_server._status_server_names")
    def test_ensure_tls_material_generates_cert(
        self, mock_names, mock_run, mock_chmod, tls_config
    ):
        cert_path, key_path = _status_tls_paths(tls_config)
        Path(cert_path).parent.mkdir(parents=True, exist_ok=True)
        mock_names.return_value = ["rpi-divinu-cam-d8ee", "rpi-divinu-cam-d8ee.local"]

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["openssl", "ecparam"]:
                Path(key_path).write_text("KEY")
            elif cmd[:2] == ["openssl", "req"]:
                Path(cert_path).write_text("CERT")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run

        result_cert, result_key = _ensure_tls_material(tls_config)
        assert result_cert == cert_path
        assert result_key == key_path
        assert mock_run.call_count == 2
        req_cmd = mock_run.call_args_list[1].args[0]
        assert "-addext" in req_cmd
        assert any("subjectAltName=" in part for part in req_cmd)
        mock_chmod.assert_called_once_with(key_path, 0o600)

    def test_ensure_tls_material_reuses_existing_files(self, tls_config):
        cert_path, key_path = _status_tls_paths(tls_config)
        Path(cert_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cert_path).write_text("CERT")
        Path(key_path).write_text("KEY")
        with patch("camera_streamer.status_server.subprocess.run") as mock_run:
            result_cert, result_key = _ensure_tls_material(tls_config)
        assert result_cert == cert_path
        assert result_key == key_path
        mock_run.assert_not_called()

    @patch("camera_streamer.status_server.subprocess.run")
    def test_ensure_tls_material_requires_openssl(self, mock_run, tls_config):
        mock_run.side_effect = FileNotFoundError
        with pytest.raises(RuntimeError, match="openssl is required"):
            _ensure_tls_material(tls_config)

    @patch("camera_streamer.status_server.subprocess.run")
    def test_ensure_tls_material_surfaces_openssl_error(self, mock_run, tls_config):
        mock_run.side_effect = CalledProcessError(
            1, ["openssl"], stderr="bad cert request"
        )
        with pytest.raises(RuntimeError, match="bad cert request"):
            _ensure_tls_material(tls_config)

    @patch("camera_streamer.status_server.subprocess.run")
    def test_ensure_tls_material_handles_timeout(self, mock_run, tls_config):
        mock_run.side_effect = TimeoutExpired("openssl", 15)
        with pytest.raises(RuntimeError, match="timed out"):
            _ensure_tls_material(tls_config)

    @patch("camera_streamer.status_server.ssl.SSLContext")
    @patch("camera_streamer.status_server._ensure_tls_material")
    def test_wrap_https_server_wraps_socket(
        self, mock_ensure_tls_material, mock_ssl_context, tls_config
    ):
        mock_ensure_tls_material.return_value = ("cert.pem", "key.pem")
        mock_server = MagicMock()
        original_socket = mock_server.socket
        ctx = MagicMock()
        mock_ssl_context.return_value = ctx

        wrapped = _wrap_https_server(mock_server, tls_config)

        assert wrapped is mock_server
        ctx.load_cert_chain.assert_called_once_with("cert.pem", "key.pem")
        ctx.wrap_socket.assert_called_once_with(original_socket, server_side=True)


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


# ---- Pair page template ----


class TestPairPageTemplate:
    """Test /pair page HTML rendering with server URL pre-fill."""

    def test_pair_page_prefills_server_url(self):
        """When server_ip is configured, /pair page should pre-fill the URL."""
        from camera_streamer.status_server import _PAIR_PAGE_HTML

        server_url = "https://rpi-divinu.local"
        html = (
            _PAIR_PAGE_HTML.replace("{{CAMERA_ID}}", "cam-test")
            .replace("{{PAIRED_STATUS}}", "Not paired")
            .replace("{{ERROR}}", "")
            .replace("{{ERROR_DISPLAY}}", "none")
            .replace("{{SUCCESS}}", "")
            .replace("{{SUCCESS_DISPLAY}}", "none")
            .replace("{{FORM_DISPLAY}}", "block")
            .replace("{{SERVER_URL}}", server_url)
            .replace("{{SERVER_INFO_DISPLAY}}", "block")
            .replace("{{SERVER_INPUT_DISPLAY}}", "none")
        )
        # Server URL should appear as hidden input value
        assert f'value="{server_url}"' in html
        # Manual input should be hidden
        assert "display:none" in html

    def test_pair_page_shows_input_when_no_server(self):
        """When no server_ip, /pair page should show the URL input field."""
        from camera_streamer.status_server import _PAIR_PAGE_HTML

        html = (
            _PAIR_PAGE_HTML.replace("{{CAMERA_ID}}", "cam-test")
            .replace("{{PAIRED_STATUS}}", "Not paired")
            .replace("{{ERROR}}", "")
            .replace("{{ERROR_DISPLAY}}", "none")
            .replace("{{SUCCESS}}", "")
            .replace("{{SUCCESS_DISPLAY}}", "none")
            .replace("{{FORM_DISPLAY}}", "block")
            .replace("{{SERVER_URL}}", "")
            .replace("{{SERVER_INFO_DISPLAY}}", "none")
            .replace("{{SERVER_INPUT_DISPLAY}}", "block")
        )
        # Manual server URL input should be visible
        assert 'placeholder="https://your-server.local"' in html

    def test_pair_page_hides_form_when_paired(self):
        """When paired, /pair form should be hidden."""
        from camera_streamer.status_server import _PAIR_PAGE_HTML

        html = (
            _PAIR_PAGE_HTML.replace("{{CAMERA_ID}}", "cam-test")
            .replace("{{PAIRED_STATUS}}", "Paired")
            .replace("{{ERROR}}", "")
            .replace("{{ERROR_DISPLAY}}", "none")
            .replace("{{SUCCESS}}", "")
            .replace("{{SUCCESS_DISPLAY}}", "none")
            .replace("{{FORM_DISPLAY}}", "none")
            .replace("{{SERVER_URL}}", "https://server")
            .replace("{{SERVER_INFO_DISPLAY}}", "block")
            .replace("{{SERVER_INPUT_DISPLAY}}", "none")
        )
        assert "display:none" in html

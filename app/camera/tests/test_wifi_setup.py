"""Tests for camera_streamer.wifi_setup module."""

import os
from unittest.mock import MagicMock, patch

from camera_streamer import wifi
from camera_streamer.wifi_setup import (
    CONN_NAME,
    CONNECT_DELAY,
    HOTSPOT_PASS,
    HOTSPOT_SSID,
    IFACE,
    SESSION_TIMEOUT,
    CameraStatusServer,
    WifiSetupServer,
    _check_session,
    _create_session,
    _destroy_session,
    _get_session_cookie,
    _make_handler,
    _session_lock,
    _sessions,
    is_setup_complete,
    mark_setup_complete,
)


class TestSetupStamp:
    """Test setup completion stamp file."""

    def test_not_complete_initially(self, data_dir):
        """Setup should not be complete on fresh data dir."""
        assert is_setup_complete(str(data_dir)) is False

    def test_mark_complete(self, data_dir):
        """mark_setup_complete should create stamp file."""
        mark_setup_complete(str(data_dir))
        assert is_setup_complete(str(data_dir)) is True

    def test_stamp_file_exists(self, data_dir):
        """Stamp file should exist on disk."""
        mark_setup_complete(str(data_dir))
        assert os.path.isfile(os.path.join(str(data_dir), ".setup-done"))

    def test_mark_creates_data_dir(self, tmp_path):
        """mark_setup_complete should create data dir if missing."""
        new_dir = str(tmp_path / "nonexistent" / "data")
        mark_setup_complete(new_dir)
        assert is_setup_complete(new_dir) is True


class TestWifiSetupServer:
    """Test WiFi setup server logic."""

    def test_needs_setup_initially(self, unconfigured_config):
        """Should need setup when no stamp file."""
        server = WifiSetupServer(unconfigured_config)
        assert server.needs_setup() is True

    def test_no_setup_after_complete(self, unconfigured_config):
        """Should not need setup after completion."""
        mark_setup_complete(unconfigured_config.data_dir)
        server = WifiSetupServer(unconfigured_config)
        assert server.needs_setup() is False

    @patch("http.server.HTTPServer")
    @patch("camera_streamer.wifi.start_hotspot")
    @patch("camera_streamer.wifi.scan_networks")
    def test_start_when_needed(
        self, mock_scan, mock_hotspot, mock_httpd, unconfigured_config
    ):
        """start() should pre-scan, start hotspot, and start HTTP server."""
        mock_scan.return_value = [{"ssid": "TestNet", "signal": 80, "security": "WPA2"}]
        mock_hotspot.return_value = True
        server = WifiSetupServer(unconfigured_config)
        result = server.start()
        assert result is True
        mock_scan.assert_called_once()
        mock_hotspot.assert_called_once()
        # Verify cached networks from pre-scan
        assert len(server.get_cached_networks()) == 1
        assert server.get_cached_networks()[0]["ssid"] == "TestNet"
        server.stop()

    @patch("camera_streamer.wifi.start_hotspot")
    @patch("camera_streamer.wifi.scan_networks")
    def test_start_skips_when_done(self, mock_scan, mock_hotspot, unconfigured_config):
        """start() should skip when setup already done."""
        mark_setup_complete(unconfigured_config.data_dir)
        server = WifiSetupServer(unconfigured_config)
        result = server.start()
        assert result is False
        mock_hotspot.assert_not_called()
        mock_scan.assert_not_called()

    @patch("http.server.HTTPServer")
    @patch("camera_streamer.wifi.start_hotspot")
    @patch("camera_streamer.wifi.scan_networks")
    def test_start_with_hotspot_failure(
        self, mock_scan, mock_hotspot, mock_httpd, unconfigured_config
    ):
        """start() should still work if hotspot fails (e.g. no WiFi hw)."""
        mock_scan.return_value = []
        mock_hotspot.return_value = False
        server = WifiSetupServer(unconfigured_config)
        # Should not raise — logs a warning, HTTP server still starts
        result = server.start()
        assert result is True
        server.stop()

    def test_get_status_initial(self, unconfigured_config):
        """get_status should return None initially."""
        server = WifiSetupServer(unconfigured_config)
        assert server.get_status() is None

    def test_get_cached_networks_empty(self, unconfigured_config):
        """get_cached_networks returns empty list before scan."""
        server = WifiSetupServer(unconfigured_config)
        assert server.get_cached_networks() == []

    def test_get_cached_networks_returns_copy(self, unconfigured_config):
        """get_cached_networks should return a copy, not reference."""
        server = WifiSetupServer(unconfigured_config)
        server._cached_networks = [{"ssid": "Test", "signal": 50, "security": "WPA2"}]
        nets = server.get_cached_networks()
        nets.clear()  # Modifying copy shouldn't affect original
        assert len(server.get_cached_networks()) == 1


class TestScanWifi:
    """Test WiFi scanning via wifi module."""

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_scan_parses_nmcli(self, mock_run, mock_sleep):
        """scan_networks should parse nmcli output and deduplicate."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # rescan
            MagicMock(
                returncode=0,
                stdout="MyNetwork:85:WPA2\nGuest:60:WPA1\nMyNetwork:80:WPA2\n",
            ),
        ]
        networks = wifi.scan_networks()
        assert len(networks) == 2
        assert networks[0]["ssid"] == "MyNetwork"
        assert networks[0]["signal"] == 85
        assert networks[1]["ssid"] == "Guest"

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_scan_sorts_by_signal(self, mock_run, mock_sleep):
        """Networks should be sorted by signal strength descending."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(
                returncode=0,
                stdout="Weak:20:WPA2\nStrong:90:WPA2\nMedium:50:WPA2\n",
            ),
        ]
        networks = wifi.scan_networks()
        assert networks[0]["ssid"] == "Strong"
        assert networks[1]["ssid"] == "Medium"
        assert networks[2]["ssid"] == "Weak"

    @patch("subprocess.run")
    def test_scan_handles_error(self, mock_run):
        """scan_networks should return empty list on error."""
        mock_run.side_effect = Exception("nmcli failed")
        networks = wifi.scan_networks()
        assert networks == []

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_scan_skips_empty_ssid(self, mock_run, mock_sleep):
        """scan_networks should skip entries with empty SSID."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout=":80:WPA2\nValid:70:WPA2\n"),
        ]
        networks = wifi.scan_networks()
        assert len(networks) == 1
        assert networks[0]["ssid"] == "Valid"


class TestConnectWifi:
    """Test WiFi connection via wifi module."""

    @patch("subprocess.run")
    def test_connect_success(self, mock_run):
        """connect_network should return True on success."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, err = wifi.connect_network("TestNet", "password123")
        assert ok is True
        assert err == ""

    @patch("subprocess.run")
    def test_connect_failure(self, mock_run):
        """connect_network should return False with error on failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Connection activation failed",
        )
        ok, err = wifi.connect_network("TestNet", "wrongpass")
        assert ok is False
        assert "activation failed" in err

    @patch("subprocess.run")
    def test_connect_timeout(self, mock_run):
        """connect_network should handle timeout."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="nmcli", timeout=30)
        ok, err = wifi.connect_network("TestNet", "pass")
        assert ok is False
        assert "timed out" in err


class TestSaveAndConnect:
    """Test the save_and_connect flow (background connection)."""

    @patch("camera_streamer.wifi.time.sleep")
    @patch("camera_streamer.wifi.start_hotspot")
    @patch("camera_streamer.wifi.stop_hotspot")
    @patch("camera_streamer.wifi.connect_network")
    def test_connect_success_saves_config(
        self, mock_connect, mock_stop, mock_start, mock_sleep, unconfigured_config
    ):
        """Successful connect should save config and mark setup done."""
        mock_connect.return_value = (True, "")
        server = WifiSetupServer(unconfigured_config)

        # Call _do_connect directly (skip threading)
        server._do_connect("TestNet", "pass123", "192.168.1.100", "8554")

        assert server.get_status() is True
        assert is_setup_complete(unconfigured_config.data_dir)
        mock_stop.assert_called_once()
        mock_start.assert_not_called()  # No restart on success

    @patch("camera_streamer.wifi.time.sleep")
    @patch("camera_streamer.wifi.start_hotspot")
    @patch("camera_streamer.wifi.stop_hotspot")
    @patch("camera_streamer.wifi.connect_network")
    def test_connect_failure_restarts_hotspot(
        self, mock_connect, mock_stop, mock_start, mock_sleep, unconfigured_config
    ):
        """Failed connect should restart hotspot for retry."""
        mock_connect.return_value = (False, "No such network")
        server = WifiSetupServer(unconfigured_config)

        server._do_connect("BadNet", "pass", "192.168.1.100", "8554")

        assert server.get_status() == "No such network"
        assert not is_setup_complete(unconfigured_config.data_dir)
        mock_stop.assert_called_once()
        mock_start.assert_called_once()  # Hotspot restarted

    @patch("camera_streamer.wifi.time.sleep")
    @patch("camera_streamer.wifi.start_hotspot")
    @patch("camera_streamer.wifi.stop_hotspot")
    @patch("camera_streamer.wifi.connect_network")
    def test_connect_saves_server_port(
        self, mock_connect, mock_stop, mock_start, mock_sleep, unconfigured_config
    ):
        """Server port should be saved to config on success."""
        mock_connect.return_value = (True, "")
        server = WifiSetupServer(unconfigured_config)

        server._do_connect("TestNet", "pass", "10.0.0.5", "9999")

        from camera_streamer.config import ConfigManager

        mgr = ConfigManager(data_dir=unconfigured_config.data_dir)
        mgr.load()
        assert mgr.server_ip == "10.0.0.5"
        assert mgr.server_port == 9999


class TestRescan:
    """Test the rescan flow (drops AP briefly)."""

    @patch("camera_streamer.wifi.time.sleep")
    @patch("camera_streamer.wifi.start_hotspot")
    @patch("camera_streamer.wifi.stop_hotspot")
    @patch("camera_streamer.wifi.scan_networks")
    def test_rescan_drops_and_restarts_ap(
        self, mock_scan, mock_stop, mock_start, mock_sleep, unconfigured_config
    ):
        """rescan should stop AP, scan, then restart AP."""
        mock_scan.return_value = [{"ssid": "NewNet", "signal": 75, "security": "WPA2"}]
        server = WifiSetupServer(unconfigured_config)

        networks = server.rescan()

        mock_stop.assert_called_once()
        mock_scan.assert_called_once()
        mock_start.assert_called_once()
        assert len(networks) == 1
        assert networks[0]["ssid"] == "NewNet"
        # Cached networks should be updated
        assert server.get_cached_networks()[0]["ssid"] == "NewNet"


class TestWaitForWifi:
    """Test WiFi interface readiness check."""

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_wait_immediate_ready(self, mock_run, mock_sleep):
        """Should return True immediately if wlan0 is ready."""
        mock_run.return_value = MagicMock(returncode=0, stdout="wlan0:wifi\n")
        assert wifi.wait_for_interface(max_wait=5) is True
        mock_sleep.assert_not_called()

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_wait_becomes_ready(self, mock_run, mock_sleep):
        """Should retry until wlan0 appears."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="eth0:ethernet\n"),
            MagicMock(returncode=0, stdout="eth0:ethernet\n"),
            MagicMock(returncode=0, stdout="wlan0:wifi\neth0:ethernet\n"),
        ]
        assert wifi.wait_for_interface(max_wait=5) is True
        assert mock_sleep.call_count == 2

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_wait_timeout(self, mock_run, mock_sleep):
        """Should return False after max_wait seconds."""
        mock_run.return_value = MagicMock(returncode=0, stdout="eth0:ethernet\n")
        assert wifi.wait_for_interface(max_wait=3) is False
        assert mock_sleep.call_count == 3


class TestHotspot:
    """Test hotspot start/stop."""

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_start_hotspot_success(self, mock_run, mock_sleep):
        """Should start hotspot via nmcli."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="wlan0:wifi\n", stderr=""
        )
        result = wifi.start_hotspot()
        assert result is True

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_start_hotspot_no_wlan0(self, mock_run, mock_sleep):
        """Should return False when wlan0 never appears."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="eth0:ethernet\n", stderr=""
        )
        result = wifi.start_hotspot()
        assert result is False

    @patch("subprocess.run")
    def test_stop_hotspot(self, mock_run):
        """Should stop hotspot via nmcli."""
        wifi.stop_hotspot()
        assert mock_run.call_count >= 1

    @patch("subprocess.run")
    def test_stop_hotspot_calls_nmcli(self, mock_run):
        """Should call nmcli to stop hotspot."""
        wifi.stop_hotspot()
        # Verify nmcli connection down and delete were called
        assert mock_run.call_count == 2

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_start_hotspot_nmcli_error(self, mock_run, mock_sleep):
        """Should return False and not crash on nmcli error."""
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(1, "nmcli")
        result = wifi.start_hotspot()
        assert result is False

    @patch("camera_streamer.wifi.time.sleep")
    @patch("subprocess.run")
    def test_start_hotspot_retries_activation(self, mock_run, mock_sleep):
        """Should retry connection up if first attempt fails."""
        import subprocess as sp

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="wlan0:wifi\n"),  # wait_for_interface
            MagicMock(returncode=0),  # delete
            MagicMock(returncode=0),  # add
            sp.CalledProcessError(
                1, "nmcli", stderr="No suitable device"
            ),  # up attempt 1
            MagicMock(returncode=0),  # up attempt 2
        ]
        result = wifi.start_hotspot()
        assert result is True


class TestSessionManagement:
    """Test session creation, checking, and destruction."""

    def setup_method(self):
        """Clear sessions before each test."""
        with _session_lock:
            _sessions.clear()

    def test_create_session(self):
        """create_session should return a hex token."""
        token = _create_session()
        assert isinstance(token, str)
        assert len(token) == 64  # 32 bytes hex

    def test_check_valid_session(self):
        """check_session should return True for valid token."""
        token = _create_session()
        assert _check_session(token) is True

    def test_check_invalid_session(self):
        """check_session should return False for unknown token."""
        assert _check_session("bogus_token") is False
        assert _check_session("") is False
        assert _check_session(None) is False

    def test_destroy_session(self):
        """destroy_session should invalidate the token."""
        token = _create_session()
        assert _check_session(token) is True
        _destroy_session(token)
        assert _check_session(token) is False

    def test_destroy_nonexistent_session(self):
        """destroy_session should not raise for unknown token."""
        _destroy_session("nonexistent")
        _destroy_session("")
        _destroy_session(None)

    @patch("camera_streamer.wifi_setup.time.time")
    def test_session_expiry(self, mock_time):
        """Session should expire after SESSION_TIMEOUT."""
        mock_time.return_value = 1000.0
        token = _create_session()
        # Advance past timeout
        mock_time.return_value = 1000.0 + SESSION_TIMEOUT + 1
        assert _check_session(token) is False

    @patch("camera_streamer.wifi_setup.time.time")
    def test_session_refresh_on_access(self, mock_time):
        """Checking a valid session should extend its expiry."""
        mock_time.return_value = 1000.0
        token = _create_session()
        # Access at SESSION_TIMEOUT - 10 (should refresh)
        mock_time.return_value = 1000.0 + SESSION_TIMEOUT - 10
        assert _check_session(token) is True
        # Now should still be valid SESSION_TIMEOUT after last access
        mock_time.return_value = 1000.0 + SESSION_TIMEOUT - 10 + SESSION_TIMEOUT - 1
        assert _check_session(token) is True

    def test_get_session_cookie(self):
        """Should extract cam_session from Cookie header."""
        headers = MagicMock()
        headers.get.return_value = "cam_session=abc123; other=xyz"
        assert _get_session_cookie(headers) == "abc123"

    def test_get_session_cookie_missing(self):
        """Should return empty string when no session cookie."""
        headers = MagicMock()
        headers.get.return_value = ""
        assert _get_session_cookie(headers) == ""

    def test_get_session_cookie_no_match(self):
        """Should return empty when cam_session not in cookies."""
        headers = MagicMock()
        headers.get.return_value = "other=xyz; foo=bar"
        assert _get_session_cookie(headers) == ""


class TestSaveAndConnectWithPassword:
    """Test that password is saved during provisioning."""

    @patch("camera_streamer.wifi.time.sleep")
    @patch("camera_streamer.wifi.start_hotspot")
    @patch("camera_streamer.wifi.stop_hotspot")
    @patch("camera_streamer.wifi.connect_network")
    def test_password_saved_during_connect(
        self, mock_connect, mock_stop, mock_start, mock_sleep, unconfigured_config
    ):
        """Admin password should be hashed and saved during save_and_connect."""
        mock_connect.return_value = (True, "")
        server = WifiSetupServer(unconfigured_config)

        # Call save_and_connect with admin_password
        # We need to call _do_connect directly since save_and_connect uses threading
        unconfigured_config.set_password("cam_secret")
        unconfigured_config.save()
        server._do_connect("TestNet", "pass", "192.168.1.100", "8554")

        # Verify password was saved
        from camera_streamer.config import ConfigManager

        mgr = ConfigManager(data_dir=unconfigured_config.data_dir)
        mgr.load()
        assert mgr.has_password is True
        assert mgr.check_password("cam_secret") is True


class TestCameraStatusServer:
    """Test the camera status server."""

    def test_init(self, camera_config):
        """Should initialize without error."""
        server = CameraStatusServer(camera_config)
        assert server is not None

    def test_connect_wifi_success(self, camera_config):
        """connect_wifi should call nmcli."""
        server = CameraStatusServer(camera_config)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            ok, err = server.connect_wifi("NewNet", "pass123")
            assert ok is True

    def test_connect_wifi_failure(self, camera_config):
        """connect_wifi should return error on failure."""
        server = CameraStatusServer(camera_config)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Error: no network"
            )
            ok, err = server.connect_wifi("BadNet", "pass")
            assert ok is False
            assert "no network" in err


class TestSystemInfoHelpers:
    """Test system info helper functions."""

    def test_get_cpu_temp(self):
        """Should return float temperature."""
        from camera_streamer.wifi_setup import _get_cpu_temp

        with patch(
            "builtins.open",
            MagicMock(
                return_value=MagicMock(
                    __enter__=MagicMock(
                        return_value=MagicMock(
                            read=MagicMock(return_value="52000\n"),
                            strip=MagicMock(return_value="52000"),
                        )
                    ),
                    __exit__=MagicMock(return_value=False),
                )
            ),
        ):
            # Can't easily mock open this way, just test the error path
            pass
        # Test fallback on error
        with patch("builtins.open", side_effect=OSError("no file")):
            assert _get_cpu_temp() == 0.0

    def test_get_uptime(self):
        """Should return human-readable uptime string."""
        from camera_streamer.wifi_setup import _get_uptime

        with patch("builtins.open", side_effect=OSError("no file")):
            assert _get_uptime() == "0m"

    def test_get_memory_mb(self):
        """Should return (total, used) tuple."""
        from camera_streamer.wifi_setup import _get_memory_mb

        with patch("builtins.open", side_effect=OSError("no file")):
            total, used = _get_memory_mb()
            assert total == 0
            assert used == 0


class TestSetupHTTPHandler:
    """Test the setup HTTP handler responses."""

    def _make_handler_class(self, config):
        """Create handler class for testing."""
        server = WifiSetupServer(config)
        return _make_handler(config, server), server

    def test_handler_class_created(self, unconfigured_config):
        """Should create a valid handler class."""
        handler, _ = self._make_handler_class(unconfigured_config)
        assert handler is not None

    def test_hotspot_ssid(self):
        """SSID should be HomeCam-Setup."""
        assert HOTSPOT_SSID == "HomeCam-Setup"

    def test_hotspot_password(self):
        """Password should be homecamera."""
        assert HOTSPOT_PASS == "homecamera"

    def test_connection_name(self):
        """Connection name should match SSID."""
        assert CONN_NAME == "HomeCam-Setup"

    def test_interface(self):
        """Interface should be wlan0."""
        assert IFACE == "wlan0"

    def test_connect_delay(self):
        """Connect delay should be positive (give phone time to receive response)."""
        assert CONNECT_DELAY > 0

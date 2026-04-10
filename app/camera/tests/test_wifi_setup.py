"""Tests for camera_streamer.wifi_setup module."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock, call
from http.client import HTTPConnection

from camera_streamer.wifi_setup import (
    WifiSetupServer,
    is_setup_complete,
    mark_setup_complete,
    _make_handler,
    HOTSPOT_SSID,
    HOTSPOT_PASS,
    CONN_NAME,
    IFACE,
    CONNECT_DELAY,
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

    @patch("camera_streamer.wifi_setup.WifiSetupServer._start_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._scan_wifi")
    def test_start_when_needed(self, mock_scan, mock_hotspot, unconfigured_config):
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

    @patch("camera_streamer.wifi_setup.WifiSetupServer._start_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._scan_wifi")
    def test_start_skips_when_done(self, mock_scan, mock_hotspot, unconfigured_config):
        """start() should skip when setup already done."""
        mark_setup_complete(unconfigured_config.data_dir)
        server = WifiSetupServer(unconfigured_config)
        result = server.start()
        assert result is False
        mock_hotspot.assert_not_called()
        mock_scan.assert_not_called()

    @patch("camera_streamer.wifi_setup.WifiSetupServer._start_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._scan_wifi")
    def test_start_with_hotspot_failure(self, mock_scan, mock_hotspot, unconfigured_config):
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
    """Test WiFi scanning (private _scan_wifi)."""

    @patch("subprocess.run")
    def test_scan_parses_nmcli(self, mock_run, unconfigured_config):
        """_scan_wifi should parse nmcli output and deduplicate."""
        # First call is rescan (no output needed), second is list
        mock_run.side_effect = [
            MagicMock(returncode=0),  # rescan
            MagicMock(
                returncode=0,
                stdout="MyNetwork:85:WPA2\nGuest:60:WPA1\nMyNetwork:80:WPA2\n",
            ),
        ]
        server = WifiSetupServer(unconfigured_config)
        networks = server._scan_wifi()
        assert len(networks) == 2  # Deduplicated
        assert networks[0]["ssid"] == "MyNetwork"
        assert networks[0]["signal"] == 85
        assert networks[1]["ssid"] == "Guest"

    @patch("subprocess.run")
    def test_scan_sorts_by_signal(self, mock_run, unconfigured_config):
        """Networks should be sorted by signal strength descending."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(
                returncode=0,
                stdout="Weak:20:WPA2\nStrong:90:WPA2\nMedium:50:WPA2\n",
            ),
        ]
        server = WifiSetupServer(unconfigured_config)
        networks = server._scan_wifi()
        assert networks[0]["ssid"] == "Strong"
        assert networks[1]["ssid"] == "Medium"
        assert networks[2]["ssid"] == "Weak"

    @patch("subprocess.run")
    def test_scan_handles_error(self, mock_run, unconfigured_config):
        """_scan_wifi should return empty list on error."""
        mock_run.side_effect = Exception("nmcli failed")
        server = WifiSetupServer(unconfigured_config)
        networks = server._scan_wifi()
        assert networks == []

    @patch("subprocess.run")
    def test_scan_skips_empty_ssid(self, mock_run, unconfigured_config):
        """_scan_wifi should skip entries with empty SSID."""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout=":80:WPA2\nValid:70:WPA2\n"),
        ]
        server = WifiSetupServer(unconfigured_config)
        networks = server._scan_wifi()
        assert len(networks) == 1
        assert networks[0]["ssid"] == "Valid"


class TestConnectWifi:
    """Test WiFi connection (private _connect_wifi)."""

    @patch("subprocess.run")
    def test_connect_success(self, mock_run, unconfigured_config):
        """_connect_wifi should return True on success."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        server = WifiSetupServer(unconfigured_config)
        ok, err = server._connect_wifi("TestNet", "password123")
        assert ok is True
        assert err == ""

    @patch("subprocess.run")
    def test_connect_failure(self, mock_run, unconfigured_config):
        """_connect_wifi should return False with error on failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Connection activation failed",
        )
        server = WifiSetupServer(unconfigured_config)
        ok, err = server._connect_wifi("TestNet", "wrongpass")
        assert ok is False
        assert "activation failed" in err

    @patch("subprocess.run")
    def test_connect_timeout(self, mock_run, unconfigured_config):
        """_connect_wifi should handle timeout."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="nmcli", timeout=30)
        server = WifiSetupServer(unconfigured_config)
        ok, err = server._connect_wifi("TestNet", "pass")
        assert ok is False
        assert "timed out" in err


class TestSaveAndConnect:
    """Test the save_and_connect flow (background connection)."""

    @patch("camera_streamer.wifi_setup.time.sleep")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._start_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._stop_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._connect_wifi")
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

    @patch("camera_streamer.wifi_setup.time.sleep")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._start_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._stop_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._connect_wifi")
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

    @patch("camera_streamer.wifi_setup.time.sleep")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._start_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._stop_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._connect_wifi")
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

    @patch("camera_streamer.wifi_setup.time.sleep")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._start_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._stop_hotspot")
    @patch("camera_streamer.wifi_setup.WifiSetupServer._scan_wifi")
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


class TestHotspot:
    """Test hotspot start/stop."""

    @patch("subprocess.run")
    def test_start_hotspot_success(self, mock_run, unconfigured_config):
        """Should start hotspot via nmcli."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="wlan0\n", stderr=""
        )
        server = WifiSetupServer(unconfigured_config)
        result = server._start_hotspot()
        assert result is True
        assert server._hotspot_active is True

    @patch("subprocess.run")
    def test_start_hotspot_no_wlan0(self, mock_run, unconfigured_config):
        """Should return False when wlan0 missing."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="eth0\n", stderr=""
        )
        server = WifiSetupServer(unconfigured_config)
        result = server._start_hotspot()
        assert result is False

    @patch("subprocess.run")
    def test_stop_hotspot(self, mock_run, unconfigured_config):
        """Should stop hotspot via nmcli."""
        server = WifiSetupServer(unconfigured_config)
        server._hotspot_active = True
        server._stop_hotspot()
        assert server._hotspot_active is False

    @patch("subprocess.run")
    def test_stop_hotspot_noop_when_inactive(self, mock_run, unconfigured_config):
        """Should not call nmcli when hotspot not active."""
        server = WifiSetupServer(unconfigured_config)
        server._hotspot_active = False
        server._stop_hotspot()
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_start_hotspot_nmcli_error(self, mock_run, unconfigured_config):
        """Should return False and not crash on nmcli error."""
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(1, "nmcli")
        server = WifiSetupServer(unconfigured_config)
        result = server._start_hotspot()
        assert result is False
        assert server._hotspot_active is False


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

"""Tests for camera_streamer.wifi_setup module."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from http.client import HTTPConnection

from camera_streamer.wifi_setup import (
    WifiSetupServer,
    is_setup_complete,
    mark_setup_complete,
    _make_handler,
    HOTSPOT_SSID,
    HOTSPOT_PASS,
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
    def test_start_when_needed(self, mock_hotspot, unconfigured_config):
        """start() should activate when setup needed."""
        mock_hotspot.return_value = True
        server = WifiSetupServer(unconfigured_config)
        result = server.start()
        assert result is True
        mock_hotspot.assert_called_once()
        server.stop()

    @patch("camera_streamer.wifi_setup.WifiSetupServer._start_hotspot")
    def test_start_skips_when_done(self, mock_hotspot, unconfigured_config):
        """start() should skip when setup already done."""
        mark_setup_complete(unconfigured_config.data_dir)
        server = WifiSetupServer(unconfigured_config)
        result = server.start()
        assert result is False
        mock_hotspot.assert_not_called()

    def test_complete_setup_saves_config(self, unconfigured_config):
        """complete_setup should save server IP and mark done."""
        server = WifiSetupServer(unconfigured_config)
        server.complete_setup("10.0.0.5", "9999")

        assert is_setup_complete(unconfigured_config.data_dir)
        # Reload and verify
        from camera_streamer.config import ConfigManager
        mgr = ConfigManager(data_dir=unconfigured_config.data_dir)
        mgr.load()
        assert mgr.server_ip == "10.0.0.5"
        assert mgr.server_port == 9999

    @patch("subprocess.run")
    def test_scan_wifi(self, mock_run, unconfigured_config):
        """scan_wifi should parse nmcli output."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="MyNetwork:85:WPA2\nGuest:60:WPA1\nMyNetwork:80:WPA2\n",
        )
        server = WifiSetupServer(unconfigured_config)
        networks = server.scan_wifi()
        assert len(networks) == 2  # Deduplicated
        assert networks[0]["ssid"] == "MyNetwork"
        assert networks[0]["signal"] == 85
        assert networks[1]["ssid"] == "Guest"

    @patch("subprocess.run")
    def test_scan_wifi_handles_error(self, mock_run, unconfigured_config):
        """scan_wifi should return empty list on error."""
        mock_run.side_effect = Exception("nmcli failed")
        server = WifiSetupServer(unconfigured_config)
        networks = server.scan_wifi()
        assert networks == []

    @patch("subprocess.run")
    def test_connect_wifi_success(self, mock_run, unconfigured_config):
        """connect_wifi should return True on success."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        server = WifiSetupServer(unconfigured_config)
        ok, err = server.connect_wifi("TestNet", "password123")
        assert ok is True
        assert err == ""

    @patch("subprocess.run")
    def test_connect_wifi_failure(self, mock_run, unconfigured_config):
        """connect_wifi should return False with error on failure."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Connection activation failed",
        )
        server = WifiSetupServer(unconfigured_config)
        ok, err = server.connect_wifi("TestNet", "wrongpass")
        assert ok is False
        assert "activation failed" in err


class TestHotspot:
    """Test hotspot start/stop."""

    @patch("subprocess.run")
    def test_start_hotspot_success(self, mock_run, unconfigured_config):
        """Should start hotspot via nmcli."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout=f"wlan0\n", stderr=""
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


class TestSetupHTTPHandler:
    """Test the setup HTTP handler responses."""

    def _make_handler_class(self, config):
        """Create handler class for testing."""
        server = WifiSetupServer(config)
        return _make_handler(config, server)

    def test_handler_class_created(self, unconfigured_config):
        """Should create a valid handler class."""
        handler = self._make_handler_class(unconfigured_config)
        assert handler is not None

    def test_hotspot_ssid(self):
        """SSID should be HomeCam-Setup."""
        assert HOTSPOT_SSID == "HomeCam-Setup"

    def test_hotspot_password(self):
        """Password should be homecamera."""
        assert HOTSPOT_PASS == "homecamera"

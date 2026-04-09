"""Tests for camera_streamer.config module."""
import os
import pytest
from unittest.mock import patch, mock_open

from camera_streamer.config import ConfigManager, _get_hardware_serial, DEFAULTS


class TestConfigManager:
    """Test ConfigManager initialization and loading."""

    def test_defaults(self, data_dir):
        """Config should have sane defaults when no file exists."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        assert mgr.server_ip == ""
        assert mgr.server_port == 8554
        assert mgr.width == 1920
        assert mgr.height == 1080
        assert mgr.fps == 25

    def test_load_from_file(self, camera_config):
        """Config should load values from camera.conf."""
        assert camera_config.server_ip == "192.168.1.100"
        assert camera_config.server_port == 8554
        assert camera_config.stream_name == "stream"
        assert camera_config.width == 1920
        assert camera_config.height == 1080
        assert camera_config.fps == 25
        assert camera_config.camera_id == "cam-test001"

    def test_rtsp_url(self, camera_config):
        """Should build correct RTSP URL."""
        # Camera ID is used as stream path for multi-camera support
        assert camera_config.rtsp_url == "rtsp://192.168.1.100:8554/cam-test001"

    def test_rtsp_url_empty_when_no_server(self, unconfigured_config):
        """RTSP URL should be empty when server not configured."""
        assert unconfigured_config.rtsp_url == ""

    def test_is_configured_true(self, camera_config):
        """is_configured should be True when server IP is set."""
        assert camera_config.is_configured is True

    def test_is_configured_false(self, tmp_path):
        """is_configured should be False when no server IP."""
        for d in ["config", "certs", "logs"]:
            (tmp_path / d).mkdir()
        mgr = ConfigManager(data_dir=str(tmp_path))
        mgr.load()
        assert mgr.is_configured is False

    def test_certs_dir(self, camera_config, data_dir):
        """certs_dir should point to data/certs."""
        assert camera_config.certs_dir == os.path.join(str(data_dir), "certs")

    def test_save_and_reload(self, data_dir):
        """Config should save and reload correctly."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        mgr.update(SERVER_IP="10.0.0.1", SERVER_PORT="9999", FPS="30")

        # Reload
        mgr2 = ConfigManager(data_dir=str(data_dir))
        mgr2.load()
        assert mgr2.server_ip == "10.0.0.1"
        assert mgr2.server_port == 9999
        assert mgr2.fps == 30

    def test_update_ignores_unknown_keys(self, camera_config, data_dir):
        """update() should ignore keys not in DEFAULTS."""
        camera_config.update(UNKNOWN_KEY="value")
        mgr2 = ConfigManager(data_dir=str(data_dir))
        mgr2.load()
        # Should still have original values, no UNKNOWN_KEY
        assert mgr2.server_ip == "192.168.1.100"

    def test_parse_ignores_comments(self, data_dir):
        """Parser should skip comment lines."""
        conf = data_dir / "config" / "camera.conf"
        conf.write_text("# This is a comment\nSERVER_IP=1.2.3.4\n")
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        assert mgr.server_ip == "1.2.3.4"

    def test_parse_strips_quotes(self, data_dir):
        """Parser should strip quotes from values."""
        conf = data_dir / "config" / "camera.conf"
        conf.write_text('SERVER_IP="1.2.3.4"\nSTREAM_NAME=\'mystream\'\n')
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        assert mgr.server_ip == "1.2.3.4"
        assert mgr.stream_name == "mystream"

    def test_ensure_config_copies_default(self, data_dir, tmp_path):
        """Should copy default config if no config exists."""
        default_path = tmp_path / "camera.conf.default"
        default_path.write_text("SERVER_IP=default.ip\nFPS=15\n")

        mgr = ConfigManager(data_dir=str(data_dir))
        mgr._default_path = str(default_path)
        mgr.load()
        assert mgr.server_ip == "default.ip"
        assert mgr.fps == 15


class TestHardwareSerial:
    """Test hardware serial detection."""

    def test_reads_serial_from_cpuinfo(self):
        """Should extract serial from /proc/cpuinfo."""
        fake_cpuinfo = "processor\t: 0\nSerial\t\t: 00000000abcdef12\n"
        with patch("builtins.open", mock_open(read_data=fake_cpuinfo)):
            result = _get_hardware_serial()
        assert result == "cam-abcdef12"

    def test_fallback_to_hostname(self):
        """Should fall back to hostname when cpuinfo unavailable."""
        with patch("builtins.open", side_effect=OSError("no file")):
            result = _get_hardware_serial()
        assert result.startswith("cam-")

    def test_camera_id_auto_derived(self, data_dir):
        """Camera ID should be auto-derived when empty."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        # Should have some auto-generated ID
        assert mgr.camera_id.startswith("cam-")

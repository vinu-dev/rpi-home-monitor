"""Tests for camera_streamer.config module."""

import os
from unittest.mock import mock_open, patch

from camera_streamer.config import ConfigManager, _get_hardware_serial


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
        conf.write_text("SERVER_IP=\"1.2.3.4\"\nSTREAM_NAME='mystream'\n")
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

    def test_ensure_config_refuses_when_data_not_mounted(
        self, data_dir, tmp_path, monkeypatch, caplog
    ):
        """Regression (ADR-0008): if /data is not a separate mount,
        ConfigManager must NOT write defaults. A silent /data mount
        failure used to factory-reset the camera into setup/AP mode
        on next boot — see incident on 2026-04-18."""
        import logging

        default_path = tmp_path / "camera.conf.default"
        default_path.write_text("SERVER_IP=default.ip\nFPS=15\n")

        # Turn OFF the test escape hatch so the real guard runs.
        monkeypatch.setenv("CAMERA_SKIP_MOUNT_CHECK", "0")

        mgr = ConfigManager(data_dir=str(data_dir))
        mgr._default_path = str(default_path)

        with caplog.at_level(logging.ERROR):
            mgr.load()

        # No file written (guard triggered).
        assert not (data_dir / "config" / "camera.conf").exists()
        # Error logged so operators / serial console see it.
        assert any(
            "not a separate mounted filesystem" in rec.message for rec in caplog.records
        )
        # Values stayed at defaults (empty SERVER_IP → not configured).
        assert mgr.server_ip == ""
        assert mgr.is_configured is False

    def test_ensure_config_respects_existing_file_even_when_unmounted(
        self, data_dir, monkeypatch
    ):
        """If a config already exists on /data, never touch it — the
        guard only prevents writing fresh defaults."""
        existing = data_dir / "config" / "camera.conf"
        existing.write_text("SERVER_IP=paired.example\nCAMERA_ID=cam-xyz\n")
        monkeypatch.setenv("CAMERA_SKIP_MOUNT_CHECK", "0")

        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()

        # File untouched and values honoured.
        assert existing.read_text().startswith("SERVER_IP=paired.example")
        assert mgr.server_ip == "paired.example"
        assert mgr.camera_id == "cam-xyz"

    def test_is_data_persisted_skip_env(self, data_dir, monkeypatch):
        """CAMERA_SKIP_MOUNT_CHECK=1 forces the guard to pass — used by
        tests and by single-partition dev boards."""
        monkeypatch.setenv("CAMERA_SKIP_MOUNT_CHECK", "1")
        mgr = ConfigManager(data_dir=str(data_dir))
        assert mgr._is_data_persisted() is True

    def test_is_data_persisted_detects_same_device(self, data_dir, monkeypatch):
        """Without the env flag, tmp_path (same device as /) must
        report as NOT persisted."""
        monkeypatch.setenv("CAMERA_SKIP_MOUNT_CHECK", "0")
        mgr = ConfigManager(data_dir=str(data_dir))
        assert mgr._is_data_persisted() is False


class TestMTLSConfig:
    """Test mTLS-related config properties."""

    def test_rtsps_url(self, camera_config):
        """Should build correct RTSPS URL."""
        assert camera_config.rtsps_url == "rtsps://192.168.1.100:8322/cam-test001"

    def test_rtsps_url_empty_when_no_server(self, unconfigured_config):
        """RTSPS URL should be empty when server not configured."""
        assert unconfigured_config.rtsps_url == ""

    def test_has_client_cert_false(self, camera_config):
        """has_client_cert should be False when no cert file."""
        assert camera_config.has_client_cert is False

    def test_has_client_cert_true(self, camera_config, data_dir):
        """has_client_cert should be True when client.crt exists."""
        (data_dir / "certs" / "client.crt").write_text("CERT")
        assert camera_config.has_client_cert is True


class TestServerHttpsUrl:
    """Test server_https_url property used for API registration and pairing."""

    def test_https_url_from_hostname(self, camera_config):
        """Should build HTTPS URL from bare hostname."""
        assert camera_config.server_https_url == "https://192.168.1.100"

    def test_https_url_empty_when_no_server(self, unconfigured_config):
        """Should return empty string when server_ip is not set."""
        assert unconfigured_config.server_https_url == ""

    def test_https_url_passthrough_existing_scheme(self, data_dir):
        """Should pass through URL with existing scheme unchanged."""
        mgr = ConfigManager(data_dir=str(data_dir))
        (data_dir / "config").mkdir(parents=True, exist_ok=True)
        (data_dir / "config" / "camera.conf").write_text(
            'SERVER_IP="https://my-server.local"\n'
            'SERVER_PORT="8554"\n'
            'STREAM_NAME="stream"\n'
            'WIDTH="1920"\nHEIGHT="1080"\nFPS="25"\n'
            'CAMERA_ID="cam-test"\n'
        )
        mgr.load()
        assert mgr.server_https_url == "https://my-server.local"

    def test_https_url_strips_trailing_slash(self, data_dir):
        """Should strip trailing slash from URL with existing scheme."""
        mgr = ConfigManager(data_dir=str(data_dir))
        (data_dir / "config").mkdir(parents=True, exist_ok=True)
        (data_dir / "config" / "camera.conf").write_text(
            'SERVER_IP="https://my-server.local/"\n'
            'SERVER_PORT="8554"\n'
            'STREAM_NAME="stream"\n'
            'WIDTH="1920"\nHEIGHT="1080"\nFPS="25"\n'
            'CAMERA_ID="cam-test"\n'
        )
        mgr.load()
        assert mgr.server_https_url == "https://my-server.local"

    def test_https_url_from_mdns_hostname(self, data_dir):
        """Should build HTTPS URL from .local mDNS hostname."""
        mgr = ConfigManager(data_dir=str(data_dir))
        (data_dir / "config").mkdir(parents=True, exist_ok=True)
        (data_dir / "config" / "camera.conf").write_text(
            'SERVER_IP="rpi-divinu.local"\n'
            'SERVER_PORT="8554"\n'
            'STREAM_NAME="stream"\n'
            'WIDTH="1920"\nHEIGHT="1080"\nFPS="25"\n'
            'CAMERA_ID="cam-test"\n'
        )
        mgr.load()
        assert mgr.server_https_url == "https://rpi-divinu.local"


class TestPasswordManagement:
    """Test camera admin password hashing and verification."""

    def test_default_username(self, data_dir):
        """Default username should be 'admin'."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        assert mgr.admin_username == "admin"

    def test_custom_username(self, data_dir):
        """Should support custom username."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        mgr.update(ADMIN_USERNAME="myuser")
        mgr2 = ConfigManager(data_dir=str(data_dir))
        mgr2.load()
        assert mgr2.admin_username == "myuser"

    def test_no_password_by_default(self, data_dir):
        """has_password should be False when no password is set."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        assert mgr.has_password is False

    def test_set_and_check_password(self, data_dir):
        """Should hash password and verify it correctly."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        mgr.set_password("mysecret123")
        assert mgr.has_password is True
        assert mgr.check_password("mysecret123") is True
        assert mgr.check_password("wrong") is False

    def test_password_persists_after_save_reload(self, data_dir):
        """Password hash should survive save/reload cycle."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        mgr.set_password("persist_test")
        mgr.save()

        mgr2 = ConfigManager(data_dir=str(data_dir))
        mgr2.load()
        assert mgr2.has_password is True
        assert mgr2.check_password("persist_test") is True
        assert mgr2.check_password("wrong") is False

    def test_password_change(self, data_dir):
        """Should be able to change password."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        mgr.set_password("old_pw")
        assert mgr.check_password("old_pw") is True

        mgr.set_password("new_pw")
        assert mgr.check_password("new_pw") is True
        assert mgr.check_password("old_pw") is False

    def test_check_password_empty_hash(self, data_dir):
        """check_password should return False when no password is set."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        assert mgr.check_password("anything") is False

    def test_password_hash_format(self, data_dir):
        """Password hash should be in salt:hash format."""
        mgr = ConfigManager(data_dir=str(data_dir))
        mgr.load()
        mgr.set_password("test")
        raw = mgr.admin_password
        assert ":" in raw
        salt, h = raw.split(":", 1)
        assert len(salt) == 32  # 16 bytes hex
        assert len(h) == 64  # sha256 hex


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

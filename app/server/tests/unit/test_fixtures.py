"""Tests for test fixtures and data directory setup.

Verifies that the test infrastructure itself works correctly.
"""

import json


class TestDataDir:
    """Test that the data_dir fixture creates the right structure."""

    def test_data_dir_exists(self, data_dir):
        assert data_dir.exists()

    def test_config_dir_exists(self, data_dir):
        assert (data_dir / "config").exists()

    def test_recordings_dir_exists(self, data_dir):
        assert (data_dir / "recordings").exists()

    def test_live_dir_exists(self, data_dir):
        assert (data_dir / "live").exists()

    def test_certs_dir_exists(self, data_dir):
        assert (data_dir / "certs").exists()

    def test_logs_dir_exists(self, data_dir):
        assert (data_dir / "logs").exists()


class TestJSONFixtures:
    """Test that JSON fixture files are created correctly."""

    def test_cameras_json_created(self, cameras_json):
        data = json.loads(cameras_json.read_text())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "cam-abc123"

    def test_users_json_created(self, users_json):
        data = json.loads(users_json.read_text())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["username"] == "admin"

    def test_settings_json_created(self, settings_json):
        data = json.loads(settings_json.read_text())
        assert isinstance(data, dict)
        assert data["timezone"] == "Europe/Dublin"
